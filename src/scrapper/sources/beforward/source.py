"""BeForward listing and detail parsing.

Two deliberate robustness choices, because a scraper's real enemy is the site
redesign rather than the first successful run:

1. **JSON-LD first.** If the page ships structured data we trust it over any
   CSS selector, because it is a published contract rather than presentation.
2. **Label-text spec mapping.** The spec table is read as label/value *pairs*
   and mapped by what the label says ("Mileage"), never by column position or
   class name. Restyling the table cannot mis-assign a field, and every pair is
   also kept verbatim in ``Vehicle.raw`` so nothing on the page is ever lost.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable
from urllib.parse import quote, urljoin

from selectolax.parser import HTMLParser, Node

from ...models import ListingItem, ListingPage, Price, Vehicle
from ...normalize import (
    clean_text,
    parse_count,
    parse_drivetrain,
    parse_engine_cc,
    parse_fuel,
    parse_int,
    parse_mileage_km,
    parse_price,
    parse_steering,
    parse_transmission,
    parse_year,
    parse_year_month,
    slugify,
)
from ..base import ParseError, Target
from . import selectors as S

log = logging.getLogger(__name__)


class BeForwardSource:
    """Scraper for beforward.jp."""

    name = "beforward"
    base_url = S.BASE_URL

    # -- URLs ------------------------------------------------------------

    def listing_url(self, target: Target, page: int = 1) -> str:
        """Build a search-results URL for a target.

        BeForward's search takes PATH SEGMENTS, not a query string:

            /stocklist/keyword=Toyota%20Corolla%20Axio/kmode=and/page=2/sortkey=n

        Keyword search is used in preference to /stocklist/make=1/model=132/,
        which would require maintaining a map of BeForward's internal numeric
        make and model ids.
        """
        keyword = " ".join(part for part in (target.make, target.model) if part)
        segments = [f"keyword={quote(keyword)}", f"kmode={S.KEYWORD_MODE}"]
        segments.extend(f"{key}={quote(str(value))}" for key, value in target.filters.items())
        if page > 1:
            segments.append(f"page={page}")
        segments.append(f"sortkey={S.SORT_KEY}")
        return f"{self.base_url}{S.STOCKLIST_PATH}/" + "/".join(segments)

    # -- Listing pages ---------------------------------------------------

    def parse_listing(self, html: str, url: str) -> ListingPage:
        """Collect detail links and pagination info from a results page.

        Works off every anchor on the page rather than a card selector, so a
        restyled results grid still yields links.
        """
        tree = HTMLParser(html)
        items: list[ListingItem] = []
        seen: set[str] = set()

        for anchor in tree.css("a[href]"):
            href = anchor.attributes.get("href") or ""
            if not href or S.DETAIL_URL_EXCLUDE.search(href):
                continue
            stock_id = extract_stock_id(href)
            if stock_id is None or stock_id in seen:
                continue
            seen.add(stock_id)
            items.append(
                ListingItem(
                    source_id=stock_id,
                    detail_url=urljoin(url, href),
                    title=clean_text(anchor.text()) or clean_text(anchor.attributes.get("title")),
                    thumbnail=_first_image_in(anchor, url),
                )
            )

        return ListingPage(
            items=items,
            total_pages=self._total_pages(tree),
            total_results=self._total_results(tree),
            next_page_url=self._next_page(tree, url),
        )

    def _total_pages(self, tree: HTMLParser) -> int | None:
        """Highest page number linked anywhere on the page.

        Scans every anchor rather than a pagination container: on the real
        markup the CSS selectors matched nothing, while the page numbers were
        plainly present in hrefs as `/page=11/` path segments.
        """
        best = 0
        for node in tree.css("a[href]"):
            match = S.PAGE_NUMBER_RE.search(node.attributes.get("href") or "")
            if match:
                best = max(best, int(match.group(1)))
        return best or None

    def _total_results(self, tree: HTMLParser) -> int | None:
        for selector in S.TOTAL_RESULTS_SELECTORS:
            node = tree.css_first(selector)
            if node is not None:
                value = parse_int(node.text())
                if value:
                    return value
        return None

    def _next_page(self, tree: HTMLParser, url: str) -> str | None:
        for selector in S.NEXT_PAGE_SELECTORS:
            node = tree.css_first(selector)
            if node is not None:
                href = node.attributes.get("href")
                if href:
                    return urljoin(url, href)
        return None

    # -- Detail pages ----------------------------------------------------

    def parse_detail(self, html: str, url: str) -> Vehicle:
        """Build a fully-populated Vehicle from a detail page."""
        tree = HTMLParser(html)
        jsonld = extract_jsonld_vehicle(tree)
        breadcrumbs = extract_breadcrumbs(tree)
        specs = extract_spec_pairs(tree)

        source_id = (
            extract_stock_id(url)
            or _spec_value(specs, "source_id")
            or clean_text(jsonld.get("sku"))
            or clean_text(jsonld.get("productID"))
        )
        if not source_id:
            raise ParseError(f"could not determine stock id for {url}")

        vehicle = Vehicle(
            source=self.name,
            source_id=str(source_id),
            source_url=url,
            raw={k: v for k, v in specs.items()},
        )

        self._apply_specs(vehicle, specs)
        self._apply_jsonld(vehicle, jsonld)
        self._apply_breadcrumbs(vehicle, breadcrumbs)
        self._apply_fallbacks(vehicle, tree, url)

        return vehicle.finalize()

    def _apply_breadcrumbs(self, vehicle: Vehicle, crumbs: list[str]) -> None:
        """Fill model, body type and year from the breadcrumb trail.

        These three appear nowhere else: the Product JSON-LD has only a combined
        `name`, and the spec table has no model or body-type row at all. The
        trail runs HOME > TOYOTA > Sedan > Corolla Axio > 2019 > <full title>,
        so entries are matched by shape rather than by fixed position.
        """
        if not crumbs:
            return

        # Skip the first (HOME) and last (the full listing title) entries.
        candidates = [c for c in crumbs[1:-1] if c and c.upper() != "HOME"]

        for crumb in candidates:
            if vehicle.year is None and re.fullmatch(r"(19|20)\d{2}", crumb):
                vehicle.year = int(crumb)
                continue
            if vehicle.body_type is None and crumb.lower() in BODY_TYPES:
                vehicle.body_type = crumb
                continue
            # The make appears in caps; whatever is left is the model.
            if vehicle.make and crumb.upper() == vehicle.make.upper():
                continue
            if vehicle.model is None and not crumb.isdigit():
                vehicle.model = crumb

    def _apply_specs(self, vehicle: Vehicle, specs: dict[str, str]) -> None:
        """Map spec labels onto typed fields. The primary data path."""
        for label, value in specs.items():
            field = S.map_label(label)
            if field is None:
                continue  # Unmapped labels still live on in vehicle.raw.

            if field == "year_month":
                year, month = parse_year_month(value)
                vehicle.year = vehicle.year or year
                vehicle.month = vehicle.month or month
                continue

            parsed = _parse_field(field, value)
            if parsed is not None and getattr(vehicle, field, None) in (None, [], {}):
                setattr(vehicle, field, parsed)

    def _apply_jsonld(self, vehicle: Vehicle, data: dict[str, Any]) -> None:
        """Fill gaps from structured data, which is more reliable than markup."""
        if not data:
            return

        if vehicle.make is None:
            # BeForward publishes the brand in caps ("TOYOTA"); title-case it so
            # aggregated output across sources is consistent.
            brand = clean_text(_nested(data, "brand", "name") or data.get("brand"))
            vehicle.make = brand.title() if brand and brand.isupper() else brand
        if vehicle.model is None:
            vehicle.model = clean_text(_nested(data, "model", "name") or data.get("model"))
        if vehicle.year is None:
            vehicle.year = parse_year(data.get("vehicleModelDate") or data.get("modelDate"))
        if vehicle.mileage_km is None:
            vehicle.mileage_km = parse_mileage_km(
                _nested(data, "mileageFromOdometer", "value") or data.get("mileageFromOdometer")
            )
        if vehicle.engine_cc is None:
            vehicle.engine_cc = parse_engine_cc(
                _nested(data, "vehicleEngine", "engineDisplacement", "value")
                or _nested(data, "vehicleEngine", "engineDisplacement")
            )
        if vehicle.fuel is None:
            vehicle.fuel = parse_fuel(data.get("fuelType"))
        if vehicle.transmission is None:
            vehicle.transmission = parse_transmission(data.get("vehicleTransmission"))
        if vehicle.color is None:
            vehicle.color = clean_text(data.get("color"))
        if vehicle.body_type is None:
            vehicle.body_type = clean_text(data.get("bodyType"))
        if vehicle.seats is None:
            vehicle.seats = parse_count(data.get("seatingCapacity"), maximum=100)
        if vehicle.doors is None:
            vehicle.doors = parse_count(data.get("numberOfDoors"), maximum=10)

        offers = data.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict) and vehicle.price is None:
            amount = offers.get("price")
            currency = clean_text(offers.get("priceCurrency"))
            if amount is not None:
                vehicle.price = Price(
                    currency=currency,
                    fob_amount=float(str(amount).replace(",", "")),
                    raw=f"{currency or ''} {amount}".strip(),
                )
            availability = clean_text(offers.get("availability"))
            if availability and vehicle.availability is None:
                vehicle.availability = availability.rsplit("/", 1)[-1]

            condition = clean_text(offers.get("itemCondition"))
            if condition and vehicle.condition is None:
                # "https://schema.org/UsedCondition" -> "used"
                vehicle.condition = condition.rsplit("/", 1)[-1].replace("Condition", "").lower()

        images = data.get("image")
        if images and not vehicle.images:
            vehicle.images = [images] if isinstance(images, str) else list(images)

    def _apply_fallbacks(self, vehicle: Vehicle, tree: HTMLParser, url: str) -> None:
        """CSS-selector fallbacks for anything still missing."""
        if vehicle.price is None:
            for selector in S.PRICE_SELECTORS:
                node = tree.css_first(selector)
                if node is None:
                    continue
                text = node.attributes.get("content") if node.tag == "meta" else node.text()
                price = parse_price(text)
                if price is not None and price.fob_amount is not None:
                    vehicle.price = price
                    break

        if not vehicle.images:
            vehicle.images = extract_images(tree, url)

        if not vehicle.features:
            features: list[str] = []
            for selector in S.FEATURE_SELECTORS:
                for node in tree.css(selector):
                    text = clean_text(node.text())
                    # Action buttons ("Notify Me", "Save Search") sit in markup
                    # that looks like an equipment list; they are not features.
                    if text and text.lower() not in S.FEATURE_NOISE:
                        features.append(text)
                if features:
                    break
            vehicle.features = features

        # Title is the last resort for make/model, and the least trustworthy —
        # only consulted when the spec table gave us nothing.
        if vehicle.make is None or vehicle.model is None:
            title = _page_title(tree)
            if title:
                vehicle.raw.setdefault("_page_title", title)
                if vehicle.year is None:
                    vehicle.year = parse_year(title)


# -- module-level helpers, reusable by tests and future sources ----------


def extract_stock_id(href: str) -> str | None:
    """Pull a BeForward reference code out of a URL, or None if not a detail link.

    URLs carry the code lowercased (`/ce566767/id/...`) while the site publishes
    it uppercase (`CE566767`). Normalising to uppercase here keeps ids from the
    listing page and the detail page identical, which dedupe depends on.
    """
    for pattern in S.DETAIL_URL_PATTERNS:
        match = pattern.search(href)
        if match:
            code = match.group(1)
            return code.upper() if re.fullmatch(r"[A-Za-z]{2}\d{5,}", code) else code
    return None


# Body types BeForward uses in its breadcrumb trail, lowercased for matching.
BODY_TYPES = {
    "sedan", "hatchback", "suv", "wagon", "station wagon", "coupe", "convertible",
    "van", "mini van", "minivan", "truck", "pickup", "bus", "mini bus", "minibus",
    "cabriolet", "roadster", "mpv", "crossover", "tractor", "trailer", "machinery",
    "motorcycle", "forklift", "chassis", "light van", "commercial",
}


def extract_breadcrumbs(tree: HTMLParser) -> list[str]:
    """Names from the BreadcrumbList JSON-LD, in trail order.

    The trail carries model, body type and year, none of which appear in the
    Product JSON-LD or the spec table.
    """
    for node in tree.css("script[type='application/ld+json']"):
        raw = node.text()
        if not raw or "BreadcrumbList" not in raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for candidate in _walk_jsonld(data):
            if candidate.get("@type") != "BreadcrumbList":
                continue
            names: list[str] = []
            for element in candidate.get("itemListElement") or []:
                if not isinstance(element, dict):
                    continue
                item = element.get("item")
                name = item.get("name") if isinstance(item, dict) else element.get("name")
                cleaned = clean_text(name)
                if cleaned:
                    names.append(cleaned)
            if names:
                return names
    return []


def extract_jsonld_vehicle(tree: HTMLParser) -> dict[str, Any]:
    """Return the first JSON-LD node describing a product/vehicle.

    Walks nested ``@graph`` structures, which is how most CMSs emit multiple
    entities in one block.
    """
    for node in tree.css("script[type='application/ld+json']"):
        raw = node.text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue  # A malformed block is not a reason to fail the page.
        for candidate in _walk_jsonld(data):
            types = candidate.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if any(t in {"Product", "Car", "Vehicle", "Offer"} for t in types):
                return candidate
    return {}


def _walk_jsonld(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, dict):
        yield data
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in data:
                yield from _walk_jsonld(data[key])
    elif isinstance(data, list):
        for item in data:
            yield from _walk_jsonld(item)


def extract_spec_pairs(tree: HTMLParser) -> dict[str, str]:
    """Read every label/value pair on the page.

    Handles ``th``/``td`` rows, two-column ``td``/``td`` rows, and the
    four-cell ``label,value,label,value`` rows Japanese car sites favour, plus
    ``dl`` definition lists. Everything found is returned verbatim so it can be
    stored in ``Vehicle.raw``.
    """
    pairs: dict[str, str] = {}

    for selector in S.SPEC_TABLE_SELECTORS:
        for table in tree.css(selector):
            for row in table.css("tr"):
                headers = row.css("th")
                cells = row.css("td")

                if headers and cells and len(headers) == len(cells):
                    for header, cell in zip(headers, cells):
                        _add_pair(pairs, header.text(), cell.text())
                elif not headers and len(cells) >= 2 and len(cells) % 2 == 0:
                    # label, value, label, value...
                    for i in range(0, len(cells), 2):
                        _add_pair(pairs, cells[i].text(), cells[i + 1].text())
        if pairs:
            break

    for selector in S.SPEC_LIST_SELECTORS:
        for container in tree.css(selector):
            terms = container.css("dt")
            definitions = container.css("dd")
            for term, definition in zip(terms, definitions):
                _add_pair(pairs, term.text(), definition.text())

    return pairs


def _add_pair(pairs: dict[str, str], label: Any, value: Any) -> None:
    key = clean_text(label)
    val = clean_text(value)
    if not key or val is None:
        return
    key = key.rstrip(":").strip()
    # First occurrence wins: detail tables usually precede "related cars" blocks.
    pairs.setdefault(key, val)


def extract_images(tree: HTMLParser, base_url: str) -> list[str]:
    """Collect vehicle photos, skipping logos, icons and placeholders."""
    urls: list[str] = []
    seen: set[str] = set()

    def consider(node: Node) -> None:
        for attribute in ("src", "data-src", "data-original", "data-lazy"):
            value = node.attributes.get(attribute)
            if not value or S.IMAGE_EXCLUDE.search(value):
                continue
            absolute = urljoin(base_url, value)
            if absolute not in seen:
                seen.add(absolute)
                urls.append(absolute)
            return

    for selector in S.IMAGE_SELECTORS:
        for node in tree.css(selector):
            consider(node)
        if urls:
            break

    if not urls:  # Last resort: every image on the page, filtered.
        for node in tree.css("img"):
            consider(node)

    return urls


def _first_image_in(node: Node, base_url: str) -> str | None:
    for image in node.css("img"):
        for attribute in ("src", "data-src", "data-original"):
            value = image.attributes.get(attribute)
            if value and not S.IMAGE_EXCLUDE.search(value):
                return urljoin(base_url, value)
    return None


def _page_title(tree: HTMLParser) -> str | None:
    for selector in S.TITLE_SELECTORS:
        node = tree.css_first(selector)
        if node is None:
            continue
        text = node.attributes.get("content") if node.tag == "meta" else node.text()
        cleaned = clean_text(text)
        if cleaned:
            return cleaned
    return None


def _spec_value(specs: dict[str, str], field: str) -> str | None:
    for label, value in specs.items():
        if S.map_label(label) == field:
            return value
    return None


_FIELD_PARSERS = {
    "mileage_km": parse_mileage_km,
    "engine_cc": parse_engine_cc,
    "fuel": parse_fuel,
    "transmission": parse_transmission,
    "drivetrain": parse_drivetrain,
    "steering": parse_steering,
    "year": parse_year,
    "doors": lambda v: parse_count(v, maximum=10),
    "seats": lambda v: parse_count(v, maximum=100),
    "price": parse_price,
}


def _parse_field(field: str, value: str) -> Any:
    """Convert a raw spec value for the field it maps to."""
    parser = _FIELD_PARSERS.get(field)
    if parser is not None:
        return parser(value)
    if field == "source_id":
        return None  # Handled explicitly; never overwritten from the table.
    return clean_text(value)


def _nested(data: dict[str, Any], *keys: str) -> Any:
    """Safely walk nested dicts: _nested(d, 'brand', 'name')."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
