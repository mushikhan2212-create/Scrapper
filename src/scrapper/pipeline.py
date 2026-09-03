"""Orchestration: targets -> listing pages -> detail pages -> JSON on disk.

Deliberately source-agnostic — it talks only to the ``Source`` protocol, so it
runs any provider added later without modification.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

from .config import Settings
from .http import BlockedByRobots, Fetcher
from .models import ListingItem, Vehicle
from .sources.base import ParseError, Source, Target
from .storage import RunWriter, new_run_id

log = logging.getLogger(__name__)


async def collect_listing_items(
    source: Source, fetcher: Fetcher, target: Target
) -> list[ListingItem]:
    """Walk a target's results pages and gather every detail link found."""
    items: list[ListingItem] = []
    seen: set[str] = set()
    page = 1

    while page <= target.max_pages:
        url = source.listing_url(target, page)
        try:
            result = await fetcher.get(url)
        except BlockedByRobots:
            log.warning("robots.txt disallows %s — skipping target", url)
            break
        except Exception as exc:
            log.error("listing fetch failed for %s: %s", url, exc)
            break

        listing = source.parse_listing(result.text, result.url)
        new = [item for item in listing.items if item.source_id not in seen]
        for item in new:
            seen.add(item.source_id)
        items.extend(new)

        log.info(
            "%s page %s: %s listings (%s new, total so far %s)",
            target.key, page, len(listing.items), len(new), len(items),
        )

        # Stop early rather than hammering pages that cannot exist.
        if not listing.items:
            break
        if listing.total_pages is not None and page >= listing.total_pages:
            break
        page += 1

    return items


async def scrape_detail(
    source: Source, fetcher: Fetcher, item: ListingItem
) -> tuple[Vehicle | None, str | None]:
    """Fetch and parse one detail page. Returns (vehicle, error_reason)."""
    try:
        result = await fetcher.get(item.detail_url)
    except BlockedByRobots as exc:
        return None, f"robots: {exc}"
    except Exception as exc:
        return None, f"fetch: {type(exc).__name__}: {exc}"

    try:
        return source.parse_detail(result.text, result.url), None
    except ParseError as exc:
        return None, f"parse: {exc}"
    except Exception as exc:  # A single odd page must never end the run.
        return None, f"parse: {type(exc).__name__}: {exc}"


async def run_scrape(
    source: Source,
    targets: Sequence[Target],
    settings: Settings,
    *,
    limit: int | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    markets: list[str] | None = None,
) -> dict[str, Any]:
    """Scrape every target and write the run to disk. Returns the manifest."""
    run_id = run_id or new_run_id()
    run_dir = settings.run_dir(source.name, run_id)

    async with Fetcher(settings) as fetcher:
        # Phase 1 — discover detail URLs across all targets.
        all_items: list[ListingItem] = []
        seen: set[str] = set()
        for target in targets:
            for item in await collect_listing_items(source, fetcher, target):
                if item.source_id not in seen:
                    seen.add(item.source_id)
                    all_items.append(item)

        if limit is not None:
            all_items = all_items[:limit]

        log.info("discovered %s unique listings", len(all_items))

        if dry_run:
            return {
                "source": source.name,
                "run_id": run_id,
                "dry_run": True,
                "targets": [t.key for t in targets],
                "discovered": len(all_items),
                "sample_urls": [i.detail_url for i in all_items[:10]],
                "http": dict(fetcher.stats),
            }

        # Phase 2 — fetch and parse detail pages, writing as we go.
        with RunWriter(run_dir, source.name, run_id, markets=markets) as writer:
            pending = [i for i in all_items if not writer.already_seen(i.source_id)]
            skipped = len(all_items) - len(pending)
            if skipped:
                log.info("skipping %s listings already present in this run dir", skipped)

            async def handle(item: ListingItem) -> None:
                vehicle, error = await scrape_detail(source, fetcher, item)
                if vehicle is not None:
                    writer.write_vehicle(vehicle)
                else:
                    # Recorded rather than raised: the URL can be retried later
                    # without re-running the whole scrape.
                    writer.write_error(item.detail_url, error or "unknown")
                    log.warning("failed %s: %s", item.detail_url, error)

            # Fetcher's own semaphore enforces the concurrency limit; gather
            # here just keeps the pipeline saturated up to it.
            await asyncio.gather(*(handle(item) for item in pending))

            manifest = writer.finalize(
                targets=[t.key for t in targets], stats=dict(fetcher.stats)
            )

    manifest["discovered"] = len(all_items)
    return manifest
