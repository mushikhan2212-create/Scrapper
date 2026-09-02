"""Source name -> implementation. One line per provider."""

from __future__ import annotations

from .base import Source
from .beforward.source import BeForwardSource

SOURCES: dict[str, type[Source]] = {
    BeForwardSource.name: BeForwardSource,
}


def get_source(name: str) -> Source:
    try:
        return SOURCES[name]()  # type: ignore[operator]
    except KeyError:
        known = ", ".join(sorted(SOURCES)) or "none"
        raise KeyError(f"Unknown source {name!r}. Available: {known}") from None
