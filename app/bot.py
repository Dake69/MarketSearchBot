from __future__ import annotations

import asyncio
import hashlib
import html
import logging
from collections.abc import Callable

from app.preferences import ALL_CATEGORY_IDS, MonitorPreferences, PreferencesStore
from app.telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)

CATEGORIES = {
    "electronics": "📱 Электроника",
    "vehicles": "🚗 Транспорт",
    "propertyrentals": "🏠 Аренда жилья",
    "apparel": "👕 Одежда",
    "classifieds": "📦 Объявления",
    "entertainment": "🎮 Развлечения",
    "family": "👶 Семья",
    "free": "🆓 Бесплатно",
    "garden": "🌿 Сад",
    "hobbies": "🎨 Хобби",
    "home": "🛋 Дом",
    "home-improvement": "🔨 Ремонт",
    "musical-instruments": "🎸 Музыкальные инструменты",
    "office-supplies": "🖨 Офис",
    "pets": "🐾 Животные",
    "sporting-goods": "⚽ Спорт",
    "toys-and-games": "🧸 Игрушки",
}


class TelegramSettingsBot:
    def __init__(
        self,
        telegram: TelegramClient,
        store: PreferencesStore,
        authorized_chat_id: str,
        on_change: Callable[[], None],
    ):
        self.telegram = telegram
        self.store = store
        self.authorized_chat_id = str(authorized_chat_id)
        self.on_change = on_change
        self.offset: int | None = None
        self.input_state: dict[str, str] = {}
        self._stop = asyncio.Event()

    async def run(self) -> None:
        if not self.telegram.token or not self.authorized_chat_id:
            log.warning("Telegram settings bot disabled: credentials are missing")
            return
        try:
            await self.telegram.set_commands()
            stale = await self.telegram.get_updates(-1, timeout=0)
            if stale:
                self.offset = max(item["update_id"] for item in stale) + 1
            log.info("Telegram settings bot started")
            while not self._stop.is_set():
                try:
                    updates = await self.telegram.get_updates(self.offset, timeout=25)
                    for update in updates:
                        self.offset = update["update_id"] + 1
                        await self._handle_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Telegram bot polling failed")
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        self._stop.set()

    async def _handle_update(self, update: dict) -> None:
        callback = update.get("callback_query")
        message = update.get("message")
        source = callback.get("message", {}) if callback else message or {}
        chat_id = str(source.get("chat", {}).get("id", ""))
        if chat_id != self.authorized_chat_id:
            log.warning("Ignored Telegram update from unauthorized chat")
            return
        if callback:
            await self.telegram.answer_callback(callback["id"])
            await self._handle_callback(
                chat_id, callback.get("data", ""), source.get("message_id")
            )
        elif message:
            await self._handle_message(chat_id, message.get("text", "").strip())

    async def _handle_message(self, chat_id: str, text: str) -> None:
        if text.startswith(("/start", "/settings", "/status")):
            self.input_state.pop(chat_id, None)
            await self._show_main(chat_id)
            return
        state = self.input_state.pop(chat_id, None)
        if not state:
            await self.telegram.send_text(
                "Используйте /settings, чтобы изменить мониторинг.", chat_id=chat_id
            )
            return
        try:
            if state == "location":
                await self.store.set_location(text)
            elif state == "queries":
                await self.store.add_queries(text.splitlines())
            elif state == "radius":
                await self.store.set_radius(int(text))
            elif state in {"min_price", "max_price"}:
                value = None if text in {"-", "нет", "clear"} else int(text.replace(" ", ""))
                await self.store.set_price(state, value)
            else:
                raise ValueError("Неизвестное действие")
        except ValueError as exc:
            self.input_state[chat_id] = state
            await self.telegram.send_text(
                f"❌ {html.escape(str(exc))}\nПопробуйте ещё раз или отправьте /settings.",
                chat_id=chat_id,
            )
            return
        self.on_change()
        await self.telegram.send_text("✅ Настройка сохранена.", chat_id=chat_id)
        await self._show_main(chat_id)

    async def _handle_callback(
        self, chat_id: str, action: str, message_id: int | None
    ) -> None:
        if action in {"menu", "done"}:
            await self._show_main(chat_id, message_id)
        elif action == "location":
            await self._prompt(
                chat_id,
                "location",
                "Отправьте numeric location ID, canonical slug или полную ссылку Marketplace.\n"
                "Например для Brno: <code>107645375935528</code>.",
                message_id,
            )
        elif action == "loc_brno":
            await self.store.set_location("107645375935528")
            await self._changed(chat_id, message_id=message_id)
        elif action == "loc_prague":
            await self.store.set_location("prague")
            await self._changed(chat_id, message_id=message_id)
        elif action == "queries":
            await self._show_queries(chat_id, message_id)
        elif action == "query_add":
            await self._prompt(
                chat_id,
                "queries",
                "Отправьте один или несколько запросов, каждый с новой строки.",
                message_id,
            )
        elif action == "query_clear":
            await self.store.clear_queries()
            await self._changed(chat_id, self._show_queries, message_id)
        elif action == "all_listings":
            await self.store.clear_queries()
            await self.store.set_all_categories()
            self.on_change()
            await self._show_main(chat_id, message_id)
        elif action.startswith("query_del:"):
            key = action.split(":", 1)[1]
            preferences = await self.store.get()
            for query in preferences.queries:
                if _short_hash(query) == key:
                    await self.store.remove_query(query)
                    break
            await self._changed(chat_id, self._show_queries, message_id)
        elif action == "categories":
            await self._show_categories(chat_id, message_id)
        elif action == "category_clear":
            await self.store.set_all_categories()
            await self._changed(chat_id, self._show_categories, message_id)
        elif action.startswith("cat:"):
            category = action.split(":", 1)[1]
            if category in CATEGORIES:
                await self.store.toggle_category(category)
                self.on_change()
            await self._show_categories(chat_id, message_id)
        elif action == "prices":
            await self._show_prices(chat_id, message_id)
        elif action == "price_min":
            await self._prompt(
                chat_id,
                "min_price",
                "Введите минимальную цену или <code>-</code>.",
                message_id,
            )
        elif action == "price_max":
            await self._prompt(
                chat_id,
                "max_price",
                "Введите максимальную цену или <code>-</code>.",
                message_id,
            )
        elif action == "price_clear":
            await self.store.set_price("min_price", None)
            await self.store.set_price("max_price", None)
            await self._changed(chat_id, self._show_prices, message_id)
        elif action == "radius":
            await self._prompt(
                chat_id, "radius", "Введите радиус от 1 до 500 км.", message_id
            )

    async def _changed(self, chat_id: str, view=None, message_id: int | None = None) -> None:
        self.on_change()
        if view:
            await view(chat_id, message_id)
        else:
            await self._show_main(chat_id, message_id)

    async def _prompt(
        self, chat_id: str, state: str, text: str, message_id: int | None = None
    ) -> None:
        self.input_state[chat_id] = state
        await self._render(
            chat_id,
            text,
            _keyboard([[("↩️ Отмена", "menu")]]),
            message_id,
        )

    async def _show_main(self, chat_id: str, message_id: int | None = None) -> None:
        preferences = await self.store.get()
        await self._render(
            chat_id,
            _summary(preferences),
            _keyboard(
                [
                    [("📍 Локация", "location"), ("📏 Радиус", "radius")],
                    [("🔎 Запросы", "queries"), ("🗂 Категории", "categories")],
                    [("💰 Цена", "prices"), ("✅ Готово", "done")],
                    [("🌐 Все объявления", "all_listings")],
                    [("Brno", "loc_brno"), ("Prague", "loc_prague")],
                ]
            ),
            message_id,
        )

    async def _show_queries(self, chat_id: str, message_id: int | None = None) -> None:
        preferences = await self.store.get()
        rows = [
            [(f"❌ {query[:30]}", f"query_del:{_short_hash(query)}")]
            for query in preferences.queries
        ]
        rows.extend(
            [[("➕ Добавить", "query_add"), ("🗑 Очистить", "query_clear")], [("↩️ Назад", "menu")]]
        )
        text = "🔎 <b>Поисковые запросы</b>\n" + (
            "\n".join(f"• {html.escape(query)}" for query in preferences.queries)
            if preferences.queries
            else "Без ключевых слов"
        )
        await self._render(chat_id, text, _keyboard(rows), message_id)

    async def _show_categories(self, chat_id: str, message_id: int | None = None) -> None:
        selected = set((await self.store.get()).categories)
        buttons = []
        items = list(CATEGORIES.items())
        for index in range(0, len(items), 2):
            row = []
            for category_id, label in items[index : index + 2]:
                prefix = "✅ " if category_id in selected else "▫️ "
                row.append((prefix + label, f"cat:{category_id}"))
            buttons.append(row)
        buttons.extend([[("✅ Выбрать все", "category_clear")], [("↩️ Назад", "menu")]])
        await self._render(
            chat_id,
            "🗂 <b>Категории</b>\n"
            "По умолчанию проверяются все категории. Можно выбрать одну или несколько — "
            "тогда бот будет следить только за ними. Нажатие переключает категорию.",
            _keyboard(buttons),
            message_id,
        )

    async def _show_prices(self, chat_id: str, message_id: int | None = None) -> None:
        preferences = await self.store.get()
        await self._render(
            chat_id,
            f"💰 Цена: {_value(preferences.min_price)} — {_value(preferences.max_price)}",
            _keyboard(
                [
                    [("Минимум", "price_min"), ("Максимум", "price_max")],
                    [("🗑 Сбросить", "price_clear"), ("↩️ Назад", "menu")],
                ]
            ),
            message_id,
        )

    async def _render(
        self, chat_id: str, text: str, reply_markup: dict, message_id: int | None
    ) -> None:
        if message_id is not None:
            try:
                await self.telegram.edit_text(
                    message_id, text, chat_id=chat_id, reply_markup=reply_markup
                )
                return
            except TelegramError as exc:
                if "message is not modified" in str(exc).lower():
                    return
                log.warning("Could not edit settings message; sending a new one: %s", exc)
        await self.telegram.send_text(text, chat_id=chat_id, reply_markup=reply_markup)


def _summary(preferences: MonitorPreferences) -> str:
    queries = ", ".join(html.escape(value) for value in preferences.queries) or "без ключевых слов"
    categories = (
        ", ".join(CATEGORIES.get(value, html.escape(value)) for value in preferences.categories)
        or "все"
    )
    mode = (
        f"🌐 Все объявления — newest, ротация {len(ALL_CATEGORY_IDS)} категорий"
        if not preferences.queries and not preferences.categories
        else "🎯 Фильтрованный поиск"
    )
    return (
        "⚙️ <b>Настройки мониторинга</b>\n\n"
        f"Режим: {mode}\n"
        f"📍 Location: <code>{html.escape(preferences.location)}</code>\n"
        f"📏 Радиус: {preferences.radius_km} км\n"
        f"🔎 Запросы: {queries}\n"
        f"🗂 Категории: {categories}\n"
        f"💰 Цена: {_value(preferences.min_price)} — {_value(preferences.max_price)}\n"
        f"🔄 Комбинаций поиска: {preferences.combination_count}\n\n"
        "⏱ Повторная проверка: каждые 5 минут.\n\n"
        "Изменения применяются к следующему циклу автоматически."
    )


def _value(value: int | None) -> str:
    return str(value) if value is not None else "не задано"


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.casefold().encode()).hexdigest()[:12]


def _keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": callback} for text, callback in row] for row in rows
        ]
    }
