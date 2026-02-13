from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    create_sdk_mcp_server,
    query,
)

from rss_track.tools import create_telegram_tool, fetch_rss_entries

if TYPE_CHECKING:
    from rss_track.config import AppConfig, FeedConfig
    from rss_track.state import StateStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是一個 RSS 內容彙整助手。你會收到一批 RSS 新文章和使用者的彙整指示。

你的任務：
1. 根據使用者的彙整指示，整理這些新文章的重點
2. 使用 send_to_telegram 工具將彙整結果傳送到指定的 Telegram 頻道

規則：
- 使用繁體中文
- 使用 Telegram 支援的 Markdown 格式
- 保持簡潔有用，只提取關鍵資訊
- 多則內容用分隔線 (---) 隔開
- 文章內容與彙整指示不相關時跳過
- 所有文章都不相關時，不需要呼叫工具，直接說明即可
"""

_MCP_SERVER_NAME = "rss_tools"
_TOOL_NAME = "send_to_telegram"


class RSSAgent:
    def __init__(self, config: AppConfig, state: StateStore) -> None:
        self.config = config
        self.state = state
        self._telegram_tool = create_telegram_tool(config.telegram_bot_token)
        self._mcp_server = create_sdk_mcp_server(
            name=_MCP_SERVER_NAME,
            tools=[self._telegram_tool],
        )

    async def check_feed(self, feed: FeedConfig) -> None:
        """Full pipeline for a single feed: fetch -> filter -> summarise -> send."""
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

        # 3. Build prompt
        user_message = self._build_user_message(feed, new_entries)

        # 4. Call Claude via Agent SDK
        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            max_turns=10,
            mcp_servers={_MCP_SERVER_NAME: self._mcp_server},
            allowed_tools=[f"mcp__{_MCP_SERVER_NAME}__{_TOOL_NAME}"],
            permission_mode="bypassPermissions",
        )

        try:
            async for message in query(prompt=user_message, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            logger.debug("[%s] Claude: %s", feed.name, block.text[:200])
        except Exception as e:
            error_str = str(e).lower()
            if "auth" in error_str or "token" in error_str or "login" in error_str:
                logger.error(
                    "[%s] Claude authentication expired. "
                    "Please run 'claude login' on the host machine.",
                    feed.name,
                )
            else:
                logger.error("[%s] Agent SDK error: %s", feed.name, e)
            return  # Don't mark seen — retry next cycle

        # 5. Mark all new entries as seen
        for entry in new_entries:
            self.state.mark_seen(feed.url, entry["id"], entry.get("title", ""))
        self.state.mark_checked(feed.url)
        logger.info("[%s] Marked %d entries as seen", feed.name, len(new_entries))

    @staticmethod
    def _build_user_message(feed: FeedConfig, entries: list[dict[str, str]]) -> str:
        parts = [
            f"## 彙整指示\n{feed.prompt}",
            f"## Telegram 頻道\nchat_id: {feed.telegram_chat_id}",
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
