# RSS Telegram Agent — CLAUDE.md

## Project Overview

RSS tracking agent that periodically checks feeds, uses Claude Agent SDK to summarise articles, and sends results to Telegram channels. Feed management is done dynamically via Telegram bot commands — no config files needed.

## Status

- **Implementation**: Dynamic Telegram bot management complete
- **Tests**: Import verification + StateStore unit tests passing
- **Not yet verified**: End-to-end test with real RSS feed + Telegram bot commands

## Tech Stack

- **Language**: Python 3.13
- **Package Manager**: uv
- **Agent Framework**: claude-agent-sdk (>=0.1.35) — spawns Claude Code CLI under the hood
- **RSS Parsing**: feedparser
- **HTTP Client**: httpx (async, for Telegram API direct calls)
- **Bot Framework**: python-telegram-bot (>=21.0, async)
- **Scheduler**: APScheduler 3 (AsyncIOScheduler)
- **State**: SQLite (WAL mode) — stores both feed configs and seen entries
- **Config**: .env only (python-dotenv)

## Project Structure

```
rss-track/
├── pyproject.toml
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── compose.yaml
├── CLAUDE.md
├── README.md
├── task.md
└── src/
    └── rss_track/
        ├── __init__.py
        ├── __main__.py     # python -m rss_track entry
        ├── main.py         # CLI args + async bot/scheduler setup
        ├── bot.py          # Telegram bot command handlers
        ├── scheduler.py    # AsyncIOScheduler helpers
        ├── agent.py        # RSSAgent: fetch → Claude → Telegram
        ├── tools.py        # fetch_rss_entries() + send_to_telegram()
        ├── config.py       # FeedConfig/AppConfig dataclasses + load_config()
        └── state.py        # StateStore: SQLite feeds + seen_entries + feed_checks
```

## Architecture

```
Before:  feeds.yaml → config.py → BlockingScheduler (static)
After:   Telegram Bot → SQLite → AsyncIOScheduler (dynamic)
```

- Bot and scheduler share one asyncio event loop (no thread-safety concerns)
- Feed configs stored in SQLite `feeds` table, managed via bot commands
- `agent.py` and `tools.py` are unchanged — they accept `FeedConfig` regardless of source

## Key Architecture Decisions

- **AsyncIOScheduler + python-telegram-bot**: Both async, share one event loop
- **query() not ClaudeSDKClient**: Each RSS check is a stateless one-shot task
- **ConversationHandler for multi-step flows**: `/new` and `/edit` use step-by-step prompts
- **permission_mode="bypassPermissions"**: Required for autonomous operation
- **Shared state via bot_data**: scheduler, state, agent, config accessible from all handlers

## Claude Agent SDK API (Actual, v0.1.35)

```python
options = ClaudeAgentOptions(
    system_prompt=SYSTEM_PROMPT,
    max_turns=3,
    permission_mode="bypassPermissions",
    cli_path=shutil.which("claude"),
)
async for msg in query(prompt="...", options=options):
    ...
```

## Authentication

- Uses Claude Max plan via Claude Code CLI OAuth (no API key)
- **Do NOT set ANTHROPIC_API_KEY** — it switches to per-token billing
- Docker mounts `~/.config/claude-code/auth.json` read-only

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| TELEGRAM_BOT_TOKEN | Yes | Telegram bot token from @BotFather |
| TELEGRAM_CHAT_ID | No | Default chat ID for new feeds |
| LOG_LEVEL | No | Logging level, default INFO |
| DB_PATH | No | SQLite path, default data/state.db |

## Commands

```bash
uv run rss-track              # Start bot + scheduler
uv run rss-track --once       # Check all active feeds once (from DB) and exit
uv run ruff check src/        # Lint
uv run mypy src/              # Type check
```

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome + command list |
| `/new` | Add new RSS feed (interactive) |
| `/list` | List all feeds |
| `/edit <name>` | Edit feed settings |
| `/delete <name>` | Delete feed |
| `/pause <name>` | Pause feed |
| `/resume <name>` | Resume feed |
| `/check <name>` | Check feed immediately |
| `/cancel` | Cancel current operation |

## SQLite Schema

- `feeds(id, name, url, telegram_chat_id, prompt, check_interval_minutes, max_entries_per_check, is_paused, created_at, updated_at)` — feed configurations
- `seen_entries(feed_url, entry_id, title, seen_at)` — tracks processed articles
- `feed_checks(feed_url, last_checked)` — tracks last check time per feed
- Auto-cleanup: entries older than 30 days removed daily at 03:00 UTC
