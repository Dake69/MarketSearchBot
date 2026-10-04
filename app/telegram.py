from __future__ import annotations

import asyncio
import html
import json
import logging
import random

import httpx

from app.models import MarketplaceListing

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


def format_listing(listing: MarketplaceListing) -> str:
    def esc(value: object) -> str:
        return html.escape(str(value), quote=False)

    lines = ["🆕 <b>Новое для мониторинга</b>", "", f"<b>{esc(listing.title)}</b>"]
    if listing.price:
        lines.append(f"💰 {esc(listing.price)}")
    if listing.location:
        lines.append(f"📍 {esc(listing.location)}")
    if listing.url:
        lines.extend(
            ("", f'🔗 <a href="{html.escape(listing.url, quote=True)}">Открыть Marketplace</a>')
        )
    return "\n".join(lines)[:1024]


class TelegramClient:
    def __init__(self, token: str, chat_id: str, timeout: float = 30):
        self.token = token
        self.chat_id = chat_id
        self.client = httpx.AsyncClient(timeout=timeout)

    async def send_listing(self, listing: MarketplaceListing) -> None:
        caption = format_listing(listing)
        if listing.image_url:
            try:
                await self._request(
                    "sendPhoto",
                    {
                        "chat_id": self.chat_id,
                        "photo": listing.image_url,
                        "caption": caption,
                        "parse_mode": "HTML",
                    },
                )
                return
            except TelegramError as exc:
                log.warning("sendPhoto failed; falling back to sendMessage: %s", exc)
        await self._request(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": caption,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )

    async def send_test(self) -> None:
        await self._request(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": "✅ Facebook Marketplace Monitor: Telegram работает.",
            },
        )

    async def send_text(
        self, text: str, *, chat_id: str | None = None, reply_markup: dict | None = None
    ) -> dict:
        data: dict[str, object] = {
            "chat_id": chat_id or self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return await self._request("sendMessage", data)

    async def edit_text(
        self,
        message_id: int,
        text: str,
        *,
        chat_id: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        data: dict[str, object] = {
            "chat_id": chat_id or self.chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return await self._request("editMessageText", data)

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        data: dict[str, object] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        if offset is not None:
            data["offset"] = offset
        result = await self._request("getUpdates", data)
        return result if isinstance(result, list) else []

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._request(
            "answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]}
        )

    async def set_commands(self) -> None:
        commands = [
            {"command": "settings", "description": "Настройки мониторинга"},
            {"command": "status", "description": "Текущие параметры"},
        ]
        await self._request("setMyCommands", {"commands": json.dumps(commands, ensure_ascii=False)})

    async def _request(self, method: str, data: dict) -> dict | list:
        if not self.token or not self.chat_id:
            raise TelegramError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        last_error = "unknown error"
        for attempt in range(4):
            try:
                response = await self.client.post(url, data=data)
                payload = response.json()
                if response.status_code == 429:
                    delay = min(float(payload.get("parameters", {}).get("retry_after", 5)), 60)
                    await asyncio.sleep(delay)
                    continue
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "Telegram server error", request=response.request, response=response
                    )
                if not response.is_success or not payload.get("ok"):
                    raise TelegramError(payload.get("description", f"HTTP {response.status_code}"))
                return payload["result"]
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = str(exc)
                if attempt == 3:
                    break
                await asyncio.sleep((2**attempt) + random.random())
        raise TelegramError(last_error)

    async def close(self) -> None:
        await self.client.aclose()
