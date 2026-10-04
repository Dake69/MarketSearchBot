from __future__ import annotations

from typing import Protocol

from app.models import MarketplaceListing, SearchConfig


class MarketplaceError(RuntimeError):
    """Marketplace could not provide a usable result."""


class LoginRequiredError(MarketplaceError):
    """The page is gated behind Facebook login."""


class RateLimitedError(MarketplaceError):
    """Facebook temporarily rejected requests."""


class MarketplaceProvider(Protocol):
    name: str

    async def start(self) -> None: ...

    async def get_listings(self, search: SearchConfig) -> list[MarketplaceListing]: ...

    async def close(self) -> None: ...
