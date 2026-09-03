"""String -> typed conversions.

Every messy parse lives here rather than in a source module, so it is unit
tested without touching the network and reused by every provider added later.

All functions are total: unparseable input returns ``None`` rather than raising.
Losing one field must never lose a whole record.
"""

from __future__ import annotations

import re
from typing import Any

from .models import Drivetrain, Fuel, Price, Steering, Transmission

_WS = re.compile(r"\s+")
_NUM = re.compile(r"-?[\d, \s]*\d(?:\.\d+)?")

MILES_TO_KM = 1.609344

# Currency symbols/codes seen on export car sites, longest-first so that
# "US$" wins over "$".
_CURRENCY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("US$", "USD"), ("USD", "USD"), ("JPY", "JPY"), ("¥", "JPY"), ("￥", "JPY"),
    ("EUR", "EUR"), ("€", "EUR"), ("GBP", "GBP"), ("£", "GBP"),
    ("AUD", "AUD"), ("CAD", "CAD"), ("NZD", "NZD"), ("ZAR", "ZAR"),
    ("KES", "KES"), ("TZS", "TZS"), ("UGX", "UGX"), ("ZMW", "ZMW"),
    ("$", "USD"),
)


def clean_text(value: Any) -> str | None:
    """Collapse whitespace and strip. Returns None for empty/placeholder input."""
    if value is None:
        return None
    text = _WS.sub(" ", str(value).replace("\xa0", " ")).strip()
    if not text or text in {"-", "--", "N/A", "n/a", "NA", "?", "—"}:
        return None
    return text


def parse_number(value: Any) -> float | None:
    """Pull the first number out of a string, tolerating thousands separators."""
    text = clean_text(value)
    if text is None:
        return None
    match = _NUM.search(text)
    if not match:
        return None
    cleaned = re.sub(r"[, \s]", "", match.group(0))
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    number = parse_number(value)
    return int(number) if number is not None else None


def parse_mileage_km(value: Any) -> int | None:
    """Parse odometer readings, converting miles to km.

    Handles '90,000 km', '90000km', '55,000 miles', '1.2万km' (Japanese man = 10k).
    """
    text = clean_text(value)
    if text is None:
        return None
    lowered = text.lower()

    # Japanese listings sometimes use 万 (10,000).
    man = re.search(r"([\d.,]+)\s*万", text)
    if man:
        base = parse_number(man.group(1))
        return int(base * 10_000) if base is not None else None

    number = parse_number(text)
    if number is None:
        return None
    if re.search(r"\b(miles?|mi)\b", lowered):
        return int(round(number * MILES_TO_KM))
    return int(number)


def parse_engine_cc(value: Any) -> int | None:
    """Parse displacement to cc. Accepts '1500cc', '1,500 cc', '1.5L', '2.0 litre'."""
    text = clean_text(value)
    if text is None:
        return None
    lowered = text.lower()

    litres = re.search(r"([\d.]+)\s*(?:l\b|liters?|litres?)", lowered)
    if litres:
        number = parse_number(litres.group(1))
        if number is not None:
            return int(round(number * 1000))

    number = parse_number(lowered)
    if number is None:
        return None
    # A bare small number is almost certainly litres, not cc.
    if number < 100:
        return int(round(number * 1000))
    return int(number)


def parse_currency(value: Any) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    upper = text.upper()
    for token, code in _CURRENCY_PATTERNS:
        if token.upper() in upper:
            return code
    return None


def parse_price(value: Any, *, currency: str | None = None) -> Price | None:
    """Parse a displayed price into a Price, preserving the original string."""
    text = clean_text(value)
    if text is None:
        return None
    amount = parse_number(text)
    if amount is None:
        return Price(currency=currency or parse_currency(text), raw=text)
    return Price(currency=currency or parse_currency(text), fob_amount=amount, raw=text)


def parse_year(value: Any) -> int | None:
    """Extract a plausible 4-digit model year."""
    text = clean_text(value)
    if text is None:
        return None
    for match in re.finditer(r"(19|20)\d{2}", text):
        year = int(match.group(0))
        if 1900 <= year <= 2100:
            return year
    return None


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_year_month(value: Any) -> tuple[int | None, int | None]:
    """Parse registration dates like '2015/3', '2015-03', 'Mar 2015', '2015'."""
    text = clean_text(value)
    if text is None:
        return None, None

    year = parse_year(text)

    # Numeric forms: 2015/3, 2015-03, 3/2015
    numeric = re.search(r"(19|20)\d{2}\s*[/\-.]\s*(\d{1,2})", text)
    if numeric:
        month = int(numeric.group(2))
        return year, month if 1 <= month <= 12 else None

    reversed_numeric = re.search(r"\b(\d{1,2})\s*[/\-.]\s*((?:19|20)\d{2})", text)
    if reversed_numeric:
        month = int(reversed_numeric.group(1))
        return year, month if 1 <= month <= 12 else None

    name = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", text.lower())
    if name:
        return year, _MONTHS[name.group(1)]

    return year, None


def parse_fuel(value: Any) -> Fuel | None:
    text = clean_text(value)
    if text is None:
        return None
    t = text.lower()
    if "plug" in t and "hybrid" in t:
        return Fuel.PLUGIN_HYBRID
    if "hybrid" in t:
        return Fuel.HYBRID
    if "electric" in t or t in {"ev", "bev"}:
        return Fuel.ELECTRIC
    if "diesel" in t or "gas oil" in t:
        return Fuel.DIESEL
    if "petrol" in t or "gasoline" in t or t == "gas":
        return Fuel.PETROL
    if "lpg" in t or "liquefied" in t:
        return Fuel.LPG
    if "cng" in t or "natural gas" in t:
        return Fuel.CNG
    return Fuel.OTHER


def parse_transmission(value: Any) -> Transmission | None:
    text = clean_text(value)
    if text is None:
        return None
    t = text.lower()
    if "cvt" in t:
        return Transmission.CVT
    if "semi" in t or "amt" in t or "tiptronic" in t or "dct" in t:
        return Transmission.SEMI_AUTOMATIC
    # Check manual before automatic: "manual mode automatic" is an automatic,
    # but a bare "MT"/"5MT"/"manual" is not.
    if re.search(r"\bm/?t\b|\bmanual\b|\b\dmt\b", t) and "automatic" not in t:
        return Transmission.MANUAL
    if re.search(r"\ba/?t\b|automatic|\b\dat\b", t):
        return Transmission.AUTOMATIC
    return Transmission.OTHER


def parse_drivetrain(value: Any) -> Drivetrain | None:
    text = clean_text(value)
    if text is None:
        return None
    t = text.lower().replace(" ", "")
    if "awd" in t or "allwheel" in t:
        return Drivetrain.AWD
    # BeForward writes "2wheel drive" / "4wheel drive" and never distinguishes
    # front from rear, so those are the most specific answers available.
    if "4wd" in t or "4x4" in t or "fourwheel" in t or "4wheel" in t:
        return Drivetrain.FOUR_WD
    if "fwd" in t or "frontwheel" in t or "ff" == t:
        return Drivetrain.FWD
    if "rwd" in t or "rearwheel" in t or "fr" == t:
        return Drivetrain.RWD
    if "2wd" in t or "4x2" in t or "2wheel" in t or "twowheel" in t:
        return Drivetrain.TWO_WD
    return Drivetrain.OTHER


def parse_steering(value: Any) -> Steering | None:
    text = clean_text(value)
    if text is None:
        return None
    t = text.lower()
    if "right" in t or t in {"rhd", "r"}:
        return Steering.RIGHT
    if "left" in t or t in {"lhd", "l"}:
        return Steering.LEFT
    return None


def parse_count(value: Any, *, maximum: int) -> int | None:
    """Parse small counts such as doors or seats, rejecting nonsense."""
    number = parse_int(value)
    if number is None or number < 0 or number > maximum:
        return None
    return number


def slugify(value: str) -> str:
    """Lowercase hyphenated slug, used for URL building and file names."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")
