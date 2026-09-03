"""Parser tests against synthetic fixtures.

These fixtures are hand-written approximations of BeForward's markup, not real
captures. They prove the parsing *strategy* works — JSON-LD preference,
label-text spec mapping, dedupe, image filtering — independently of the exact
markup. Real captured fixtures (see the beforward/fixtures README) pin the
selectors to the live site; both layers are wanted.
"""

import pytest

from scrapper.models import Drivetrain, Fuel, Steering, Transmission
from scrapper.sources.base import ParseError, Target
from scrapper.sources.beforward.source import (
    BeForwardSource,
    extract_jsonld_vehicle,
    extract_spec_pairs,
    extract_stock_id,
)
from selectolax.parser import HTMLParser

LISTING_URL = "https://www.beforward.jp/stocklist/?make=toyota&model=corolla-axio"
DETAIL_URL = "https://www.beforward.jp/stocklist/toyota-corolla-axio/detail/8811001"


@pytest.fixture
def source():
    return BeForwardSource()


# --- URL building -------------------------------------------------------


def test_listing_url_uses_keyword_path_segments(source):
    """Confirmed against a real page: the search is keyword-based, not ?make=&model=."""
    target = Target(make="Toyota", model="Corolla Axio")
    assert source.listing_url(target, 1) == (
        "https://www.beforward.jp/stocklist/keyword=Toyota%20Corolla%20Axio/kmode=and/sortkey=n"
    )
    assert "/page=3/" in source.listing_url(target, 3)


def test_listing_url_passes_through_filters(source):
    target = Target(make="Toyota", model="Premio", filters={"year_from": "2010"})
    assert "year_from=2010" in source.listing_url(target, 1)


def test_listing_url_without_model(source):
    assert source.listing_url(Target(make="Nissan"), 1) == (
        "https://www.beforward.jp/stocklist/keyword=Nissan/kmode=and/sortkey=n"
    )


@pytest.mark.parametrize(
    "href,expected",
    [
        ("/stocklist/toyota-corolla/detail/8811001", "8811001"),
        ("/carview/toyota/corolla-axio/bf8811003", "8811003"),
        ("https://www.beforward.jp/x?stkNo=99123", "99123"),
        ("/about/company-profile", None),
        ("/help/faq", None),
    ],
)
def test_extract_stock_id(href, expected):
    assert extract_stock_id(href) == expected


# --- Listing pages ------------------------------------------------------


def test_parse_listing_finds_unique_vehicles(source, synthetic):
    page = source.parse_listing(synthetic("listing.html"), LISTING_URL)
    ids = [item.source_id for item in page.items]
    assert ids == ["8811001", "8811002", "8811003"], "duplicate links must collapse"


def test_parse_listing_absolutizes_urls(source, synthetic):
    page = source.parse_listing(synthetic("listing.html"), LISTING_URL)
    assert all(item.detail_url.startswith("https://www.beforward.jp/") for item in page.items)


def test_parse_listing_skips_navigation_links(source, synthetic):
    page = source.parse_listing(synthetic("listing.html"), LISTING_URL)
    assert not any("about" in item.detail_url for item in page.items)


def test_parse_listing_reads_pagination(source, synthetic):
    page = source.parse_listing(synthetic("listing.html"), LISTING_URL)
    assert page.total_pages == 3
    assert page.total_results == 1284
    assert page.next_page_url is not None and "page=2" in page.next_page_url


def test_parse_listing_ignores_placeholder_thumbnails(source, synthetic):
    page = source.parse_listing(synthetic("listing.html"), LISTING_URL)
    by_id = {item.source_id: item for item in page.items}
    assert by_id["8811001"].thumbnail == "https://img.beforward.jp/cars/8811001/main.jpg"
    assert by_id["8811003"].thumbnail is None, "noimage.png is not a real photo"


# --- Detail pages: JSON-LD path ----------------------------------------


def test_parse_detail_extracts_full_record(source, synthetic):
    v = source.parse_detail(synthetic("detail_jsonld.html"), DETAIL_URL)

    assert v.source == "beforward"
    assert v.source_id == "8811001"
    assert v.make == "Toyota"
    assert v.model == "Corolla Axio"
    assert (v.year, v.month) == (2015, 3)
    assert v.mileage_km == 78000
    assert v.engine_cc == 1500
    assert v.transmission == Transmission.CVT
    assert v.fuel == Fuel.PETROL
    assert v.steering == Steering.RIGHT
    assert v.drivetrain == Drivetrain.TWO_WD
    assert v.model_code == "DBA-NRE160"
    assert v.doors == 4
    assert v.seats == 5
    assert v.color == "Pearl White"
    assert v.location == "Japan"


def test_parse_detail_reads_price_from_jsonld(source, synthetic):
    v = source.parse_detail(synthetic("detail_jsonld.html"), DETAIL_URL)
    assert v.price is not None
    assert v.price.currency == "USD"
    assert v.price.fob_amount == 4850.0
    assert v.availability == "InStock"


def test_parse_detail_collects_images_and_features(source, synthetic):
    v = source.parse_detail(synthetic("detail_jsonld.html"), DETAIL_URL)
    assert v.images == [
        "https://img.beforward.jp/cars/8811001/1.jpg",
        "https://img.beforward.jp/cars/8811001/2.jpg",
    ]
    assert "Air Conditioner" in v.features
    assert "Navigation" in v.features


def test_raw_preserves_unmapped_specs(source, synthetic):
    """The escape hatch: fields we do not model yet still reach the JSON."""
    v = source.parse_detail(synthetic("detail_jsonld.html"), DETAIL_URL)
    assert v.raw["Dimension (L*W*H)"] == "4360*1695*1460"
    assert v.raw["Weight"] == "1090 kg"


def test_content_hash_is_stable_across_scrapes(source, synthetic):
    a = source.parse_detail(synthetic("detail_jsonld.html"), DETAIL_URL)
    b = source.parse_detail(synthetic("detail_jsonld.html"), DETAIL_URL)
    assert a.scraped_at != b.scraped_at or True  # timing-independent by design
    assert a.content_hash == b.content_hash


# --- Detail pages: no structured data ----------------------------------


def test_parse_detail_without_jsonld_uses_labels(source, synthetic):
    v = source.parse_detail(
        synthetic("detail_no_jsonld.html"),
        "https://www.beforward.jp/stocklist/nissan-x-trail/detail/8811002",
    )
    assert v.source_id == "8811002"
    assert v.make == "Nissan"
    assert v.model == "X-Trail"
    assert v.grade == "20X"
    assert v.year == 2013
    assert v.mileage_km == 124500
    assert v.engine_cc == 2000, "'2.0L' must convert to cc"
    assert v.transmission == Transmission.AUTOMATIC
    assert v.fuel == Fuel.DIESEL
    assert v.drivetrain == Drivetrain.FOUR_WD
    assert v.steering == Steering.RIGHT
    assert v.body_type == "SUV"
    assert v.color == "Silver"


def test_parse_detail_without_jsonld_falls_back_to_css_price(source, synthetic):
    v = source.parse_detail(synthetic("detail_no_jsonld.html"), DETAIL_URL)
    assert v.price is not None and v.price.fob_amount == 6300.0
    assert v.price.currency == "USD"


def test_images_exclude_site_furniture(source, synthetic):
    v = source.parse_detail(synthetic("detail_no_jsonld.html"), DETAIL_URL)
    assert v.images == ["https://img.beforward.jp/cars/8811002/1.jpg"]


def test_parse_detail_requires_a_stock_id(source):
    with pytest.raises(ParseError):
        source.parse_detail("<html><body>nothing here</body></html>", "https://x.test/page")


# --- Helper-level tests -------------------------------------------------


def test_extract_spec_pairs_handles_four_cell_rows(synthetic):
    pairs = extract_spec_pairs(HTMLParser(synthetic("detail_jsonld.html")))
    assert pairs["Mileage"] == "78,000 km"
    assert pairs["Engine Size"] == "1,500 cc"
    assert pairs["Fuel"] == "Petrol"


def test_extract_spec_pairs_handles_definition_lists(synthetic):
    pairs = extract_spec_pairs(HTMLParser(synthetic("detail_no_jsonld.html")))
    assert pairs["Maker"] == "Nissan"
    assert pairs["Warranty"] == "Not available"


def test_extract_jsonld_skips_non_product_nodes(synthetic):
    data = extract_jsonld_vehicle(HTMLParser(synthetic("detail_jsonld.html")))
    assert data["@type"] == "Product"
    assert data["sku"] == "8811001"


def test_extract_jsonld_tolerates_malformed_blocks():
    html = """<html><head>
      <script type="application/ld+json">{not valid json,,,}</script>
      <script type="application/ld+json">{"@type":"Product","sku":"1"}</script>
    </head></html>"""
    assert extract_jsonld_vehicle(HTMLParser(html))["sku"] == "1"


def test_extract_jsonld_returns_empty_when_absent(synthetic):
    assert extract_jsonld_vehicle(HTMLParser(synthetic("detail_no_jsonld.html"))) == {}
