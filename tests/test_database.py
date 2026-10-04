from datetime import UTC, datetime, timedelta

import pytest

from app.database import Database
from app.models import MarketplaceListing


@pytest.mark.asyncio
async def test_database_deduplicates_and_tracks_send(tmp_path) -> None:
    database = Database(tmp_path / "marketplace.db")
    await database.open()
    now = datetime.now(UTC).isoformat()
    item = MarketplaceListing("123", "Laptop", url="https://example.test/123")
    try:
        first = await database.record(item, "laptops", now, suppress=False)
        second = await database.record(item, "laptops", now, suppress=False)
        assert first.inserted and first.should_send
        assert not second.inserted and second.should_send  # pending delivery is retried
        await database.mark_sent(item.id)
        third = await database.record(item, "laptops", now, suppress=False)
        assert not third.inserted and not third.should_send
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_baseline_is_suppressed(tmp_path) -> None:
    database = Database(tmp_path / "marketplace.db")
    await database.open()
    now = datetime.now(UTC).isoformat()
    try:
        result = await database.record(MarketplaceListing("1", "Old"), "search", now, suppress=True)
        assert result.inserted and not result.should_send
        await database.mark_baseline_initialized("search", now)
        assert await database.is_baseline_initialized("search")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_prune_removes_only_listings_not_seen_before_cutoff(tmp_path) -> None:
    database = Database(tmp_path / "marketplace.db")
    await database.open()
    now = datetime.now(UTC)
    try:
        await database.record(
            MarketplaceListing("old", "Old"),
            "search",
            (now - timedelta(days=4)).isoformat(),
            suppress=False,
        )
        await database.record(
            MarketplaceListing("active", "Active"),
            "search",
            now.isoformat(),
            suppress=False,
        )

        removed = await database.prune_stale((now - timedelta(days=3)).isoformat())

        assert removed == 1
        assert not await database.contains("old")
        assert await database.contains("active")
    finally:
        await database.close()
