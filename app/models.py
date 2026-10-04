from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SearchConfig:
    name: str
    query: str = ""
    location: str = ""
    latitude: float | None = None
    longitude: float | None = None
    radius_km: int = 50
    min_price: int | None = None
    max_price: int | None = None
    category_id: str | None = None
    days_since_listed: int | None = 1


@dataclass(slots=True)
class MarketplaceListing:
    listing_id: str | None
    title: str
    price: str = ""
    currency: str = ""
    location: str = ""
    url: str = ""
    image_url: str = ""
    seller_name: str = ""
    listed_at: str = ""
    category: str = ""
    raw: dict[str, Any] | None = None

    @property
    def id(self) -> str:
        if self.listing_id:
            return self.listing_id
        canonical = "\x1f".join(
            x.strip().casefold() for x in (self.url, self.title, self.price, self.location)
        )
        return "fp:" + hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def first_seen_iso(self) -> str:
        return datetime.now(UTC).isoformat()
