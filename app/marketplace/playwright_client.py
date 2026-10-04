from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.config import Settings
from app.marketplace.base import LoginRequiredError, MarketplaceError, RateLimitedError
from app.marketplace.parser import canonical_listing_url, listing_id_from_url, listings_from_json
from app.marketplace.search import build_search_url
from app.models import MarketplaceListing, SearchConfig

log = logging.getLogger(__name__)


class PlaywrightProvider:
    def __init__(self, settings: Settings, authenticated: bool):
        self.settings = settings
        self.authenticated = authenticated
        self.name = "authenticated_playwright" if authenticated else "anonymous_playwright"
        self._playwright: Any = None
        self.context: Any = None
        self.page: Any = None

    @property
    def profile_path(self) -> Path:
        if self.authenticated:
            return self.settings.browser_profile_path
        return self.settings.browser_profile_path.with_name(
            self.settings.browser_profile_path.name + "-anonymous"
        )

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        if self.context:
            return
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self.context = await self._playwright.chromium.launch_persistent_context(
            str(self.profile_path),
            headless=self.settings.headless,
            locale=self.settings.browser_locale,
            viewport={"width": 1365, "height": 900},
            args=["--disable-dev-shm-usage"],
        )
        self.context.set_default_timeout(self.settings.page_timeout_seconds)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def _accept_consent_if_present(self) -> None:
        # This only accepts the ordinary privacy dialog; it never handles challenges/CAPTCHA.
        for label in ("Allow all cookies", "Accept all cookies", "Accept All"):
            button = self.page.get_by_role("button", name=label, exact=True)
            try:
                if await button.is_visible(timeout=750):
                    await button.click()
                    return
            except Exception:
                continue

    async def get_listings(self, search: SearchConfig) -> list[MarketplaceListing]:
        for attempt in range(2):
            try:
                return await self._get_listings(search)
            except (LoginRequiredError, RateLimitedError):
                raise
            except Exception as exc:
                if attempt == 0 and self._is_browser_failure(exc):
                    log.warning("Browser stopped; restarting persistent context", exc_info=True)
                    await self.close()
                    await self.start()
                    continue
                if isinstance(exc, MarketplaceError):
                    raise
                raise MarketplaceError(f"Playwright failed: {exc}") from exc
        raise MarketplaceError("Playwright failed after restart")

    async def _get_listings(self, search: SearchConfig) -> list[MarketplaceListing]:
        if not self.context:
            await self.start()
        if search.latitude is not None and search.longitude is not None:
            await self.context.set_geolocation(
                {"latitude": search.latitude, "longitude": search.longitude}
            )
            await self.context.grant_permissions(["geolocation"], origin="https://www.facebook.com")

        network_payloads: list[Any] = []
        network_responses: list[Any] = []

        def on_response(response) -> None:
            # Do not read response bodies while React is rendering the cards.
            # Concurrent body reads delayed DOM hydration in constrained containers.
            if "graphql" in response.url or "/api/" in response.url:
                network_responses.append(response)

        self.page.on("response", on_response)
        try:
            response = await self.page.goto(build_search_url(search), wait_until="domcontentloaded")
            if response and response.status == 429:
                raise RateLimitedError("Facebook returned HTTP 429 in Chromium")
            await self._accept_consent_if_present()
            await self._detect_gate()
            try:
                await self.page.locator('a[href*="/marketplace/item/"]').first.wait_for(
                    state="attached", timeout=min(15_000, self.settings.page_timeout_seconds)
                )
            except Exception:
                await self._detect_gate()
            await self.page.mouse.wheel(0, 900)
            await self.page.wait_for_timeout(750)
            from_dom = await self._extract_dom()
            for response_item in network_responses[-20:]:
                try:
                    content_type = (await response_item.header_value("content-type")) or ""
                    if "json" in content_type:
                        payload = await asyncio.wait_for(response_item.json(), timeout=2)
                        network_payloads.append(payload)
                except Exception:
                    continue
            combined = {listing.id: listing for listing in from_dom}
            for payload in network_payloads:
                for listing in listings_from_json(payload):
                    existing = combined.get(listing.id)
                    if existing:
                        self._merge(existing, listing)
                    else:
                        combined[listing.id] = listing
                    if len(combined) >= self.settings.max_listings_per_search:
                        break
            listings = list(combined.values())[: self.settings.max_listings_per_search]
            if not listings:
                body = (await self.page.locator("body").inner_text()).casefold()
                empty_markers = (
                    "there are currently no products in your area",
                    "no listings found",
                    "we couldn't find any results",
                    "žádné nabídky",
                    "nejsou žádné produkty",
                )
                if any(marker in body for marker in empty_markers):
                    log.info("Marketplace returned an empty result set")
                    return []
                raise MarketplaceError(
                    f"{self.name} loaded the page but found no Marketplace listing links"
                )
            return listings
        finally:
            self.page.remove_listener("response", on_response)

    async def _detect_gate(self) -> None:
        url = self.page.url.lower()
        body = (await self.page.locator("body").inner_text(timeout=5000)).lower()
        if "checkpoint" in url or "captcha" in body:
            raise MarketplaceError("Facebook displayed a checkpoint/CAPTCHA; no bypass attempted")
        login_visible = "/login" in url or (
            ("log in" in body or "вход" in body) and "marketplace" not in body
        )
        if login_visible:
            raise LoginRequiredError(f"{self.name} requires Facebook login")
        if "temporarily blocked" in body or "try again later" in body:
            raise RateLimitedError("Facebook displayed a temporary block")

    async def _extract_dom(self) -> list[MarketplaceListing]:
        rows = await self.page.locator('a[href*="/marketplace/item/"]').evaluate_all(
            """(links, limit) => links.slice(0, limit).map(a => {
              const card = a.closest('[role="main"] a[href*="/marketplace/item/"]') || a;
              const img = a.querySelector('img') || card.querySelector?.('img');
              return {
                url: a.href,
                text: (a.innerText || a.getAttribute('aria-label') || '').trim(),
                image: img ? (img.currentSrc || img.src || '') : '',
                alt: img ? (img.alt || '') : ''
              };
            })""",
            self.settings.max_listings_per_search * 2,
        )
        result: dict[str, MarketplaceListing] = {}
        for row in rows:
            listing_id = listing_id_from_url(row.get("url", ""))
            if not listing_id or listing_id in result:
                continue
            lines = [x.strip() for x in row.get("text", "").splitlines() if x.strip()]
            title, price, location, listed_at = _fields_from_lines(
                lines, row.get("alt", ""), listing_id
            )
            result[listing_id] = MarketplaceListing(
                listing_id=listing_id,
                title=title,
                price=price,
                currency=_currency_from_price(price),
                location=location,
                url=canonical_listing_url(row["url"]),
                image_url=row.get("image", ""),
                listed_at=listed_at,
            )
        return list(result.values())

    @staticmethod
    def _merge(target: MarketplaceListing, source: MarketplaceListing) -> None:
        for field in (
            "title",
            "price",
            "currency",
            "location",
            "url",
            "image_url",
            "seller_name",
            "listed_at",
            "category",
        ):
            if not getattr(target, field) and getattr(source, field):
                setattr(target, field, getattr(source, field))

    @staticmethod
    def _is_browser_failure(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(x in text for x in ("target closed", "browser has been closed", "page crashed"))

    async def close(self) -> None:
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self.context = self.page = self._playwright = None


def _fields_from_lines(lines: list[str], alt: str, listing_id: str) -> tuple[str, str, str, str]:
    price = next(
        (
            x
            for x in lines
            if any(c.isdigit() for c in x)
            and any(symbol in x for symbol in ("$", "€", "£", "Kč", "CZK", "USD", "EUR"))
        ),
        "",
    )
    recency_words = (
        "just listed",
        "listed ",
        "minute ago",
        "minutes ago",
        "hour ago",
        "hours ago",
        "today",
        "new listing",
    )
    # Card badges (especially "Just listed") are not reliable creation
    # timestamps: Facebook may also apply them to renewed/resurfaced listings.
    # Strip the badge from the title/location, but do not report it as a fact.
    recency_label = next(
        (x for x in lines if any(word in x.casefold() for word in recency_words)), ""
    )
    content = [x for x in lines if x not in {price, recency_label}]
    title = (content[0] if content else alt) or f"Marketplace listing {listing_id}"
    location = content[1] if len(content) > 1 else ""
    return title, price, location, ""


def _currency_from_price(price: str) -> str:
    folded = price.upper()
    for currency in ("CZK", "EUR", "USD", "GBP"):
        if currency in folded:
            return currency
    for symbol, currency in (("€", "EUR"), ("£", "GBP"), ("$", "USD"), ("KČ", "CZK")):
        if symbol in folded:
            return currency
    return ""
