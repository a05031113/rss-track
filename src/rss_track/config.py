from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class FeedConfig:
    name: str
    url: str
    telegram_chat_id: str
    prompt: str
    check_interval_minutes: int = 60
    max_entries_per_check: int = 10


@dataclass
class AppConfig:
    telegram_bot_token: str
    telegram_chat_id: str = ""
    log_level: str = "INFO"
    db_path: Path = field(default_factory=lambda: Path("data/state.db"))
    feeds: list[FeedConfig] = field(default_factory=list)


def load_config(feeds_path: str = "feeds.yaml") -> AppConfig:
    """Load .env environment variables and feeds.yaml configuration."""
    load_dotenv()

    # Warn about API key billing
    if os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning(
            "ANTHROPIC_API_KEY is set — Agent SDK will use API billing instead of Max plan. "
            "Unset it to use Max plan quota."
        )

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not telegram_token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is required. Set it in .env or environment variables."
        )

    default_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    db_path = Path(os.environ.get("DB_PATH", "data/state.db"))

    # Ensure db directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Load feeds from YAML
    feeds_file = Path(feeds_path)
    if not feeds_file.exists():
        raise FileNotFoundError(f"Feeds config not found: {feeds_file}")

    with open(feeds_file, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    feeds: list[FeedConfig] = []
    for item in raw.get("feeds", []):
        chat_id = str(item.get("telegram_chat_id", "")) or default_chat_id
        if not chat_id:
            raise ValueError(
                f"Feed '{item['name']}' has no telegram_chat_id and "
                "TELEGRAM_CHAT_ID is not set in .env"
            )
        feeds.append(
            FeedConfig(
                name=item["name"],
                url=item["url"],
                telegram_chat_id=chat_id,
                prompt=item["prompt"],
                check_interval_minutes=item.get("check_interval_minutes", 60),
                max_entries_per_check=item.get("max_entries_per_check", 10),
            )
        )

    if not feeds:
        logger.warning("No feeds configured in %s", feeds_path)

    return AppConfig(
        telegram_bot_token=telegram_token,
        telegram_chat_id=default_chat_id,
        log_level=log_level,
        db_path=db_path,
        feeds=feeds,
    )
