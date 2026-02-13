from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
"""


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
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
        return {row[0] for row in cur.fetchall()}

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

    def close(self) -> None:
        self._conn.close()
