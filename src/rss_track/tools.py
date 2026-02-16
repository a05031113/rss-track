from __future__ import annotations

import logging
import re

import feedparser
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RSS fetch
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MAX_SUMMARY_LEN = 3000


def fetch_rss_entries(url: str, max_entries: int = 10) -> list[dict[str, str]]:
    """Parse an RSS feed and return a list of entry dicts.

    Each dict has keys: id, title, link, summary, published.
    HTML tags are stripped from summary and it is truncated to 3000 chars.
    """
    try:
        feed = feedparser.parse(url)
    except Exception:
        logger.exception("Failed to parse RSS feed: %s", url)
        return []

    if feed.bozo and not feed.entries:
        logger.warning("RSS feed returned no entries (bozo): %s", url)
        return []

    entries: list[dict[str, str]] = []
    for entry in feed.entries[:max_entries]:
        entry_id = (
            getattr(entry, "id", "") or getattr(entry, "link", "") or entry.get("title", "")
        )

        raw_summary = ""
        if hasattr(entry, "content") and entry.content:
            raw_summary = entry.content[0].get("value", "")
        elif hasattr(entry, "summary"):
            raw_summary = entry.summary
        elif hasattr(entry, "description"):
            raw_summary = entry.description

        summary = _HTML_TAG_RE.sub("", raw_summary).strip()
        if len(summary) > _MAX_SUMMARY_LEN:
            summary = summary[:_MAX_SUMMARY_LEN] + "..."

        entries.append(
            {
                "id": str(entry_id),
                "title": getattr(entry, "title", ""),
                "link": getattr(entry, "link", ""),
                "summary": summary,
                "published": getattr(entry, "published", ""),
            }
        )

    return entries


# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

_TELEGRAM_MSG_LIMIT = 4096


def _split_message(text: str, limit: int = _TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split a long message at newline boundaries."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts


async def send_to_telegram(
    bot_token: str, chat_id: str, message: str
) -> bool:
    """Send a message to Telegram. Returns True on success."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    parts = _split_message(message)

    async with httpx.AsyncClient(timeout=30) as client:
        for part in parts:
            payload: dict[str, object] = {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            try:
                resp = await client.post(url, json=payload)
            except httpx.HTTPError:
                logger.exception("Telegram HTTP request failed")
                return False

            if resp.status_code != 200 or not resp.json().get("ok"):
                # Fallback to plain text
                logger.warning("Markdown failed, falling back to plain text")
                payload.pop("parse_mode")
                try:
                    resp = await client.post(url, json=payload)
                except httpx.HTTPError:
                    logger.exception("Telegram HTTP request failed (plain text)")
                    return False

                if resp.status_code != 200 or not resp.json().get("ok"):
                    logger.error("Telegram API error: %s", resp.text)
                    return False

    return True
