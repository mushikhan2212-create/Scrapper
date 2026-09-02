"""Polite, resilient HTTP fetching.

One ``Fetcher`` per run owns the connection pool, the concurrency limit, the
retry policy, robots.txt state and the optional on-disk cache. Sources never
construct their own clients — that is what keeps rate limiting global rather
than per-source.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import Settings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 522, 524}


class FetchError(RuntimeError):
    """A request failed in a way worth retrying."""


class BlockedByRobots(RuntimeError):
    """robots.txt disallows this URL and we are configured to respect it."""


@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str
    from_cache: bool = False


class Fetcher:
    """Async HTTP client with retries, throttling, robots and disk caching."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.concurrency)
        self._client: httpx.AsyncClient | None = None
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._robots_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._pace_lock = asyncio.Lock()
        self.stats = {"requests": 0, "cache_hits": 0, "retries": 0, "failures": 0}

    async def __aenter__(self) -> "Fetcher":
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=self.settings.timeout_seconds,
            follow_redirects=self.settings.follow_redirects,
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- caching ---------------------------------------------------------

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.settings.cache_dir / digest[:2] / f"{digest}.html"

    def _read_cache(self, url: str) -> str | None:
        if not self.settings.use_cache:
            return None
        path = self._cache_path(url)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.settings.cache_ttl_seconds:
            return None
        return path.read_text(encoding="utf-8")

    def _write_cache(self, url: str, text: str) -> None:
        if not self.settings.use_cache:
            return
        path = self._cache_path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    # -- robots ----------------------------------------------------------

    async def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        async with self._robots_lock:
            if origin in self._robots:
                return self._robots[origin]
            parser: urllib.robotparser.RobotFileParser | None = None
            try:
                assert self._client is not None
                response = await self._client.get(f"{origin}/robots.txt")
                if response.status_code == 200:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(response.text.splitlines())
                    log.info("Loaded robots.txt for %s", origin)
                else:
                    log.info("No robots.txt at %s (HTTP %s)", origin, response.status_code)
            except httpx.HTTPError as exc:
                # An unreachable robots.txt is not a reason to abort the run.
                log.warning("Could not fetch robots.txt for %s: %s", origin, exc)
            self._robots[origin] = parser
            return parser

    async def allowed(self, url: str) -> bool:
        if not self.settings.respect_robots:
            return True
        parser = await self._robots_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self.settings.user_agent, url)

    async def crawl_delay(self, url: str) -> float | None:
        parser = await self._robots_for(url)
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(self.settings.user_agent)
        except AttributeError:
            return None
        return float(delay) if delay else None

    # -- fetching --------------------------------------------------------

    async def _pace(self) -> None:
        """Space out requests globally, with jitter so we are not metronomic."""
        async with self._pace_lock:
            wait = self.settings.delay_seconds + random.uniform(0, self.settings.jitter_seconds)
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < wait:
                await asyncio.sleep(wait - elapsed)
            self._last_request_at = time.monotonic()

    async def get(self, url: str) -> FetchResult:
        """Fetch a URL as text, honouring cache, robots, throttle and retries."""
        cached = self._read_cache(url)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return FetchResult(url=url, status_code=200, text=cached, from_cache=True)

        if not await self.allowed(url):
            raise BlockedByRobots(f"robots.txt disallows {url}")

        async with self._semaphore:
            result = await self._get_with_retries(url)

        self._write_cache(url, result.text)
        return result

    async def _get_with_retries(self, url: str) -> FetchResult:
        assert self._client is not None, "Fetcher must be used as an async context manager"
        attempt_number = 0
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.settings.max_retries),
            wait=wait_exponential_jitter(initial=2, max=30),
            retry=retry_if_exception_type((FetchError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                attempt_number += 1
                if attempt_number > 1:
                    self.stats["retries"] += 1
                await self._pace()
                self.stats["requests"] += 1
                response = await self._client.get(url)

                if response.status_code in RETRYABLE_STATUS:
                    # Honour Retry-After when the server tells us how long to wait.
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        await asyncio.sleep(min(int(retry_after), 60))
                    raise FetchError(f"HTTP {response.status_code} for {url}")

                if response.status_code >= 400:
                    # 404/403 etc. are not transient; retrying just adds load.
                    self.stats["failures"] += 1
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code} for {url}",
                        request=response.request,
                        response=response,
                    )

                return FetchResult(url=str(response.url), status_code=response.status_code,
                                   text=response.text)

        raise FetchError(f"exhausted retries for {url}")  # pragma: no cover
