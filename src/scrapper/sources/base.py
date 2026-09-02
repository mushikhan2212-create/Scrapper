"""The contract every provider implements.

Adding a second provider in phase 2 means writing one class against this
protocol and registering it. The pipeline, schema, storage, HTTP layer and CLI
do not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol, runtime_checkable

from .. models import ListingPage, Vehicle


@dataclass
class Target:
    """One make/model to scrape, as declared in targets.yml."""

    make: str
    model: str | None = None
    max_pages: int = 1
    filters: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.make}:{self.model}" if self.model else self.make

    @classmethod
    def parse(cls, spec: str, *, max_pages: int = 1) -> "Target":
        """Build a Target from a 'make:model' CLI string."""
        make, _, model = spec.partition(":")
        return cls(make=make.strip(), model=model.strip() or None, max_pages=max_pages)


@runtime_checkable
class Source(Protocol):
    """A car listing provider."""

    name: str

    def listing_url(self, target: Target, page: int) -> str:
        """URL of the Nth search-results page for this target (1-indexed)."""
        ...

    def parse_listing(self, html: str, url: str) -> ListingPage:
        """Extract detail-page links and pagination info from a results page."""
        ...

    def parse_detail(self, html: str, url: str) -> Vehicle:
        """Extract one fully-populated Vehicle from a detail page."""
        ...


class ParseError(ValueError):
    """A page could not be parsed into the expected shape."""
