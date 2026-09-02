"""Every BeForward-specific selector and pattern, in one file.

This is the only genuinely fragile part of the scraper: when the site is
redesigned, this file is what needs editing — nothing else.

Each entry is a *chain* of candidates, tried in order until one matches, so a
partial redesign degrades instead of breaking. The parser also prefers JSON-LD
and label-text matching over CSS wherever possible, because both survive
markup churn that class names do not.
"""

from __future__ import annotations

import re

BASE_URL = "https://www.beforward.jp"

# --- URL construction -------------------------------------------------
# BeForward exposes both a query-string search and SEO-friendly paths. The
# query form is used because it is stable and lets us add filters cleanly.
STOCKLIST_PATH = "/stocklist"

# --- Detail links on a results page ------------------------------------
# Matched against every anchor href on the page. The stock id is group 1.
DETAIL_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/stocklist/[^/]*/detail/(\d+)", re.I),
    re.compile(r"/carview/(?:[\w%-]+/)*?(?:bf|bm)?(\d{5,})", re.I),
    re.compile(r"[?&]stkNo=(\d+)", re.I),
    re.compile(r"/(?:bf|bm)(\d{5,})/?$", re.I),
    re.compile(r"/detail/(\d{5,})", re.I),
)

# Anything matching these is never a vehicle detail link.
DETAIL_URL_EXCLUDE = re.compile(
    r"/(?:about|help|faq|contact|blog|news|login|signup|cart|compare|review"
    r"|terms|privacy|sitemap|banner|campaign)\b",
    re.I,
)

# --- Results page structure --------------------------------------------
LISTING_CARD_SELECTORS: tuple[str, ...] = (
    "div.stocklist-item",
    "li.stocklist-item",
    "div.vehicle-card",
    "div.car-item",
    "article.product",
    "div[class*='stock-list'] li",
    "div[class*='StockList'] > div",
)

TOTAL_RESULTS_SELECTORS: tuple[str, ...] = (
    "span.total-count",
    "div.search-result-count",
    "[class*='totalCount']",
    "[class*='result-count']",
)

PAGINATION_SELECTORS: tuple[str, ...] = (
    "div.pagination a",
    "ul.pagination a",
    "nav[class*='pagination'] a",
    "[class*='pager'] a",
)

NEXT_PAGE_SELECTORS: tuple[str, ...] = (
    "a[rel='next']",
    "link[rel='next']",
    "a.next",
    "[class*='pagination'] a.next",
)

# --- Detail page --------------------------------------------------------
TITLE_SELECTORS: tuple[str, ...] = (
    "h1.vehicle-title",
    "h1[class*='title']",
    "h1",
    "meta[property='og:title']",
)

PRICE_SELECTORS: tuple[str, ...] = (
    "[class*='price'] [class*='total']",
    "span.price-value",
    "div.price-box .price",
    "[class*='fobPrice']",
    "[class*='price']",
    "meta[property='product:price:amount']",
)

# Containers holding label/value spec rows. Parsed generically as pairs
# rather than by position, so column reordering cannot mis-assign fields.
SPEC_TABLE_SELECTORS: tuple[str, ...] = (
    "table.vehicle-spec",
    "table[class*='spec']",
    "table[class*='detail']",
    "div[class*='spec'] table",
    "table",
)

SPEC_LIST_SELECTORS: tuple[str, ...] = (
    "dl[class*='spec']",
    "dl[class*='detail']",
    "ul[class*='spec']",
    "div[class*='spec-item']",
)

IMAGE_SELECTORS: tuple[str, ...] = (
    "div[class*='gallery'] img",
    "div[class*='photo'] img",
    "div[class*='slider'] img",
    "ul[class*='thumb'] img",
    "img[class*='vehicle']",
)

FEATURE_SELECTORS: tuple[str, ...] = (
    "ul[class*='equipment'] li",
    "ul[class*='feature'] li",
    "div[class*='option'] li",
)

# Image URLs matching these are site furniture, not vehicle photos.
IMAGE_EXCLUDE = re.compile(
    r"(?:logo|icon|sprite|flag|banner|placeholder|noimage|no_image|blank|spacer|pixel)",
    re.I,
)

# --- Spec label -> Vehicle field ---------------------------------------
# Keys are normalized labels (lowercased, alphanumerics only). Matching on
# label *text* rather than CSS position is what makes this robust: BeForward
# can restyle the table freely as long as it still says "Mileage".
LABEL_MAP: dict[str, str] = {
    # identity
    "refno": "source_id", "referenceno": "source_id", "stockid": "source_id",
    "stockno": "source_id", "id": "source_id",
    # vehicle
    "make": "make", "maker": "make", "brand": "make", "manufacturer": "make",
    "model": "model", "carname": "model", "modelname": "model",
    "grade": "grade", "trim": "grade", "versionclass": "grade", "version": "grade",
    "modelcode": "model_code", "modelno": "model_code",
    "year": "year", "modelyear": "year", "regyear": "year",
    "registrationyear": "year", "regyearmonth": "year_month",
    "yearmonth": "year_month", "firstregistration": "year_month",
    "manufactureyear": "year", "modelyearmonth": "year_month",
    "bodytype": "body_type", "body": "body_type", "cartype": "body_type",
    "doors": "doors", "door": "doors", "numberofdoors": "doors",
    "seats": "seats", "seat": "seats", "maxcapacity": "seats",
    "passengers": "seats", "seatingcapacity": "seats",
    # mechanical
    "mileage": "mileage_km", "odometer": "mileage_km", "km": "mileage_km",
    "distance": "mileage_km",
    "enginesize": "engine_cc", "engine": "engine_cc", "displacement": "engine_cc",
    "enginedisplacement": "engine_cc", "enginecapacity": "engine_cc", "cc": "engine_cc",
    "enginecode": "engine_code", "engineno": "engine_code", "enginemodel": "engine_code",
    "fuel": "fuel", "fueltype": "fuel", "gas": "fuel",
    "transmission": "transmission", "gearbox": "transmission", "mission": "transmission",
    "drive": "drivetrain", "drivetype": "drivetrain", "drivetrain": "drivetrain",
    "wd": "drivetrain", "drivesystem": "drivetrain",
    "steering": "steering", "handle": "steering", "steeringwheel": "steering",
    "handletype": "steering",
    # commercial
    "location": "location", "carlocation": "location", "country": "location",
    "port": "port", "shippingport": "port", "departureport": "port",
    "availability": "availability", "status": "availability", "stockstatus": "availability",
    # descriptive
    "color": "color", "colour": "color", "exteriorcolor": "color", "bodycolor": "color",
    "condition": "condition", "vehiclecondition": "condition", "grade5": "condition",
    "chassisno": "chassis_no", "chassisnumber": "chassis_no", "chassis": "chassis_no",
    "vin": "chassis_no",
}

_LABEL_CLEAN = re.compile(r"[^a-z0-9]")


def normalize_label(label: str) -> str:
    """'Reg. Year/Month' -> 'regyearmonth', so punctuation drift cannot break mapping."""
    return _LABEL_CLEAN.sub("", label.lower())


def map_label(label: str) -> str | None:
    """Return the Vehicle field a spec label belongs to, or None if unmapped."""
    return LABEL_MAP.get(normalize_label(label))
