from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from rss_track.config import FeedConfig

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_entries (
    feed_url   TEXT NOT NULL,
    entry_id   TEXT NOT NULL,
    title      TEXT,
    seen_at    TEXT NOT NULL,
    PRIMARY KEY (feed_url, entry_id)
);

CREATE TABLE IF NOT EXISTS feed_checks (
    feed_url       TEXT PRIMARY KEY,
    last_checked   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feeds (
    id                     TEXT PRIMARY KEY,
    name                   TEXT NOT NULL UNIQUE,
    url                    TEXT NOT NULL,
    telegram_chat_id       TEXT NOT NULL,
    prompt                 TEXT NOT NULL,
    check_interval_minutes INTEGER NOT NULL DEFAULT 60,
    max_entries_per_check  INTEGER NOT NULL DEFAULT 10,
    is_paused              INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
"""


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("StateStore initialized: %s", db_path)

    def is_seen(self, feed_url: str, entry_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM seen_entries WHERE feed_url = ? AND entry_id = ?",
            (feed_url, entry_id),
        )
        return cur.fetchone() is not None

    def mark_seen(self, feed_url: str, entry_id: str, title: str = "") -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_entries (feed_url, entry_id, title, seen_at) "
            "VALUES (?, ?, ?, ?)",
            (feed_url, entry_id, title, now),
        )
        self._conn.commit()

    def get_seen_ids(self, feed_url: str) -> set[str]:
        cur = self._conn.execute(
            "SELECT entry_id FROM seen_entries WHERE feed_url = ?",
            (feed_url,),
        )
        return {row["entry_id"] for row in cur.fetchall()}

    def mark_checked(self, feed_url: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO feed_checks (feed_url, last_checked) VALUES (?, ?)",
            (feed_url, now),
        )
        self._conn.commit()

    def cleanup_old_entries(self, days: int = 30) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        cur = self._conn.execute(
            "DELETE FROM seen_entries WHERE seen_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        deleted = cur.rowcount
        if deleted > 0:
            logger.info("Cleaned up %d entries older than %d days", deleted, days)
        return deleted

    # ------------------------------------------------------------------
    # Feeds CRUD
    # ------------------------------------------------------------------

    def add_feed(
        self,
        name: str,
        url: str,
        chat_id: str,
        prompt: str,
        interval: int = 60,
        max_entries: int = 10,
        feed_id: str | None = None,
    ) -> str:
        """Insert a new feed. Returns the generated feed ID."""
        fid = feed_id or uuid4().hex[:12]
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT INTO feeds "
            "(id, name, url, telegram_chat_id, prompt, "
            "check_interval_minutes, max_entries_per_check, is_paused, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (fid, name, url, chat_id, prompt, interval, max_entries, now, now),
        )
        self._conn.commit()
        logger.info("Added feed '%s' (id=%s)", name, fid)
        return fid

    def get_feed(self, feed_id: str) -> dict[str, object] | None:
        cur = self._conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_feed_by_name(self, name: str) -> dict[str, object] | None:
        cur = self._conn.execute("SELECT * FROM feeds WHERE name = ?", (name,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_feeds(self) -> list[dict[str, object]]:
        cur = self._conn.execute("SELECT * FROM feeds ORDER BY created_at")
        return [dict(row) for row in cur.fetchall()]

    def get_active_feeds(self) -> list[dict[str, object]]:
        cur = self._conn.execute(
            "SELECT * FROM feeds WHERE is_paused = 0 ORDER BY created_at"
        )
        return [dict(row) for row in cur.fetchall()]

    def update_feed(self, feed_id: str, **kwargs: object) -> bool:
        """Update feed fields. Allowed keys: name, url, telegram_chat_id, prompt,
        check_interval_minutes, max_entries_per_check."""
        allowed = {
            "name", "url", "telegram_chat_id", "prompt",
            "check_interval_minutes", "max_entries_per_check",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(UTC).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [feed_id]
        self._conn.execute(f"UPDATE feeds SET {set_clause} WHERE id = ?", values)  # noqa: S608
        self._conn.commit()
        return True

    def delete_feed(self, feed_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def set_feed_paused(self, feed_id: str, paused: bool) -> bool:
        now = datetime.now(UTC).isoformat()
        cur = self._conn.execute(
            "UPDATE feeds SET is_paused = ?, updated_at = ? WHERE id = ?",
            (1 if paused else 0, now, feed_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def row_to_feed_config(row: dict[str, object]) -> FeedConfig:
        return FeedConfig(
            name=str(row["name"]),
            url=str(row["url"]),
            telegram_chat_id=str(row["telegram_chat_id"]),
            prompt=str(row["prompt"]),
            check_interval_minutes=int(row["check_interval_minutes"]),  # type: ignore[arg-type]
            max_entries_per_check=int(row["max_entries_per_check"]),  # type: ignore[arg-type]
        )

    def close(self) -> None:
        self._conn.close()
