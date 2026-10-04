from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from app.config import Settings


async def login() -> None:
    settings = Settings.load()
    settings.browser_profile_path.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(settings.browser_profile_path),
            headless=False,
            locale=settings.browser_locale,
            viewport={"width": 1365, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded")
        print("Войдите в Facebook вручную в открывшемся окне.")
        print("Убедитесь, что Marketplace показывает карточки, затем вернитесь сюда.")
        await asyncio.to_thread(input, "Нажмите Enter, чтобы сохранить профиль и закрыть браузер: ")
        await context.close()
    print(f"Профиль сохранён: {settings.browser_profile_path}")


if __name__ == "__main__":
    asyncio.run(login())
