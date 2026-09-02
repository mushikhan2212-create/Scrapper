"""Unit tests for string normalization. No network, no fixtures."""

import pytest

from scrapper.models import Drivetrain, Fuel, Steering, Transmission
from scrapper.normalize import (
    clean_text,
    parse_count,
    parse_currency,
    parse_drivetrain,
    parse_engine_cc,
    parse_fuel,
    parse_mileage_km,
    parse_price,
    parse_steering,
    parse_transmission,
    parse_year,
    parse_year_month,
    slugify,
)


@pytest.mark.parametrize(
    "raw,expected",
    [("  Toyota   Corolla ", "Toyota Corolla"), ("\xa0Axio\n", "Axio"),
     ("-", None), ("N/A", None), ("", None), (None, None)],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("90,000 km", 90000), ("90000km", 90000), ("0 km", 0),
     ("55,000 miles", 88514), ("1.2万km", 12000), ("- km", None), (None, None)],
)
def test_parse_mileage_km(raw, expected):
    assert parse_mileage_km(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("1500cc", 1500), ("1,500 cc", 1500), ("1.5L", 1500),
     ("2.0 litre", 2000), ("660cc", 660), ("N/A", None)],
)
def test_parse_engine_cc(raw, expected):
    assert parse_engine_cc(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("US$ 4,500", "USD"), ("$1,200", "USD"), ("¥350,000", "JPY"),
     ("JPY 350000", "JPY"), ("€3.000", "EUR"), ("nothing here", None)],
)
def test_parse_currency(raw, expected):
    assert parse_currency(raw) == expected


def test_parse_price_keeps_raw_string():
    price = parse_price("US$ 4,500")
    assert price is not None
    assert price.currency == "USD"
    assert price.fob_amount == 4500.0
    assert price.raw == "US$ 4,500"


def test_parse_price_without_amount_still_records_the_string():
    price = parse_price("Ask price")
    assert price is not None
    assert price.fob_amount is None
    assert price.raw == "Ask price"


@pytest.mark.parametrize(
    "raw,expected",
    [("2015", 2015), ("2015/3", 2015), ("Mar 2015", 2015), ("no year", None)],
)
def test_parse_year(raw, expected):
    assert parse_year(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("2015/3", (2015, 3)), ("2015-03", (2015, 3)), ("3/2015", (2015, 3)),
     ("Mar 2015", (2015, 3)), ("2015", (2015, None)), ("2015/13", (2015, None))],
)
def test_parse_year_month(raw, expected):
    assert parse_year_month(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("Petrol", Fuel.PETROL), ("Gasoline", Fuel.PETROL), ("Diesel", Fuel.DIESEL),
     ("Hybrid(Petrol)", Fuel.HYBRID), ("Plug-in Hybrid", Fuel.PLUGIN_HYBRID),
     ("Electric", Fuel.ELECTRIC), ("Mystery", Fuel.OTHER)],
)
def test_parse_fuel(raw, expected):
    assert parse_fuel(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("AT", Transmission.AUTOMATIC), ("Automatic", Transmission.AUTOMATIC),
     ("MT", Transmission.MANUAL), ("5MT", Transmission.MANUAL),
     ("Manual", Transmission.MANUAL), ("CVT", Transmission.CVT),
     ("Semi-Automatic", Transmission.SEMI_AUTOMATIC)],
)
def test_parse_transmission(raw, expected):
    assert parse_transmission(raw) == expected


def test_manual_mode_automatic_is_not_manual():
    assert parse_transmission("Automatic (manual mode)") == Transmission.AUTOMATIC


@pytest.mark.parametrize(
    "raw,expected",
    [("2WD", Drivetrain.TWO_WD), ("4WD", Drivetrain.FOUR_WD),
     ("AWD", Drivetrain.AWD), ("FF", Drivetrain.FWD), ("4x4", Drivetrain.FOUR_WD)],
)
def test_parse_drivetrain(raw, expected):
    assert parse_drivetrain(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("Right Hand", Steering.RIGHT), ("RHD", Steering.RIGHT),
     ("Left", Steering.LEFT), ("unknown", None)],
)
def test_parse_steering(raw, expected):
    assert parse_steering(raw) == expected


def test_parse_count_rejects_nonsense():
    assert parse_count("5 doors", maximum=10) == 5
    assert parse_count("500", maximum=10) is None


def test_slugify():
    assert slugify("Corolla Axio") == "corolla-axio"
    assert slugify("Land Cruiser Prado") == "land-cruiser-prado"
