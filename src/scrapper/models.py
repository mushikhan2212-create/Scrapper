"""Source-agnostic vehicle schema.

Deliberately *not* BeForward-shaped: every provider added later fills the same
typed core and keeps its own provider-specific detail in ``raw``. That two-layer
design is what makes phase-2 aggregation a merge rather than a rewrite.

Only the identity fields are required. A missing spec must never lose a record.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Fuel(str, Enum):
    PETROL = "petrol"
    DIESEL = "diesel"
    HYBRID = "hybrid"
    PLUGIN_HYBRID = "plugin_hybrid"
    ELECTRIC = "electric"
    LPG = "lpg"
    CNG = "cng"
    OTHER = "other"


class Transmission(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"
    CVT = "cvt"
    SEMI_AUTOMATIC = "semi_automatic"
    OTHER = "other"


class Drivetrain(str, Enum):
    TWO_WD = "2wd"
    FOUR_WD = "4wd"
    AWD = "awd"
    FWD = "fwd"
    RWD = "rwd"
    OTHER = "other"


class Steering(str, Enum):
    RIGHT = "right"
    LEFT = "left"


class Price(BaseModel):
    """Money as scraped. Amounts stay in the listing's own currency.

    No FX conversion happens here on purpose: rates belong to the consuming
    application, and baking a stale rate into stored records is a bug factory.
    """

    model_config = ConfigDict(extra="allow")

    currency: str | None = None
    fob_amount: float | None = Field(default=None, description="Free On Board price")
    cif_amount: float | None = Field(default=None, description="Cost, Insurance & Freight")
    total_amount: float | None = None
    raw: str | None = Field(default=None, description="Price string exactly as displayed")


class Vehicle(BaseModel):
    """One vehicle listing, normalized."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    # --- Identity -------------------------------------------------------
    source: str = Field(description="Provider slug, e.g. 'beforward'")
    source_id: str = Field(description="Provider's own stock/listing id")
    source_url: str
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str | None = Field(
        default=None, description="Hash of the meaningful fields; changes when the listing changes"
    )

    # --- Vehicle identification ----------------------------------------
    make: str | None = None
    model: str | None = None
    grade: str | None = Field(default=None, description="Trim / grade, e.g. 'G Superior'")
    model_code: str | None = None
    year: int | None = Field(default=None, ge=1900, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    body_type: str | None = None
    doors: int | None = Field(default=None, ge=0, le=10)
    seats: int | None = Field(default=None, ge=0, le=100)

    # --- Mechanical ------------------------------------------------------
    mileage_km: int | None = Field(default=None, ge=0)
    engine_cc: int | None = Field(default=None, ge=0)
    engine_code: str | None = None
    fuel: Fuel | None = None
    transmission: Transmission | None = None
    drivetrain: Drivetrain | None = None
    steering: Steering | None = None

    # --- Commercial ------------------------------------------------------
    price: Price | None = None
    availability: str | None = Field(default=None, description="e.g. 'in stock', 'sold'")
    location: str | None = Field(default=None, description="Where the vehicle is held")
    port: str | None = None

    # --- Descriptive -----------------------------------------------------
    color: str | None = None
    condition: str | None = None
    chassis_no: str | None = None
    features: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)

    # --- Escape hatch ----------------------------------------------------
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Every spec row captured verbatim, label -> value. Nothing on the page "
            "is lost even when it has no typed field yet, so a newly needed field "
            "never requires a re-scrape."
        ),
    )

    @field_validator("features", "images", mode="before")
    @classmethod
    def _drop_empties(cls, v: Any) -> Any:
        if isinstance(v, list):
            seen: set[str] = set()
            out: list[str] = []
            for item in v:
                s = str(item).strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
            return out
        return v

    # Fields that describe the listing itself, as opposed to when we happened
    # to look at it. Only these feed the change-detection hash.
    _HASH_FIELDS = (
        "make", "model", "grade", "year", "month", "mileage_km", "engine_cc",
        "fuel", "transmission", "drivetrain", "steering", "color", "condition",
        "availability", "location", "body_type", "doors", "seats",
    )

    def compute_content_hash(self) -> str:
        """Stable hash of the listing's meaningful content.

        Excludes ``scraped_at`` so re-scraping an unchanged listing yields the
        same hash — that is what makes cheap change detection possible.
        """
        parts = [f"{f}={getattr(self, f)!r}" for f in self._HASH_FIELDS]
        if self.price is not None:
            parts.append(f"price={self.price.fob_amount!r}/{self.price.currency!r}")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]

    def finalize(self) -> "Vehicle":
        """Populate derived fields. Call once, after parsing."""
        self.content_hash = self.compute_content_hash()
        return self


class ListingItem(BaseModel):
    """A card on a search-results page.

    Carries whatever the card itself showed, which acts as fallback data when a
    detail-page fetch later fails.
    """

    source_id: str
    detail_url: str
    title: str | None = None
    price_raw: str | None = None
    thumbnail: str | None = None


class ListingPage(BaseModel):
    """Parsed search-results page."""

    items: list[ListingItem] = Field(default_factory=list)
    total_pages: int | None = None
    total_results: int | None = None
    next_page_url: str | None = None
