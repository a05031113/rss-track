from __future__ import annotations

import argparse
import asyncio
import logging
import signal

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
    args = parser.parse_args()

    config = load_config()
    _setup_logging(config.log_level)

    if args.once:
        asyncio.run(run_once(config))
    else:
        asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
