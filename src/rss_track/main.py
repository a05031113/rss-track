from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import NoReturn

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from rss_track.agent import RSSAgent
from rss_track.config import AppConfig, FeedConfig, load_config
from rss_track.state import StateStore

logger = logging.getLogger("rss_track")

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def _setup_logging(level: str) -> None:
    logging.basicConfig(format=LOG_FORMAT, level=getattr(logging, level.upper(), logging.INFO))


def _run_check_feed(agent: RSSAgent, feed: FeedConfig) -> None:
    """Wrapper that runs the async check_feed in a new event loop."""
    asyncio.run(agent.check_feed(feed))


def run_once(config: AppConfig) -> None:
    """Check all feeds once and exit."""
    state = StateStore(config.db_path)
    agent = RSSAgent(config, state)

    logger.info("Running one-time check for %d feeds", len(config.feeds))
    for feed in config.feeds:
        try:
            _run_check_feed(agent, feed)
        except Exception:
            logger.exception("Error checking feed: %s", feed.name)

    state.close()
    logger.info("One-time check complete")


def run_scheduler(config: AppConfig) -> NoReturn:
    """Start the APScheduler blocking scheduler."""
    state = StateStore(config.db_path)
    agent = RSSAgent(config, state)
    scheduler = BlockingScheduler()

    # Register each feed with its own interval
    for feed in config.feeds:
        scheduler.add_job(
            _run_check_feed,
            trigger=IntervalTrigger(minutes=feed.check_interval_minutes),
            args=[agent, feed],
            id=f"feed_{feed.name}",
            name=f"Check {feed.name}",
            next_run_time=None,  # We'll run immediately below
        )
        logger.info(
            "Scheduled feed '%s' every %d minutes",
            feed.name,
            feed.check_interval_minutes,
        )

    # Daily cleanup at 03:00 UTC
    scheduler.add_job(
        state.cleanup_old_entries,
        trigger=CronTrigger(hour=3, minute=0),
        id="cleanup",
        name="Cleanup old entries",
    )

    # Graceful shutdown
    def _shutdown(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down...", sig_name)
        scheduler.shutdown(wait=False)
        state.close()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Run all feeds immediately on startup
    logger.info("Running initial check for all feeds...")
    for feed in config.feeds:
        try:
            _run_check_feed(agent, feed)
        except Exception:
            logger.exception("Error during initial check: %s", feed.name)

    logger.info("Starting scheduler...")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass

    state.close()
    sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="RSS Telegram Agent")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--config", default="feeds.yaml", help="Path to feeds.yaml")
    args = parser.parse_args()

    config = load_config(feeds_path=args.config)
    _setup_logging(config.log_level)

    if args.once:
        run_once(config)
    else:
        run_scheduler(config)


if __name__ == "__main__":
    main()
