from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.marketplace.base import LoginRequiredError, MarketplaceError, RateLimitedError
from app.marketplace.parser import parse_html
from app.marketplace.search import build_search_url
from app.models import MarketplaceListing, SearchConfig

log = logging.getLogger(__name__)


class AnonymousHttpProvider:
    name = "anonymous_http"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                "Accept-Language": f"{self.settings.browser_locale},en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

    async def get_listings(self, search: SearchConfig) -> list[MarketplaceListing]:
        if self.client is None:
            await self.start()
        assert self.client
        response = await self.client.get(build_search_url(search))
        if response.status_code == 429:
            raise RateLimitedError("Facebook returned HTTP 429")
        if response.status_code >= 400:
            raise MarketplaceError(f"Facebook returned HTTP {response.status_code}")
        lowered = response.text.lower()
        if "login_form" in lowered or "you must log in" in lowered:
            raise LoginRequiredError("Facebook requires login")
        listings = parse_html(response.text, self.settings.max_listings_per_search)
        if not listings:
            raise MarketplaceError("Public HTML contained no listing cards")
        return listings

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None
