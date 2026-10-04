from __future__ import annotations

import logging

from app.config import Settings
from app.marketplace.anonymous_http import AnonymousHttpProvider
from app.marketplace.base import MarketplaceError, MarketplaceProvider
from app.marketplace.playwright_client import PlaywrightProvider
from app.models import MarketplaceListing, SearchConfig

log = logging.getLogger(__name__)


class AutoMarketplaceProvider:
    name = "unselected"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.selected: MarketplaceProvider | None = None

    async def start(self) -> None:
        return None

    def _profile_has_session(self) -> bool:
        profile = self.settings.browser_profile_path
        return profile.exists() and any(p.is_file() for p in profile.rglob("*"))

    def _candidates(self) -> list[MarketplaceProvider]:
        mode = self.settings.marketplace_mode
        if mode == "anonymous_http":
            return [AnonymousHttpProvider(self.settings)]
        if mode == "anonymous_playwright":
            return [PlaywrightProvider(self.settings, False)]
        if mode == "authenticated_playwright":
            return [PlaywrightProvider(self.settings, True)]
        candidates: list[MarketplaceProvider] = [
            AnonymousHttpProvider(self.settings),
            PlaywrightProvider(self.settings, False),
        ]
        if self._profile_has_session():
            candidates.append(PlaywrightProvider(self.settings, True))
        return candidates

    async def get_listings(self, search: SearchConfig) -> list[MarketplaceListing]:
        if self.selected:
            return await self.selected.get_listings(search)
        failures: list[str] = []
        for candidate in self._candidates():
            try:
                await candidate.start()
                listings = await candidate.get_listings(search)
                self.selected = candidate
                self.name = candidate.name
                log.info("Marketplace provider selected", extra={"provider": self.name})
                return listings
            except Exception as exc:
                failures.append(f"{candidate.name}: {exc}")
                log.warning(
                    "Marketplace provider unavailable: %s",
                    exc,
                    extra={"provider": candidate.name},
                )
                await candidate.close()
        profile_hint = (
            " Run `python -m app.login` first." if not self._profile_has_session() else ""
        )
        raise MarketplaceError("; ".join(failures) + profile_hint)

    async def close(self) -> None:
        if self.selected:
            await self.selected.close()
            self.selected = None
