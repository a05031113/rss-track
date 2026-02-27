from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from rss_track.agent import RSSAgent
    from rss_track.config import FeedConfig
    from rss_track.state import StateStore

logger = logging.getLogger(__name__)


def _job_id(feed_id: str) -> str:
    return f"feed_{feed_id}"


def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(job_defaults={"misfire_grace_time": 600, "coalesce": True})


async def _check_feed_wrapper(agent: RSSAgent, feed: FeedConfig) -> None:
    """Async wrapper called by APScheduler jobs."""
    try:
        await agent.check_feed(feed)
    except Exception:
        logger.exception("Error checking feed: %s", feed.name)


def add_feed_job(
    scheduler: AsyncIOScheduler,
    agent: RSSAgent,
    feed_id: str,
    feed: FeedConfig,
    *,
    run_immediately: bool = False,
) -> None:
    """Register a scheduler job for the given feed."""
    import datetime

    scheduler.add_job(
        _check_feed_wrapper,
        trigger=IntervalTrigger(minutes=feed.check_interval_minutes),
        args=[agent, feed],
        id=_job_id(feed_id),
        name=f"Check {feed.name}",
        next_run_time=datetime.datetime.now(datetime.UTC) if run_immediately else None,
        replace_existing=True,
    )
    logger.info(
        "Scheduled feed '%s' every %d min (job=%s)",
        feed.name,
        feed.check_interval_minutes,
        _job_id(feed_id),
    )


def remove_feed_job(scheduler: AsyncIOScheduler, feed_id: str) -> None:
    jid = _job_id(feed_id)
    try:
        scheduler.remove_job(jid)
        logger.info("Removed job %s", jid)
    except Exception:
        logger.debug("Job %s not found, skipping removal", jid)


def reschedule_feed_job(
    scheduler: AsyncIOScheduler, feed_id: str, interval_minutes: int
) -> None:
    jid = _job_id(feed_id)
    try:
        scheduler.reschedule_job(
            jid, trigger=IntervalTrigger(minutes=interval_minutes)
        )
        logger.info("Rescheduled job %s to every %d min", jid, interval_minutes)
    except Exception:
        logger.warning("Could not reschedule job %s", jid)


def pause_feed_job(scheduler: AsyncIOScheduler, feed_id: str) -> None:
    jid = _job_id(feed_id)
    try:
        scheduler.pause_job(jid)
        logger.info("Paused job %s", jid)
    except Exception:
        logger.debug("Job %s not found for pausing", jid)


def resume_feed_job(scheduler: AsyncIOScheduler, feed_id: str) -> None:
    jid = _job_id(feed_id)
    try:
        scheduler.resume_job(jid)
        logger.info("Resumed job %s", jid)
    except Exception:
        logger.debug("Job %s not found for resuming", jid)


def add_cleanup_job(scheduler: AsyncIOScheduler, state: StateStore) -> None:
    scheduler.add_job(
        state.cleanup_old_entries,
        trigger=CronTrigger(hour=3, minute=0),
        id="cleanup",
        name="Cleanup old entries",
    )


def load_feeds_from_db(
    scheduler: AsyncIOScheduler, state: StateStore, agent: RSSAgent
) -> int:
    """Load all active feeds from DB and register scheduler jobs. Returns count."""
    feeds = state.get_active_feeds()
    for row in feeds:
        feed = state.row_to_feed_config(row)
        add_feed_job(scheduler, agent, str(row["id"]), feed)
    logger.info("Loaded %d active feeds from database", len(feeds))
    return len(feeds)
