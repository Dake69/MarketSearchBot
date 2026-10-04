from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from app.config import Settings
from app.database import Database
from app.logging_config import configure_logging
from app.marketplace.auto import AutoMarketplaceProvider
from app.telegram import TelegramClient
from app.worker import Worker

log = logging.getLogger(__name__)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Facebook Marketplace → Telegram monitor")
    result.add_argument("--once", action="store_true", help="run one polling cycle and exit")
    result.add_argument(
        "--dry-run", action="store_true", help="log results without DB/Telegram writes"
    )
    result.add_argument(
        "--test-marketplace", action="store_true", help="probe and print provider mode"
    )
    result.add_argument("--test-telegram", action="store_true", help="send one test message")
    result.add_argument(
        "--reset-baseline", action="store_true", help="clear listing history and baseline"
    )
    return result


async def test_marketplace(settings: Settings) -> int:
    provider = AutoMarketplaceProvider(settings)
    try:
        listings = await provider.get_listings(settings.searches[0])
        print(f"Marketplace mode: {provider.name}")
        print(f"Listings found: {len(listings)}")
        for item in listings[:3]:
            print(f"- {item.id}: {item.title} — {item.url}")
        return 0
    except Exception as exc:
        print("Marketplace anonymous access unavailable or unusable.", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("Login may be required: python -m app.login", file=sys.stderr)
        return 2
    finally:
        await provider.close()


async def reset_baseline(settings: Settings) -> int:
    database = Database(settings.storage_location)
    await database.open()
    try:
        await database.reset()
        backend = "PostgreSQL" if settings.database_url else str(settings.database_path)
        print(f"Baseline and listing history cleared: {backend}")
        return 0
    finally:
        await database.close()


async def run_worker(settings: Settings, once: bool, dry_run: bool) -> int:
    worker = Worker(settings, dry_run=dry_run, enable_bot=not once and not dry_run)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await worker.start()
    try:
        while not stop.is_set():
            worker.config_changed.clear()
            started = loop.time()
            new_count, sent_count = await worker.cycle()
            if once:
                print(
                    f"Cycle complete: new={new_count}, sent={sent_count}, provider={worker.provider.name}"
                )
                break
            remaining = max(0, settings.poll_interval_seconds - (loop.time() - started))
            stop_task = asyncio.create_task(stop.wait())
            change_task = asyncio.create_task(worker.config_changed.wait())
            done, pending = await asyncio.wait(
                {stop_task, change_task}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if change_task in done and worker.config_changed.is_set():
                log.info("Marketplace configuration changed; starting a new cycle")
        return 0
    finally:
        await worker.close()


async def async_main() -> int:
    args = parser().parse_args()
    try:
        settings = Settings.load()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    if args.test_marketplace:
        return await test_marketplace(settings)
    if args.test_telegram:
        telegram = TelegramClient(
            settings.telegram_bot_token, settings.telegram_chat_id, settings.http_timeout_seconds
        )
        try:
            await telegram.send_test()
            print("Telegram test message sent.")
            return 0
        finally:
            await telegram.close()
    if args.reset_baseline:
        return await reset_baseline(settings)
    return await run_worker(settings, args.once, args.dry_run)


def main() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
