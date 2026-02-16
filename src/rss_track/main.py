from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from rss_track.agent import RSSAgent
from rss_track.config import AppConfig, load_config
from rss_track.scheduler import (
    add_cleanup_job,
    create_scheduler,
    load_feeds_from_db,
)
from rss_track.state import StateStore

logger = logging.getLogger("rss_track")

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def _setup_logging(level: str) -> None:
    logging.basicConfig(format=LOG_FORMAT, level=getattr(logging, level.upper(), logging.INFO))


# ---------------------------------------------------------------------------
# --once: run all active feeds once and exit
# ---------------------------------------------------------------------------


async def run_once(config: AppConfig) -> None:
    state = StateStore(config.db_path)
    agent = RSSAgent(config, state)

    feeds = state.get_active_feeds()
    logger.info("Running one-time check for %d feeds", len(feeds))
    for row in feeds:
        feed = state.row_to_feed_config(row)
        try:
            await agent.check_feed(feed)
        except Exception:
            logger.exception("Error checking feed: %s", feed.name)

    state.close()
    logger.info("One-time check complete")


# ---------------------------------------------------------------------------
# --migrate: import feeds.yaml into SQLite
# ---------------------------------------------------------------------------


def run_migrate(config: AppConfig, feeds_path: str) -> None:
    import yaml

    feeds_file = Path(feeds_path)
    if not feeds_file.exists():
        logger.error("Feeds config not found: %s", feeds_file)
        sys.exit(1)

    with open(feeds_file, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    state = StateStore(config.db_path)
    default_chat_id = config.telegram_chat_id
    count = 0

    for item in raw.get("feeds", []):
        name = item["name"]
        if state.get_feed_by_name(name):
            logger.info("Feed '%s' already exists, skipping", name)
            continue

        chat_id = str(item.get("telegram_chat_id", "")) or default_chat_id
        if not chat_id:
            logger.warning("Feed '%s' has no chat_id, skipping", name)
            continue

        state.add_feed(
            name=name,
            url=item["url"],
            chat_id=chat_id,
            prompt=item["prompt"],
            interval=item.get("check_interval_minutes", 60),
            max_entries=item.get("max_entries_per_check", 10),
        )
        count += 1

    state.close()
    logger.info("Migrated %d feeds from %s to database", count, feeds_path)


# ---------------------------------------------------------------------------
# Default: bot + scheduler
# ---------------------------------------------------------------------------


async def run_bot(config: AppConfig) -> None:
    from telegram.ext import Application

    from rss_track.bot import register_handlers

    state = StateStore(config.db_path)
    agent = RSSAgent(config, state)
    scheduler = create_scheduler()

    # Load feeds from DB and register scheduler jobs
    count = load_feeds_from_db(scheduler, state, agent)
    add_cleanup_job(scheduler, state)
    scheduler.start()

    # Initial check for all active feeds
    logger.info("Running initial check for %d feeds...", count)
    for row in state.get_active_feeds():
        feed = state.row_to_feed_config(row)
        try:
            await agent.check_feed(feed)
        except Exception:
            logger.exception("Error during initial check: %s", feed.name)

    # Build Telegram bot application
    app = Application.builder().token(config.telegram_bot_token).build()
    app.bot_data.update(
        scheduler=scheduler,
        state=state,
        agent=agent,
        config=config,
    )
    register_handlers(app)

    # Start bot polling
    async with app:
        await app.start()
        await app.updater.start_polling()  # type: ignore[union-attr]

        # Wait for shutdown signal
        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        logger.info("Bot is running. Press Ctrl+C to stop.")
        await stop_event.wait()

        logger.info("Shutting down...")
        await app.updater.stop()  # type: ignore[union-attr]
        await app.stop()

    scheduler.shutdown()
    state.close()
    logger.info("Shutdown complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="RSS Telegram Agent")
    parser.add_argument("--once", action="store_true", help="Check all feeds once and exit")
    parser.add_argument("--migrate", action="store_true", help="Migrate feeds.yaml to database")
    parser.add_argument(
        "--config", default="feeds.yaml", help="Path to feeds.yaml (for --migrate)"
    )
    args = parser.parse_args()

    config = load_config()
    _setup_logging(config.log_level)

    if args.migrate:
        run_migrate(config, args.config)
    elif args.once:
        asyncio.run(run_once(config))
    else:
        asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
