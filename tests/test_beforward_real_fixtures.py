"""Regression tests against real captured BeForward HTML.

These pin the parsers to the live site. The values asserted below were read off
the actual captured pages, so when BeForward redesigns, these fail loudly and
name the field that broke — which is the entire point of keeping 1.6 MB of real
HTML in the repo.

Captured 2026-09-03:
  listing_corolla.html — /stocklist/keyword=Toyota%20Corolla%20Axio (25 cars, 11 pages)
  detail_corolla.html  — /toyota/corolla-axio/ce566767/id/16468163/
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrapper.models import Drivetrain, Fuel, Steering, Transmission
from scrapper.sources.base import Target
from scrapper.sources.beforward.source import BeForwardSource, extract_breadcrumbs
from selectolax.parser import HTMLParser

FIXTURES = Path(__file__).parents[1] / "src" / "scrapper" / "sources" / "beforward" / "fixtures"
LISTING = FIXTURES / "listing_corolla.html"
DETAIL = FIXTURES / "detail_corolla.html"

LISTING_URL = "https://www.beforward.jp/stocklist/keyword=Toyota%20Corolla%20Axio"
DETAIL_URL = "https://www.beforward.jp/toyota/corolla-axio/ce566767/id/16468163/"

needs_listing = pytest.mark.skipif(not LISTING.exists(), reason="listing fixture not captured")
needs_detail = pytest.mark.skipif(not DETAIL.exists(), reason="detail fixture not captured")


@pytest.fixture
def source():
    return BeForwardSource()


@pytest.fixture
def listing_html():
    return LISTING.read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def detail_html():
    return DETAIL.read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def vehicle(source, detail_html):
    return source.parse_detail(detail_html, DETAIL_URL)


# --- URL construction ---------------------------------------------------


def test_listing_url_uses_path_segment_keyword_search(source):
    """The real search is /stocklist/keyword=.../kmode=and/..., not ?make=&model=."""
    url = source.listing_url(Target(make="Toyota", model="Corolla Axio"), 1)
    assert url == (
        "https://www.beforward.jp/stocklist/"
        "keyword=Toyota%20Corolla%20Axio/kmode=and/sortkey=n"
    )


def test_listing_url_paginates_by_path_segment(source):
    url = source.listing_url(Target(make="Toyota", model="Corolla Axio"), 2)
    assert "/page=2/" in url
    assert "?page=" not in url


# --- Listing page -------------------------------------------------------


@needs_listing
def test_real_listing_finds_every_vehicle(source, listing_html):
    """Regression: the pre-capture URL patterns matched 0 of these 25 links."""
    page = source.parse_listing(listing_html, LISTING_URL)
    assert len(page.items) == 25


@needs_listing
def test_real_listing_extracts_uppercase_reference_codes(source, listing_html):
    page = source.parse_listing(listing_html, LISTING_URL)
    ids = [item.source_id for item in page.items]
    assert "CE463187" in ids, "reference codes must be normalised to uppercase"
    assert all(len(i) >= 7 and i[:2].isalpha() and i[2:].isdigit() for i in ids)


@needs_listing
def test_real_listing_detects_pagination_depth(source, listing_html):
    """Page numbers live in `/page=11/` path segments, not query params."""
    page = source.parse_listing(listing_html, LISTING_URL)
    assert page.total_pages == 11
    assert page.next_page_url and "page=2" in page.next_page_url


@needs_listing
def test_real_listing_skips_navigation_links(source, listing_html):
    page = source.parse_listing(listing_html, LISTING_URL)
    assert all("/id/" in item.detail_url for item in page.items)


# --- Detail page --------------------------------------------------------


@needs_detail
def test_real_detail_identity(vehicle):
    assert vehicle.source_id == "CE566767"
    assert vehicle.source_url == DETAIL_URL


@needs_detail
def test_real_detail_vehicle_identification(vehicle):
    """make/model/body_type come from three different places on the page."""
    assert vehicle.make == "Toyota", "brand is published as 'TOYOTA'; must be title-cased"
    assert vehicle.model == "Corolla Axio", "model exists only in the breadcrumb trail"
    assert vehicle.body_type == "Sedan", "body type exists only in the breadcrumb trail"
    assert vehicle.grade == "HYBRID G"
    assert vehicle.model_code == "DAA-NKE165"
    assert (vehicle.year, vehicle.month) == (2019, 3)


@needs_detail
def test_real_detail_mechanical_specs(vehicle):
    assert vehicle.mileage_km == 108333
    assert vehicle.engine_cc == 1490
    assert vehicle.engine_code == "1NZ-1LM"
    assert vehicle.fuel == Fuel.HYBRID
    assert vehicle.transmission == Transmission.AUTOMATIC
    assert vehicle.steering == Steering.RIGHT
    assert vehicle.drivetrain == Drivetrain.TWO_WD, "'2wheel drive' must not fall through to other"
    assert vehicle.doors == 4
    assert vehicle.seats == 5


@needs_detail
def test_real_detail_commercial_fields(vehicle):
    assert vehicle.price is not None
    assert vehicle.price.fob_amount == 6180.0
    assert vehicle.price.currency == "USD"
    assert vehicle.availability == "InStock"
    assert vehicle.condition == "used"
    assert vehicle.location == "NAGOYA"


@needs_detail
def test_real_detail_descriptive_fields(vehicle):
    assert vehicle.color == "Pearl"
    assert vehicle.chassis_no == "NKE165-7161960"


@needs_detail
def test_real_detail_images_are_cdn_photos(vehicle):
    assert len(vehicle.images) == 3
    assert all(u.startswith("https://image-cdn.beforward.jp/") for u in vehicle.images)


@needs_detail
def test_real_detail_features_exclude_site_chrome(vehicle):
    """This page has no equipment list; action buttons must not masquerade as one."""
    noise = {"Favorites", "Notify Me", "Save Search", "Easy Inquiry"}
    assert not (set(vehicle.features) & noise)


@needs_detail
def test_real_detail_preserves_unmapped_specs(vehicle):
    """Fields with no typed home still reach the output via raw."""
    assert vehicle.raw["Dimension"] == "4.40×1.69×1.46 m"
    assert vehicle.raw["Weight"] == "1,140 kg"
    assert vehicle.raw["M3"] == "10.857"


@needs_detail
def test_real_breadcrumbs_parse(detail_html):
    crumbs = extract_breadcrumbs(HTMLParser(detail_html))
    assert "TOYOTA" in crumbs and "Sedan" in crumbs and "Corolla Axio" in crumbs
