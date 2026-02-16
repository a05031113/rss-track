from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

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


def load_config() -> AppConfig:
    """Load application configuration from environment variables / .env file."""
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

    return AppConfig(
        telegram_bot_token=telegram_token,
        telegram_chat_id=default_chat_id,
        log_level=log_level,
        db_path=db_path,
    )
