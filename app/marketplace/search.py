from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlencode

from app.models import SearchConfig

BASE_URL = "https://www.facebook.com"


def location_slug(location: str) -> str:
    # Facebook accepts city slugs or its numeric location IDs in this path. The
    # latter is the most exact option and can be copied from a browser URL.
    slug = unicodedata.normalize("NFKD", location.strip()).encode("ascii", "ignore").decode()
    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", slug)
    return slug.strip("-") or "marketplace"


def build_search_url(search: SearchConfig) -> str:
    location = location_slug(search.location)
    if not search.query and not search.category_id:
        raise ValueError(
            "Unfiltered Marketplace root is ranked as Today's picks; expand it into categories"
        )
    path = f"/marketplace/{location}/search" if location != "marketplace" else "/marketplace/search"
    params: dict[str, str | int] = {
        "sortBy": "creation_time_descend",
        "exact": "false",
        "radius": search.radius_km,
    }
    if search.query:
        params["query"] = search.query
    if search.min_price is not None:
        params["minPrice"] = search.min_price
    if search.max_price is not None:
        params["maxPrice"] = search.max_price
    if search.category_id:
        params["category_id"] = search.category_id
    if search.days_since_listed is not None:
        params["daysSinceListed"] = search.days_since_listed
    return f"{BASE_URL}{path}?{urlencode(params)}"
