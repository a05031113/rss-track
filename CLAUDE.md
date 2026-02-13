# RSS Telegram Agent — CLAUDE.md

## Project Overview

RSS tracking agent that periodically checks feeds, uses Claude Agent SDK to summarise articles, and sends results to Telegram channels.

## Tech Stack

- **Language**: Python 3.13
- **Package Manager**: uv
- **Agent Framework**: claude-agent-sdk (>=0.1.35) — spawns Claude Code CLI under the hood
- **RSS Parsing**: feedparser
- **HTTP Client**: httpx (async, for Telegram API)
- **Scheduler**: APScheduler 3 (BlockingScheduler)
- **State**: SQLite (WAL mode)
- **Config**: YAML (feeds.yaml) + .env (python-dotenv)

## Project Structure

```
src/rss_track/
├── __init__.py
├── __main__.py     # python -m rss_track entry
├── main.py         # CLI args + APScheduler setup
├── agent.py        # RSSAgent: orchestrates fetch → Claude → Telegram
├── tools.py        # fetch_rss_entries() + @tool send_to_telegram
├── config.py       # FeedConfig/AppConfig dataclasses + load_config()
└── state.py        # StateStore: SQLite seen_entries + feed_checks
```

## Key Architecture Decisions

- **query() not ClaudeSDKClient**: Each RSS check is a one-shot task, so we use the stateless `query()` async iterator instead of the interactive `ClaudeSDKClient`.
- **Closure pattern for tools**: `create_telegram_tool(bot_token)` uses a closure to inject the token into the `@tool` decorated function, keeping the tool signature clean for Claude.
- **MCP server naming**: Tool is registered as `mcp__rss_tools__send_to_telegram` in `allowed_tools`.
- **permission_mode="bypassPermissions"**: Required for autonomous operation without human approval.

## Claude Agent SDK API (Actual, v0.1.35)

The task.md examples use outdated API. Actual patterns:

```python
# Tool definition
@tool(name="...", description="...", input_schema=MyTypedDict)
async def my_tool(args: MyTypedDict) -> dict:
    return {"content": [{"type": "text", "text": "..."}]}

# Tool registration
server = create_sdk_mcp_server("name", tools=[my_tool])

# Query
options = ClaudeAgentOptions(
    mcp_servers={"name": server},
    allowed_tools=["mcp__name__my_tool"],
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
| LOG_LEVEL | No | Logging level, default INFO |
| DB_PATH | No | SQLite path, default data/state.db |

## Commands

```bash
uv run rss-track              # Start scheduler
uv run rss-track --once       # Run once and exit
uv run rss-track --config x   # Use custom feeds.yaml path
uv run ruff check src/        # Lint
uv run mypy src/               # Type check
```

## SQLite Schema

- `seen_entries(feed_url, entry_id, title, seen_at)` — tracks processed articles
- `feed_checks(feed_url, last_checked)` — tracks last check time per feed
- Auto-cleanup: entries older than 30 days removed daily at 03:00 UTC
