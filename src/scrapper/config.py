"""Runtime settings. Every knob that affects politeness or throughput lives here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw and raw.isdigit() else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass
class Settings:
    """Tunables for a scrape run.

    Defaults are deliberately conservative: this hits someone else's servers.
    """

    # Politeness
    concurrency: int = field(default_factory=lambda: _env_int("SCRAPPER_CONCURRENCY", 4))
    delay_seconds: float = field(default_factory=lambda: _env_float("SCRAPPER_DELAY", 0.75))
    jitter_seconds: float = field(default_factory=lambda: _env_float("SCRAPPER_JITTER", 0.5))
    respect_robots: bool = True

    # HTTP
    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: float = 30.0
    max_retries: int = 4
    follow_redirects: bool = True

    # Paths
    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / ".cache" / "http")

    # Caching responses to disk means re-parsing during selector work costs zero
    # requests. Off by default for real runs so data stays fresh.
    use_cache: bool = False
    cache_ttl_seconds: int = 60 * 60 * 24

    def run_dir(self, source: str, run_id: str) -> Path:
        return self.output_dir / source / run_id


DEFAULT_SETTINGS = Settings()
