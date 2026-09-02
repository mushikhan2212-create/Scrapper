"""Regression tests against *real* captured BeForward HTML.

These skip until someone commits captures (see the README in the beforward
fixtures directory). The moment a real page is committed, these become the
tests that pin the selectors to the live site — and they fail loudly when
BeForward redesigns, which is the whole point.

Naming convention picked up automatically:
    listing*.html  -> parsed as a search-results page
    detail*.html   -> parsed as a vehicle detail page
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrapper.sources.beforward.source import BeForwardSource

FIXTURES = Path(__file__).parents[1] / "src" / "scrapper" / "sources" / "beforward" / "fixtures"

LISTING_FIXTURES = sorted(FIXTURES.glob("listing*.html"))
DETAIL_FIXTURES = sorted(FIXTURES.glob("detail*.html"))

needs_listing = pytest.mark.skipif(
    not LISTING_FIXTURES,
    reason="no real listing fixture captured yet — run: scrapper capture <url> -n listing_x",
)
needs_detail = pytest.mark.skipif(
    not DETAIL_FIXTURES,
    reason="no real detail fixture captured yet — run: scrapper capture <url> -n detail_x",
)


@pytest.fixture
def source():
    return BeForwardSource()


@needs_listing
@pytest.mark.parametrize("path", LISTING_FIXTURES, ids=lambda p: p.name)
def test_real_listing_yields_detail_links(source, path):
    page = source.parse_listing(
        path.read_text(encoding="utf-8"), "https://www.beforward.jp/stocklist/"
    )
    assert page.items, (
        f"{path.name}: no detail links found. The URL patterns in "
        "sources/beforward/selectors.py:DETAIL_URL_PATTERNS need updating."
    )
    assert all(i.source_id.isdigit() for i in page.items)
    assert all(i.detail_url.startswith("http") for i in page.items)


@needs_listing
@pytest.mark.parametrize("path", LISTING_FIXTURES, ids=lambda p: p.name)
def test_real_listing_reports_pagination(source, path):
    page = source.parse_listing(
        path.read_text(encoding="utf-8"), "https://www.beforward.jp/stocklist/"
    )
    assert page.total_pages or page.next_page_url, (
        f"{path.name}: no pagination detected — PAGINATION_SELECTORS may need updating. "
        "Without this the scraper only ever reads page 1."
    )


@needs_detail
@pytest.mark.parametrize("path", DETAIL_FIXTURES, ids=lambda p: p.name)
def test_real_detail_extracts_core_fields(source, path):
    """The fields that make a record worth importing at all."""
    vehicle = source.parse_detail(
        path.read_text(encoding="utf-8"),
        "https://www.beforward.jp/stocklist/detail/0000000",
    )

    missing = [
        field
        for field in ("make", "model", "year", "mileage_km", "price")
        if getattr(vehicle, field) is None
    ]
    assert not missing, (
        f"{path.name}: core fields not extracted: {missing}. "
        f"Captured spec labels were: {sorted(vehicle.raw)}"
    )


@needs_detail
@pytest.mark.parametrize("path", DETAIL_FIXTURES, ids=lambda p: p.name)
def test_real_detail_captures_raw_specs_and_images(source, path):
    vehicle = source.parse_detail(
        path.read_text(encoding="utf-8"),
        "https://www.beforward.jp/stocklist/detail/0000000",
    )
    assert vehicle.raw, f"{path.name}: no spec pairs captured — SPEC_TABLE_SELECTORS need work"
    assert vehicle.images, f"{path.name}: no images captured — IMAGE_SELECTORS need work"
