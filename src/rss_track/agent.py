from __future__ import annotations

import logging
import os
import shutil
from typing import TYPE_CHECKING

from rss_track.tools import fetch_rss_entries, send_to_telegram

if TYPE_CHECKING:
    from rss_track.config import AppConfig, FeedConfig
    from rss_track.state import StateStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是一個 RSS 內容彙整助手。你會收到一批 RSS 新文章和使用者的彙整指示。

你的任務：
根據使用者的彙整指示，整理這些新文章的重點，直接輸出彙整結果。

規則：
- 使用繁體中文
- 使用 Telegram 支援的 Markdown 格式
- 保持簡潔有用，只提取關鍵資訊
- 多則內容用分隔線 (---) 隔開
- 文章內容與彙整指示不相關時跳過
- 所有文章都不相關時，回覆「無相關內容」
"""


class RSSAgent:
    def __init__(self, config: AppConfig, state: StateStore) -> None:
        self.config = config
        self.state = state
        # Use system CLI (authenticated) instead of SDK's bundled CLI
        self._cli_path = shutil.which("claude")
        # Allow SDK to spawn CLI subprocess when running inside Claude Code
        os.environ.pop("CLAUDECODE", None)

    async def check_feed(self, feed: FeedConfig) -> None:
        """Full pipeline: fetch RSS -> filter new -> summarise via SDK -> send Telegram."""
        # 1. Fetch RSS
        entries = fetch_rss_entries(feed.url, feed.max_entries_per_check)
        if not entries:
            logger.info("[%s] No entries fetched", feed.name)
            return

        # 2. Filter new
        seen_ids = self.state.get_seen_ids(feed.url)
        new_entries = [e for e in entries if e["id"] not in seen_ids]
        if not new_entries:
            logger.info("[%s] No new articles", feed.name)
            self.state.mark_checked(feed.url)
            return

        logger.info("[%s] Found %d new articles", feed.name, len(new_entries))

        # 3. Summarise via Claude Agent SDK
        user_message = self._build_user_message(feed, new_entries)
        summary = await self._get_summary(feed.name, user_message)
        if not summary:
            return  # Error logged inside; don't mark seen so we retry

        # 4. Send to Telegram
        ok = await send_to_telegram(
            self.config.telegram_bot_token, feed.telegram_chat_id, summary
        )
        if not ok:
            logger.error("[%s] Failed to send to Telegram; will retry next cycle", feed.name)
            return

        # 5. Mark seen
        for entry in new_entries:
            self.state.mark_seen(feed.url, entry["id"], entry.get("title", ""))
        self.state.mark_checked(feed.url)
        logger.info("[%s] Marked %d entries as seen", feed.name, len(new_entries))

    async def _get_summary(self, feed_name: str, user_message: str) -> str | None:
        """Call Claude via Agent SDK to generate a summary. Returns None on error."""
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            max_turns=3,
            permission_mode="bypassPermissions",
            cli_path=self._cli_path,
        )

        parts: list[str] = []
        try:
            async for message in query(prompt=user_message, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
        except Exception as e:
            error_str = str(e).lower()
            if "auth" in error_str or "token" in error_str or "login" in error_str:
                logger.error(
                    "[%s] Claude authentication expired. "
                    "Please run 'claude login' on the host machine.",
                    feed_name,
                )
                return None
            logger.warning("[%s] Agent SDK error (may have partial result): %s", feed_name, e)
            if not parts:
                return None

        summary = "\n".join(parts).strip()
        if not summary or summary == "無相關內容":
            logger.info("[%s] No relevant content to send", feed_name)
            return None

        return summary

    @staticmethod
    def _build_user_message(feed: FeedConfig, entries: list[dict[str, str]]) -> str:
        parts = [
            f"## 彙整指示\n{feed.prompt}",
            f"## 新文章（共 {len(entries)} 篇）\n",
        ]
        for i, entry in enumerate(entries, 1):
            parts.append(
                f"### 文章 {i}\n"
                f"標題: {entry['title']}\n"
                f"連結: {entry['link']}\n"
                f"摘要: {entry['summary']}\n"
                f"發布時間: {entry['published']}\n"
            )
        return "\n".join(parts)
