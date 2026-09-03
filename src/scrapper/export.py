"""Map internal ``Vehicle`` records to the consuming application's JSON shape.

Kept separate from ``models.py`` on purpose. The scraped record stays lossless
(every spec row survives in ``Vehicle.raw``), while this module renders the
narrower, camelCase view the downstream importer expects. Changing the export
shape therefore never risks the parsing layer, and a second consumer wanting a
different shape is another function here rather than a schema migration.

Envelope:

    {"sourceCode": ..., "capturedAtUtc": ..., "vehicles": [...]}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .models import Steering, Vehicle

# The consuming application uses rhd/lhd rather than right/left.
_STEERING = {Steering.RIGHT.value: "rhd", Steering.LEFT.value: "lhd"}

# BeForward's headline figure is its FOB price by site convention; it is not
# labelled as such in the markup. Recorded explicitly so the assumption is
# visible in the output rather than implied.
DEFAULT_PRICE_TYPE = "FOB"

_UNAVAILABLE_HINTS = ("outofstock", "soldout", "sold", "discontinued", "unavailable")


def _iso_z(value: datetime | None) -> str | None:
    """Render a datetime as ISO-8601 with a Z suffix, seconds precision."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _number(value: float | None) -> float | int | None:
    """Emit whole amounts as ints so prices read as 6180, not 6180.0."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def _is_available(vehicle: Vehicle) -> bool | None:
    """True/False when the listing states availability, otherwise None.

    Deliberately not defaulting to True: a missing availability field means we
    did not read one, which is not the same as knowing the car is in stock.
    """
    if vehicle.availability is None:
        return None
    normalized = vehicle.availability.replace(" ", "").replace("_", "").lower()
    if any(hint in normalized for hint in _UNAVAILABLE_HINTS):
        return False
    if "instock" in normalized or "available" in normalized:
        return True
    return None


def to_export_record(vehicle: Vehicle, markets: list[str] | None = None) -> dict[str, Any]:
    """Render one Vehicle in the consumer's field names and value vocabulary."""
    price = vehicle.price
    steering = vehicle.steering.value if hasattr(vehicle.steering, "value") else vehicle.steering

    return {
        "externalId": vehicle.source_id,
        "listingUrl": vehicle.source_url,
        "make": vehicle.make,
        "model": vehicle.model,
        "variant": vehicle.grade,
        "year": vehicle.year,
        "mileage": vehicle.mileage_km,
        # Odometer readings are converted to km during parsing, so the unit is
        # always km regardless of how the source displayed it.
        "mileageUnit": "km" if vehicle.mileage_km is not None else None,
        "steering": _STEERING.get(steering) if steering else None,
        "fuelType": _value(vehicle.fuel),
        "transmission": _value(vehicle.transmission),
        "drivetrain": _value(vehicle.drivetrain),
        "price": _number(price.fob_amount) if price else None,
        "currency": price.currency if price else None,
        "priceType": DEFAULT_PRICE_TYPE if price and price.fob_amount is not None else None,
        "chassisNumber": vehicle.chassis_no,
        # BeForward publishes no port-of-loading field. `location` is the yard
        # the vehicle sits in (e.g. NAGOYA) and is the closest honest proxy.
        "portOfLoading": vehicle.port or vehicle.location,
        "destinationMarkets": list(markets or []),
        "imageUrls": list(vehicle.images),
        "isAvailable": _is_available(vehicle),
        "lastSeenAtUtc": _iso_z(vehicle.scraped_at),
    }


def build_envelope(
    vehicles: Iterable[Vehicle],
    *,
    source: str,
    markets: list[str] | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Wrap exported records in the consumer's envelope."""
    return {
        "sourceCode": source,
        "capturedAtUtc": _iso_z(captured_at or datetime.now(timezone.utc)),
        "vehicles": [to_export_record(v, markets) for v in vehicles],
    }


def _value(field: Any) -> Any:
    """Enums are stored by value, but tolerate either form."""
    return field.value if hasattr(field, "value") else field
