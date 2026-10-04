from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.models import SearchConfig
from app.storage import Storage

ALL_CATEGORY_IDS = (
    "vehicles",
    "propertyrentals",
    "apparel",
    "classifieds",
    "electronics",
    "entertainment",
    "family",
    "free",
    "garden",
    "hobbies",
    "home",
    "home-improvement",
    "musical-instruments",
    "office-supplies",
    "pets",
    "sporting-goods",
    "toys-and-games",
)


@dataclass(frozen=True, slots=True)
class MonitorPreferences:
    location: str
    radius_km: int
    min_price: int | None
    max_price: int | None
    queries: tuple[str, ...]
    categories: tuple[str, ...]

    @property
    def combination_count(self) -> int:
        return max(1, len(self.queries)) * max(1, len(self.categories))

    def searches(
        self, categories_override: tuple[str, ...] | None = None
    ) -> tuple[SearchConfig, ...]:
        queries: tuple[str | None, ...] = self.queries or (None,)
        effective_categories = (
            categories_override if categories_override is not None else self.categories
        )
        categories: tuple[str | None, ...] = effective_categories or (None,)
        result = []
        for query in queries:
            for category in categories:
                identity = "\x1f".join(
                    (
                        self.location,
                        str(self.radius_km),
                        str(self.min_price),
                        str(self.max_price),
                        query or "",
                        category or "",
                    )
                )
                digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
                result.append(
                    SearchConfig(
                        name=f"bot-{digest}",
                        query=query or "",
                        location=self.location,
                        radius_km=self.radius_km,
                        min_price=self.min_price,
                        max_price=self.max_price,
                        category_id=category,
                    )
                )
        return tuple(result)


class PreferencesStore:
    def __init__(self, location: str | Path):
        self.storage = Storage(location)

    async def open(self, defaults: SearchConfig) -> None:
        await self.storage.open()
        await self.storage.executescript(
            """
            CREATE TABLE IF NOT EXISTS monitor_preferences (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                location TEXT NOT NULL,
                radius_km INTEGER NOT NULL,
                min_price INTEGER,
                max_price INTEGER
            );
            CREATE TABLE IF NOT EXISTS monitor_queries (
                query TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS monitor_categories (
                category_id TEXT PRIMARY KEY
            );
            """
        )
        initialized = await self.storage.fetchone(
            "SELECT 1 FROM monitor_preferences WHERE singleton = 1"
        )
        if initialized is None:
            await self.storage.execute(
                """INSERT INTO monitor_preferences(
                       singleton, location, radius_km, min_price, max_price
                   ) VALUES (1, ?, ?, ?, ?)""",
                (defaults.location, defaults.radius_km, defaults.min_price, defaults.max_price),
            )
            if defaults.query:
                await self.storage.execute(
                    "INSERT INTO monitor_queries(query) VALUES (?)", (defaults.query,)
                )
            initial_categories = (
                (defaults.category_id,) if defaults.category_id else ALL_CATEGORY_IDS
            )
            await self.storage.executemany(
                """INSERT INTO monitor_categories(category_id) VALUES (?)
                   ON CONFLICT(category_id) DO NOTHING""",
                ((category_id,) for category_id in initial_categories),
            )
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def get(self) -> MonitorPreferences:
        row = await self.storage.fetchone(
            "SELECT location, radius_km, min_price, max_price FROM monitor_preferences WHERE singleton=1"
        )
        query_rows = await self.storage.fetchall(
            "SELECT query FROM monitor_queries ORDER BY LOWER(query)"
        )
        category_rows = await self.storage.fetchall(
            "SELECT category_id FROM monitor_categories ORDER BY category_id"
        )
        queries = tuple(_value(item, "query") for item in query_rows)
        categories = tuple(_value(item, "category_id") for item in category_rows)
        return MonitorPreferences(
            location=row["location"],
            radius_km=row["radius_km"],
            min_price=row["min_price"],
            max_price=row["max_price"],
            queries=queries,
            categories=categories,
        )

    async def set_location(self, location: str) -> None:
        await self._update("location", normalize_location(location))

    async def set_radius(self, radius_km: int) -> None:
        if not 1 <= radius_km <= 500:
            raise ValueError("Радиус должен быть от 1 до 500 км")
        await self._update("radius_km", radius_km)

    async def set_price(self, field: str, value: int | None) -> None:
        if field not in {"min_price", "max_price"}:
            raise ValueError("Unknown price field")
        if value is not None and value < 0:
            raise ValueError("Цена не может быть отрицательной")
        await self._update(field, value)
        preferences = await self.get()
        if (
            preferences.min_price is not None
            and preferences.max_price is not None
            and preferences.min_price > preferences.max_price
        ):
            await self._update(field, None)
            raise ValueError("Минимальная цена не может быть больше максимальной")

    async def add_queries(self, queries: list[str]) -> None:
        clean = {query.strip()[:100] for query in queries if query.strip()}
        await self.storage.executemany(
            """INSERT INTO monitor_queries(query) VALUES (?)
               ON CONFLICT(query) DO NOTHING""",
            ((q,) for q in clean),
        )
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def remove_query(self, query: str) -> None:
        await self.storage.execute("DELETE FROM monitor_queries WHERE query = ?", (query,))
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def clear_queries(self) -> None:
        await self.storage.execute("DELETE FROM monitor_queries")
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def toggle_category(self, category_id: str) -> bool:
        row = await self.storage.fetchone(
            "SELECT 1 FROM monitor_categories WHERE category_id = ?", (category_id,)
        )
        selected = row is not None
        if selected:
            await self.storage.execute(
                "DELETE FROM monitor_categories WHERE category_id = ?", (category_id,)
            )
        else:
            await self.storage.execute(
                "INSERT INTO monitor_categories(category_id) VALUES (?)", (category_id,)
            )
        if not self.storage.is_postgres:
            await self.storage.commit()
        return not selected

    async def clear_categories(self) -> None:
        await self.storage.execute("DELETE FROM monitor_categories")
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def set_all_categories(self) -> None:
        await self.storage.execute("BEGIN IMMEDIATE")
        try:
            await self.storage.execute("DELETE FROM monitor_categories")
            await self.storage.executemany(
                "INSERT INTO monitor_categories(category_id) VALUES (?)",
                ((category_id,) for category_id in ALL_CATEGORY_IDS),
            )
            await self.storage.commit()
        except Exception:
            await self.storage.rollback()
            raise

    async def _update(self, field: str, value: object) -> None:
        if field not in {"location", "radius_km", "min_price", "max_price"}:
            raise ValueError("Unsupported preference field")
        await self.storage.execute(
            f"UPDATE monitor_preferences SET {field} = ? WHERE singleton = 1", (value,)
        )
        if not self.storage.is_postgres:
            await self.storage.commit()

    async def close(self) -> None:
        await self.storage.close()


def _value(row: object, key: str) -> object:
    if isinstance(row, dict):
        return row[key]
    return row[0]


def normalize_location(value: str) -> str:
    value = value.strip()
    if "/marketplace/" in value:
        parts = urlsplit(value)
        segments = [part for part in parts.path.split("/") if part]
        try:
            value = segments[segments.index("marketplace") + 1]
        except (ValueError, IndexError) as exc:
            raise ValueError("Не удалось извлечь location из Marketplace URL") from exc
    value = value.strip("/ ")
    if not value or value in {"category", "search", "marketplace"}:
        raise ValueError("Укажите location ID, slug или Marketplace URL")
    if len(value) > 100:
        raise ValueError("Слишком длинное значение location")
    return value
