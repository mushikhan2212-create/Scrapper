"""Tests for the consumer-facing JSON envelope."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scrapper.export import build_envelope, to_export_record
from scrapper.models import Price, Vehicle
from scrapper.sources.beforward.source import BeForwardSource

FIXTURES = Path(__file__).parents[1] / "src" / "scrapper" / "sources" / "beforward" / "fixtures"
DETAIL = FIXTURES / "detail_corolla.html"
DETAIL_URL = "https://www.beforward.jp/toyota/corolla-axio/ce566767/id/16468163/"

EXPECTED_FIELDS = {
    "externalId", "listingUrl", "make", "model", "variant", "year", "mileage",
    "mileageUnit", "steering", "fuelType", "transmission", "drivetrain", "price",
    "currency", "priceType", "chassisNumber", "portOfLoading", "destinationMarkets",
    "imageUrls", "isAvailable", "lastSeenAtUtc",
}


def _minimal(**overrides) -> Vehicle:
    base = dict(
        source="beforward",
        source_id="CE000001",
        source_url="https://www.beforward.jp/x/y/ce000001/id/1/",
    )
    base.update(overrides)
    return Vehicle(**base).finalize()


def test_record_has_exactly_the_agreed_fields():
    record = to_export_record(_minimal())
    assert set(record) == EXPECTED_FIELDS


def test_envelope_shape():
    envelope = build_envelope([_minimal()], source="beforward", markets=["PK"])
    assert set(envelope) == {"sourceCode", "capturedAtUtc", "vehicles"}
    assert envelope["sourceCode"] == "beforward"
    assert len(envelope["vehicles"]) == 1


def test_timestamps_are_iso_z_without_microseconds():
    captured = datetime(2026, 9, 2, 9, 0, 0, 123456, tzinfo=timezone.utc)
    envelope = build_envelope([], source="beforward", captured_at=captured)
    assert envelope["capturedAtUtc"] == "2026-09-02T09:00:00Z"


def test_destination_markets_are_injected_per_run():
    """BeForward publishes no market data; this comes from config."""
    record = to_export_record(_minimal(), ["PK", "KE", "TZ"])
    assert record["destinationMarkets"] == ["PK", "KE", "TZ"]


def test_destination_markets_default_to_empty_list():
    assert to_export_record(_minimal())["destinationMarkets"] == []


@pytest.mark.parametrize("steering,expected", [("right", "rhd"), ("left", "lhd"), (None, None)])
def test_steering_uses_consumer_vocabulary(steering, expected):
    assert to_export_record(_minimal(steering=steering))["steering"] == expected


def test_whole_prices_render_as_integers():
    record = to_export_record(_minimal(price=Price(currency="USD", fob_amount=6180.0)))
    assert record["price"] == 6180
    assert isinstance(record["price"], int)
    assert record["currency"] == "USD"
    assert record["priceType"] == "FOB"


def test_missing_price_leaves_price_type_unset():
    record = to_export_record(_minimal())
    assert record["price"] is None and record["priceType"] is None


@pytest.mark.parametrize(
    "availability,expected",
    [("InStock", True), ("OutOfStock", False), ("SoldOut", False), (None, None)],
)
def test_availability_maps_to_boolean(availability, expected):
    assert to_export_record(_minimal(availability=availability))["isAvailable"] is expected


def test_unknown_availability_is_null_not_assumed_true():
    """Not reading an availability field is not evidence the car is in stock."""
    assert to_export_record(_minimal(availability="Reserved"))["isAvailable"] is None


def test_mileage_unit_absent_when_mileage_is():
    assert to_export_record(_minimal())["mileageUnit"] is None
    assert to_export_record(_minimal(mileage_km=1000))["mileageUnit"] == "km"


def test_port_falls_back_to_location():
    """BeForward has no port-of-loading field; location is the proxy."""
    assert to_export_record(_minimal(location="NAGOYA"))["portOfLoading"] == "NAGOYA"
    assert to_export_record(_minimal(location="NAGOYA", port="Yokohama"))[
        "portOfLoading"
    ] == "Yokohama"


@pytest.mark.skipif(not DETAIL.exists(), reason="detail fixture not captured")
def test_real_vehicle_exports_correctly():
    """End to end: real HTML in, the consumer's exact record out."""
    html = DETAIL.read_text(encoding="utf-8", errors="replace")
    vehicle = BeForwardSource().parse_detail(html, DETAIL_URL)
    record = to_export_record(vehicle, ["PK", "KE", "TZ"])

    assert record == {
        "externalId": "CE566767",
        "listingUrl": DETAIL_URL,
        "make": "Toyota",
        "model": "Corolla Axio",
        "variant": "HYBRID G",
        "year": 2019,
        "mileage": 108333,
        "mileageUnit": "km",
        "steering": "rhd",
        "fuelType": "hybrid",
        "transmission": "automatic",
        "drivetrain": "2wd",
        "price": 6180,
        "currency": "USD",
        "priceType": "FOB",
        "chassisNumber": "NKE165-7161960",
        "portOfLoading": "NAGOYA",
        "destinationMarkets": ["PK", "KE", "TZ"],
        "imageUrls": [
            "https://image-cdn.beforward.jp/large/202608/16468163/CE566767_689f14.jpg",
            "https://image-cdn.beforward.jp/large/202608/16468163/CE566767_8da5f7.jpg",
            "https://image-cdn.beforward.jp/large/202608/16468163/CE566767_4bd531.jpg",
        ],
        "isAvailable": True,
        "lastSeenAtUtc": record["lastSeenAtUtc"],
    }
