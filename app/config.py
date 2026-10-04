from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from app.models import SearchConfig


def _bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
        raise ValueError(f"Invalid boolean value: {value}")
    return normalized in {"1", "true", "yes", "on"}


def _optional(value: Any, cast):
    return None if value is None or str(value).strip() == "" else cast(value)


@dataclass(frozen=True, slots=True)
class Settings:
    searches: tuple[SearchConfig, ...]
    poll_interval_seconds: int = 300
    send_existing_on_first_run: bool = False
    marketplace_mode: str = "auto"
    headless: bool = True
    browser_locale: str = "en-US"
    http_timeout_seconds: float = 30
    page_timeout_seconds: int = 45_000
    max_listings_per_search: int = 60
    all_categories_per_cycle: int = 17
    listing_retention_days: int = 3
    jitter_seconds: float = 5
    database_path: Path = Path("data/marketplace.db")
    database_url: str = ""
    browser_profile_path: Path = Path("data/browser-profile")
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    log_level: str = "INFO"

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> Settings:
        load_dotenv(env_file, override=False)
        searches_file = Path(os.getenv("SEARCHES_FILE", "searches.yaml"))
        searches = _load_searches(searches_file) if searches_file.exists() else (_env_search(),)
        if not searches:
            raise ValueError("At least one search must be configured")
        names = [s.name for s in searches]
        if len(names) != len(set(names)):
            raise ValueError("Search names must be unique")
        interval = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
        if interval < 60:
            raise ValueError("POLL_INTERVAL_SECONDS must be at least 60")
        retention_days = int(os.getenv("LISTING_RETENTION_DAYS", "3"))
        if retention_days < 1:
            raise ValueError("LISTING_RETENTION_DAYS must be at least 1")
        mode = os.getenv("MARKETPLACE_MODE", "auto").lower()
        if mode not in {
            "auto",
            "anonymous_http",
            "anonymous_playwright",
            "authenticated_playwright",
        }:
            raise ValueError(f"Unsupported MARKETPLACE_MODE: {mode}")
        return cls(
            searches=tuple(searches),
            poll_interval_seconds=interval,
            send_existing_on_first_run=_bool(os.getenv("SEND_EXISTING_ON_FIRST_RUN")),
            marketplace_mode=mode,
            headless=_bool(os.getenv("HEADLESS"), True),
            browser_locale=os.getenv("BROWSER_LOCALE", "en-US"),
            http_timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            page_timeout_seconds=int(os.getenv("PAGE_TIMEOUT_SECONDS", "45000")),
            max_listings_per_search=int(os.getenv("MAX_LISTINGS_PER_SEARCH", "60")),
            all_categories_per_cycle=max(
                1, min(int(os.getenv("ALL_CATEGORIES_PER_CYCLE", "17")), 17)
            ),
            listing_retention_days=retention_days,
            jitter_seconds=float(os.getenv("JITTER_SECONDS", "5")),
            database_path=Path(os.getenv("DATABASE_PATH", "data/marketplace.db")),
            database_url=os.getenv("DATABASE_URL", ""),
            browser_profile_path=Path(os.getenv("BROWSER_PROFILE_PATH", "data/browser-profile")),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    @property
    def storage_location(self) -> str | Path:
        return self.database_url or self.database_path


def _env_search() -> SearchConfig:
    return SearchConfig(
        name="default",
        query=os.getenv("SEARCH_QUERY", ""),
        location=os.getenv("FB_MARKETPLACE_LOCATION", ""),
        latitude=_optional(os.getenv("FB_MARKETPLACE_LAT"), float),
        longitude=_optional(os.getenv("FB_MARKETPLACE_LON"), float),
        radius_km=int(os.getenv("FB_MARKETPLACE_RADIUS_KM", "50")),
        min_price=_optional(os.getenv("MIN_PRICE"), int),
        max_price=_optional(os.getenv("MAX_PRICE"), int),
        category_id=_optional(os.getenv("FB_CATEGORY_ID"), str),
    )


def _load_searches(path: Path) -> tuple[SearchConfig, ...]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = document.get("searches")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: top-level 'searches' must be a list")
    result = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            raise ValueError(f"{path}: every search needs a name")
        result.append(
            SearchConfig(
                name=str(row["name"]),
                query=str(row.get("query", "")),
                location=str(row.get("location", "")),
                latitude=_optional(row.get("lat", row.get("latitude")), float),
                longitude=_optional(row.get("lon", row.get("longitude")), float),
                radius_km=int(row.get("radius_km", 50)),
                min_price=_optional(row.get("min_price"), int),
                max_price=_optional(row.get("max_price"), int),
                category_id=_optional(row.get("category_id"), str),
                days_since_listed=_optional(row.get("days_since_listed", 1), int),
            )
        )
    return tuple(result)
