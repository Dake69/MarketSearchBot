from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta

from app.bot import TelegramSettingsBot
from app.config import Settings
from app.database import Database
from app.marketplace.auto import AutoMarketplaceProvider
from app.models import MarketplaceListing, SearchConfig
from app.preferences import ALL_CATEGORY_IDS, PreferencesStore
from app.telegram import TelegramClient

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings, dry_run: bool = False, enable_bot: bool = True):
        self.settings = settings
        self.dry_run = dry_run
        self.database = Database(settings.storage_location)
        self.preferences = PreferencesStore(settings.storage_location)
        self.provider = AutoMarketplaceProvider(settings)
        self.telegram = TelegramClient(
            settings.telegram_bot_token, settings.telegram_chat_id, settings.http_timeout_seconds
        )
        self.config_changed = asyncio.Event()
        self.settings_bot = TelegramSettingsBot(
            self.telegram,
            self.preferences,
            settings.telegram_chat_id,
            self.config_changed.set,
        )
        self.enable_bot = enable_bot
        self.bot_task: asyncio.Task | None = None
        self.all_category_cursor = 0

    async def start(self) -> None:
        await self.database.open()
        await self.preferences.open(self.settings.searches[0])
        await self.provider.start()
        if self.enable_bot:
            self.bot_task = asyncio.create_task(
                self.settings_bot.run(), name="telegram-settings-bot"
            )

    async def cycle(self) -> tuple[int, int]:
        found = sent = 0
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.listing_retention_days)
        pruned = await self.database.prune_stale(cutoff.isoformat())
        if pruned:
            log.info("Deleted %d stale listings", pruned)
        preferences = await self.preferences.get()
        if not preferences.queries and not preferences.categories:
            batch_size = self.settings.all_categories_per_cycle
            categories = tuple(
                ALL_CATEGORY_IDS[(self.all_category_cursor + index) % len(ALL_CATEGORY_IDS)]
                for index in range(batch_size)
            )
            self.all_category_cursor = (self.all_category_cursor + batch_size) % len(
                ALL_CATEGORY_IDS
            )
            searches = preferences.searches(categories)
            log.info(
                "All-listings newest sweep batch: %s",
                ", ".join(categories),
            )
        else:
            searches = preferences.searches()
        for index, search in enumerate(searches):
            if index and self.settings.jitter_seconds > 0:
                await asyncio.sleep(random.uniform(0, self.settings.jitter_seconds))
            try:
                listings = await self._fetch_with_backoff(search)
                new_count, sent_count = await self._process(search, listings)
                found += new_count
                sent += sent_count
                log.info(
                    "Search completed: %d listings, %d new, %d sent",
                    len(listings),
                    new_count,
                    sent_count,
                    extra={"search": search.name, "provider": self.provider.name},
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Search cycle failed", extra={"search": search.name})
        return found, sent

    async def _fetch_with_backoff(self, search: SearchConfig) -> list[MarketplaceListing]:
        for attempt in range(3):
            try:
                return await self.provider.get_listings(search)
            except Exception:
                if attempt == 2:
                    raise
                delay = min(5 * 2**attempt + random.random() * 2, 30)
                log.warning(
                    "Marketplace fetch failed; retrying",
                    extra={"search": search.name, "retry_in": round(delay, 1)},
                    exc_info=True,
                )
                await asyncio.sleep(delay)
        return []

    async def _process(
        self, search: SearchConfig, listings: list[MarketplaceListing]
    ) -> tuple[int, int]:
        baseline_exists = await self.database.is_baseline_initialized(search.name)
        suppress = not baseline_exists and not self.settings.send_existing_on_first_run
        timestamp = datetime.now(UTC).isoformat()
        new_count = sent_count = 0
        for listing in listings:
            if self.dry_run:
                # Dry-run deliberately has no DB side effects.
                if await self.database.contains(listing.id):
                    continue
                log.info(
                    "Dry-run listing: %s | %s",
                    listing.title,
                    listing.url,
                    extra={"search": search.name, "listing_id": listing.id},
                )
                new_count += 1
                continue
            record = await self.database.record(listing, search.name, timestamp, suppress)
            new_count += int(record.inserted)
            if record.should_send:
                try:
                    await self.telegram.send_listing(listing)
                    await self.database.mark_sent(listing.id)
                    sent_count += 1
                except Exception as exc:
                    await self.database.mark_send_error(listing.id, str(exc))
                    log.exception(
                        "Telegram delivery failed; listing remains pending",
                        extra={"listing_id": listing.id, "search": search.name},
                    )
        if not self.dry_run and not baseline_exists:
            await self.database.mark_baseline_initialized(search.name, timestamp)
            if suppress:
                log.info(
                    "Initial result set saved as baseline without notifications",
                    extra={"search": search.name},
                )
        return new_count, sent_count

    async def close(self) -> None:
        await self.settings_bot.stop()
        if self.bot_task:
            self.bot_task.cancel()
            await asyncio.gather(self.bot_task, return_exceptions=True)
        await self.provider.close()
        await self.telegram.close()
        await self.preferences.close()
        await self.database.close()
