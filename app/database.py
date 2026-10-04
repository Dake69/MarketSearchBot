from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.models import MarketplaceListing
from app.storage import Storage


@dataclass(frozen=True, slots=True)
class RecordResult:
    inserted: bool
    should_send: bool


class Database:
    def __init__(self, location: str | Path):
        self.storage = Storage(location)

    async def open(self) -> None:
        await self.storage.open()
        await self.storage.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                listing_id TEXT,
                title TEXT NOT NULL,
                price TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                seller_name TEXT NOT NULL DEFAULT '',
                listed_at TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                sent_to_telegram INTEGER NOT NULL DEFAULT 0,
                suppressed_baseline INTEGER NOT NULL DEFAULT 0,
                telegram_error TEXT
            );
            CREATE TABLE IF NOT EXISTS listing_searches (
                listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                search_name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (listing_id, search_name)
            );
            CREATE TABLE IF NOT EXISTS search_state (
                search_name TEXT PRIMARY KEY,
                baseline_initialized_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_listings_pending
                ON listings(sent_to_telegram, suppressed_baseline);
            CREATE INDEX IF NOT EXISTS idx_listings_last_seen
                ON listings(last_seen_at);
            """
        )
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def is_baseline_initialized(self, search_name: str) -> bool:
        row = await self.storage.fetchone(
            "SELECT 1 FROM search_state WHERE search_name = ?", (search_name,)
        )
        return row is not None

    async def contains(self, listing_id: str) -> bool:
        row = await self.storage.fetchone("SELECT 1 FROM listings WHERE id = ?", (listing_id,))
        return row is not None

    async def mark_baseline_initialized(self, search_name: str, timestamp: str) -> None:
        await self.storage.execute(
            """INSERT INTO search_state(search_name, baseline_initialized_at) VALUES (?, ?)
               ON CONFLICT(search_name) DO UPDATE
               SET baseline_initialized_at = excluded.baseline_initialized_at""",
            (search_name, timestamp),
        )
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def record(
        self, listing: MarketplaceListing, search_name: str, timestamp: str, suppress: bool
    ) -> RecordResult:
        await self.storage.execute("BEGIN IMMEDIATE")
        try:
            existing = await self.storage.fetchone(
                "SELECT sent_to_telegram, suppressed_baseline FROM listings WHERE id = ?",
                (listing.id,),
            )
            inserted = existing is None
            if inserted:
                await self.storage.execute(
                    """INSERT INTO listings(
                        id, listing_id, title, price, currency, location, url, image_url,
                        seller_name, listed_at, category, first_seen_at, last_seen_at,
                        sent_to_telegram, suppressed_baseline
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        listing.id,
                        listing.listing_id,
                        listing.title,
                        listing.price,
                        listing.currency,
                        listing.location,
                        listing.url,
                        listing.image_url,
                        listing.seller_name,
                        listing.listed_at,
                        listing.category,
                        timestamp,
                        timestamp,
                        int(suppress),
                        int(suppress),
                    ),
                )
                should_send = not suppress
            else:
                await self.storage.execute(
                    """UPDATE listings SET
                        title = CASE WHEN ? <> '' THEN ? ELSE title END,
                        price = CASE WHEN ? <> '' THEN ? ELSE price END,
                        location = CASE WHEN ? <> '' THEN ? ELSE location END,
                        url = CASE WHEN ? <> '' THEN ? ELSE url END,
                        image_url = CASE WHEN ? <> '' THEN ? ELSE image_url END,
                        last_seen_at = ? WHERE id = ?""",
                    (
                        listing.title,
                        listing.title,
                        listing.price,
                        listing.price,
                        listing.location,
                        listing.location,
                        listing.url,
                        listing.url,
                        listing.image_url,
                        listing.image_url,
                        timestamp,
                        listing.id,
                    ),
                )
                should_send = not bool(existing["sent_to_telegram"]) and not bool(
                    existing["suppressed_baseline"]
                )
            await self.storage.execute(
                """INSERT INTO listing_searches(listing_id, search_name, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(listing_id, search_name) DO UPDATE SET last_seen_at = excluded.last_seen_at""",
                (listing.id, search_name, timestamp, timestamp),
            )
            await self.storage.commit()
            return RecordResult(inserted=inserted, should_send=should_send)
        except Exception:
            await self.storage.rollback()
            raise

    async def mark_sent(self, listing_id: str) -> None:
        await self.storage.execute(
            "UPDATE listings SET sent_to_telegram = 1, telegram_error = NULL WHERE id = ?",
            (listing_id,),
        )
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def mark_send_error(self, listing_id: str, error: str) -> None:
        await self.storage.execute(
            "UPDATE listings SET telegram_error = ? WHERE id = ?", (error[:1000], listing_id)
        )
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def prune_stale(self, cutoff: str) -> int:
        """Delete listings not observed since cutoff and their search links."""
        cursor = await self.storage.execute(
            "DELETE FROM listings WHERE last_seen_at < ?", (cutoff,)
        )
        if not self.storage.is_postgres:
            await self.storage.commit()
        return max(cursor.rowcount, 0)

    async def reset(self) -> None:
        await self.storage.execute("BEGIN IMMEDIATE")
        try:
            await self.storage.execute("DELETE FROM listing_searches")
            await self.storage.execute("DELETE FROM listings")
            await self.storage.execute("DELETE FROM search_state")
            await self.storage.commit()
        except Exception:
            await self.storage.rollback()
            raise

    async def close(self) -> None:
        await self.storage.close()
