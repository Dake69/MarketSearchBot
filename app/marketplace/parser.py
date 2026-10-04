from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.models import MarketplaceListing

ITEM_RE = re.compile(r"/marketplace/item/(\d+)")
ANCHOR_RE = re.compile(
    r"<a\b[^>]*?href=[\"']([^\"']*?/marketplace/item/\d+[^\"']*)[\"'][^>]*>(.*?)</a>",
    re.I | re.S,
)
TAG_RE = re.compile(r"<[^>]+>")
IMAGE_RE = re.compile(r"<img\b[^>]*?src=[\"']([^\"']+)[\"']", re.I)


def listing_id_from_url(url: str) -> str | None:
    match = ITEM_RE.search(url)
    return match.group(1) if match else None


def canonical_listing_url(url: str) -> str:
    absolute = urljoin("https://www.facebook.com", html.unescape(url))
    parts = urlsplit(absolute)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def parse_html(html_text: str, limit: int = 60) -> list[MarketplaceListing]:
    found: dict[str, MarketplaceListing] = {}
    decoded = html.unescape(html_text)
    for href, body in ANCHOR_RE.findall(decoded):
        url = canonical_listing_url(href)
        listing_id = listing_id_from_url(url)
        if not listing_id or listing_id in found:
            continue
        text = " ".join(TAG_RE.sub(" ", body).split())
        image = IMAGE_RE.search(body)
        found[listing_id] = MarketplaceListing(
            listing_id=listing_id,
            title=text or f"Marketplace listing {listing_id}",
            url=url,
            image_url=html.unescape(image.group(1)) if image else "",
        )
        if len(found) >= limit:
            break
    if len(found) < limit:
        for payload in embedded_json_objects(html_text):
            for listing in listings_from_json(payload):
                if listing.id not in found:
                    found[listing.id] = listing
                    if len(found) >= limit:
                        break
    return list(found.values())


def embedded_json_objects(source: str) -> Iterable[Any]:
    decoder = json.JSONDecoder()
    for marker in ('<script type="application/json"', '<script type="application/ld+json"'):
        start = 0
        while (pos := source.find(marker, start)) >= 0:
            pos = source.find(">", pos) + 1
            end = source.find("</script>", pos)
            if pos <= 0 or end < 0:
                break
            raw = html.unescape(source[pos:end]).strip()
            try:
                yield decoder.decode(raw)
            except (ValueError, TypeError):
                pass
            start = end + 9


def _get(obj: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value: Any = obj
        for key in path.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value not in (None, "", []):
            return value
    return None


def listings_from_json(payload: Any, limit: int = 100) -> list[MarketplaceListing]:
    result: dict[str, MarketplaceListing] = {}
    stack = [payload]
    visited: set[int] = set()
    while stack and len(result) < limit:
        node = stack.pop()
        if isinstance(node, dict):
            if id(node) in visited:
                continue
            visited.add(id(node))
            candidate = node.get("listing") if isinstance(node.get("listing"), dict) else node
            raw_id = _get(candidate, "id", "listing_id", "marketplace_listing_id")
            url = _get(candidate, "url", "listingUrl", "story_url") or ""
            if not raw_id and url:
                raw_id = listing_id_from_url(str(url))
            title = _get(candidate, "marketplace_listing_title", "title", "name")
            # Requiring Marketplace evidence avoids treating every GraphQL object with id/title as a listing.
            evidence = raw_id and (
                "marketplace" in str(url).lower() or "marketplace_listing_title" in candidate
            )
            if evidence and title:
                listing_id = str(raw_id)
                price = (
                    _get(
                        candidate, "listing_price.formatted_amount", "formatted_price.text", "price"
                    )
                    or ""
                )
                image = (
                    _get(
                        candidate,
                        "primary_listing_photo.image.uri",
                        "primary_listing_photo.uri",
                        "image.uri",
                        "image.url",
                        "image_url",
                    )
                    or ""
                )
                city = (
                    _get(
                        candidate, "location.reverse_geocode.city", "location_text.text", "location"
                    )
                    or ""
                )
                if isinstance(city, dict):
                    city = _get(city, "name", "city") or ""
                result[listing_id] = MarketplaceListing(
                    listing_id=listing_id,
                    title=str(title),
                    price=str(price),
                    location=str(city),
                    url=canonical_listing_url(str(url or f"/marketplace/item/{listing_id}")),
                    image_url=str(image),
                    seller_name=str(_get(candidate, "seller.name", "seller_name") or ""),
                    listed_at=str(
                        _get(candidate, "creation_time", "listed_at", "relative_time") or ""
                    ),
                    category=str(_get(candidate, "category_name", "category.name") or ""),
                    raw=candidate,
                )
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return list(result.values())
