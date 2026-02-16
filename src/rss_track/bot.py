"""Telegram Bot handlers for dynamic RSS feed management."""

from __future__ import annotations

import logging
from typing import Any

import feedparser
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from rss_track.config import FeedConfig
from rss_track.scheduler import (
    add_feed_job,
    pause_feed_job,
    remove_feed_job,
    reschedule_feed_job,
    resume_feed_job,
)

logger = logging.getLogger(__name__)

# ConversationHandler states for /new
NEW_URL, NEW_INTERVAL, NEW_PROMPT = range(3)

# ConversationHandler states for /edit
EDIT_FIELD, EDIT_VALUE = range(10, 12)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bot_data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    assert context.bot_data is not None
    return context.bot_data


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    await update.message.reply_text(
        "👋 RSS 追蹤機器人\n\n"
        "指令列表：\n"
        "/new — 新增 RSS feed\n"
        "/list — 列出所有 feeds\n"
        "/edit <名稱> — 修改 feed 設定\n"
        "/delete <名稱> — 刪除 feed\n"
        "/pause <名稱> — 暫停 feed\n"
        "/resume <名稱> — 恢復 feed\n"
        "/check <名稱> — 立即檢查一次\n"
        "/cancel — 取消當前操作"
    )


# ---------------------------------------------------------------------------
# /new conversation
# ---------------------------------------------------------------------------


async def new_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None
    await update.message.reply_text("請輸入 RSS feed URL：")
    return NEW_URL


async def new_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None and update.message.text is not None
    assert context.user_data is not None
    url = update.message.text.strip()

    # Validate feed
    feed = feedparser.parse(url)
    if feed.bozo and not feed.entries:
        await update.message.reply_text("❌ 無法解析此 URL，請確認是有效的 RSS feed。")
        return NEW_URL

    title = feed.feed.get("title", url) if hasattr(feed, "feed") else url
    context.user_data["new_url"] = url
    context.user_data["new_name"] = title

    await update.message.reply_text(
        f"✅ 偵測到 feed: \"{title}\"\n\n"
        "請輸入檢查間隔（分鐘），預設 60："
    )
    return NEW_INTERVAL


async def new_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None and update.message.text is not None
    assert context.user_data is not None
    text = update.message.text.strip()

    try:
        interval = int(text) if text else 60
        if interval < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ 請輸入正整數，或直接發送空白使用預設值 60。")
        return NEW_INTERVAL

    context.user_data["new_interval"] = interval
    await update.message.reply_text(
        "請輸入彙整 prompt（Claude 會依此摘要文章）："
    )
    return NEW_PROMPT


async def new_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None and update.message.text is not None
    assert context.user_data is not None
    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("❌ Prompt 不能為空，請重新輸入。")
        return NEW_PROMPT

    data = _bot_data(context)
    state = data["state"]
    scheduler = data["scheduler"]
    agent = data["agent"]
    config = data["config"]

    name: str = context.user_data["new_name"]
    url: str = context.user_data["new_url"]
    interval: int = context.user_data["new_interval"]
    chat_id = str(update.message.chat_id)
    default_chat_id = config.telegram_chat_id
    target_chat_id = default_chat_id or chat_id

    # Check duplicate name
    if state.get_feed_by_name(name):
        await update.message.reply_text(
            f"❌ 已存在同名 feed「{name}」，請先刪除或使用 /edit 修改。"
        )
        return ConversationHandler.END

    feed_id = state.add_feed(
        name=name,
        url=url,
        chat_id=target_chat_id,
        prompt=prompt,
        interval=interval,
    )
    feed = FeedConfig(
        name=name,
        url=url,
        telegram_chat_id=target_chat_id,
        prompt=prompt,
        check_interval_minutes=interval,
    )
    add_feed_job(scheduler, agent, feed_id, feed, run_immediately=True)

    await update.message.reply_text(
        f"✅ Feed 已新增！\n\n"
        f"名稱: {name}\n"
        f"URL: {url}\n"
        f"間隔: 每 {interval} 分鐘\n"
        f"即將執行第一次檢查..."
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /list
# ---------------------------------------------------------------------------


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    state = _bot_data(context)["state"]
    feeds = state.list_feeds()

    if not feeds:
        await update.message.reply_text("目前沒有任何 feed。使用 /new 新增一個。")
        return

    lines: list[str] = []
    for f in feeds:
        status = "⏸ 暫停" if f["is_paused"] else "✅ 啟用"
        lines.append(
            f"• {f['name']} [{status}]\n"
            f"  URL: {f['url']}\n"
            f"  間隔: {f['check_interval_minutes']} 分鐘"
        )
    await update.message.reply_text("\n\n".join(lines))


# ---------------------------------------------------------------------------
# /delete <name>
# ---------------------------------------------------------------------------


async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    assert context.args is not None
    if not context.args:
        await update.message.reply_text("用法: /delete <feed 名稱>")
        return

    name = " ".join(context.args)
    data = _bot_data(context)
    state = data["state"]
    scheduler = data["scheduler"]

    feed = state.get_feed_by_name(name)
    if not feed:
        await update.message.reply_text(f"❌ 找不到名為「{name}」的 feed。")
        return

    remove_feed_job(scheduler, str(feed["id"]))
    state.delete_feed(str(feed["id"]))
    await update.message.reply_text(f"✅ 已刪除 feed「{name}」。")


# ---------------------------------------------------------------------------
# /pause <name>
# ---------------------------------------------------------------------------


async def pause_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    assert context.args is not None
    if not context.args:
        await update.message.reply_text("用法: /pause <feed 名稱>")
        return

    name = " ".join(context.args)
    data = _bot_data(context)
    state = data["state"]
    scheduler = data["scheduler"]

    feed = state.get_feed_by_name(name)
    if not feed:
        await update.message.reply_text(f"❌ 找不到名為「{name}」的 feed。")
        return

    state.set_feed_paused(str(feed["id"]), paused=True)
    pause_feed_job(scheduler, str(feed["id"]))
    await update.message.reply_text(f"⏸ 已暫停 feed「{name}」。使用 /resume {name} 恢復。")


# ---------------------------------------------------------------------------
# /resume <name>
# ---------------------------------------------------------------------------


async def resume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    assert context.args is not None
    if not context.args:
        await update.message.reply_text("用法: /resume <feed 名稱>")
        return

    name = " ".join(context.args)
    data = _bot_data(context)
    state = data["state"]
    scheduler = data["scheduler"]

    feed = state.get_feed_by_name(name)
    if not feed:
        await update.message.reply_text(f"❌ 找不到名為「{name}」的 feed。")
        return

    state.set_feed_paused(str(feed["id"]), paused=False)
    resume_feed_job(scheduler, str(feed["id"]))
    await update.message.reply_text(f"▶️ 已恢復 feed「{name}」。")


# ---------------------------------------------------------------------------
# /check <name>
# ---------------------------------------------------------------------------


async def check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    assert context.args is not None
    if not context.args:
        await update.message.reply_text("用法: /check <feed 名稱>")
        return

    name = " ".join(context.args)
    data = _bot_data(context)
    state = data["state"]
    agent = data["agent"]

    row = state.get_feed_by_name(name)
    if not row:
        await update.message.reply_text(f"❌ 找不到名為「{name}」的 feed。")
        return

    await update.message.reply_text(f"🔄 正在檢查「{name}」...")
    feed = state.row_to_feed_config(row)
    try:
        await agent.check_feed(feed)
        await update.message.reply_text(f"✅ 「{name}」檢查完成。")
    except Exception:
        logger.exception("Manual check failed: %s", name)
        await update.message.reply_text(f"❌ 檢查「{name}」時發生錯誤。")


# ---------------------------------------------------------------------------
# /edit conversation
# ---------------------------------------------------------------------------


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None
    assert context.args is not None
    assert context.user_data is not None
    if not context.args:
        await update.message.reply_text("用法: /edit <feed 名稱>")
        return ConversationHandler.END

    name = " ".join(context.args)
    state = _bot_data(context)["state"]
    row = state.get_feed_by_name(name)
    if not row:
        await update.message.reply_text(f"❌ 找不到名為「{name}」的 feed。")
        return ConversationHandler.END

    context.user_data["edit_feed_id"] = row["id"]
    context.user_data["edit_feed_name"] = row["name"]

    await update.message.reply_text(
        f"目前設定：\n"
        f"URL: {row['url']}\n"
        f"間隔: {row['check_interval_minutes']} 分鐘\n"
        f"Prompt: {row['prompt']}\n\n"
        f"要修改哪個？請輸入: url / interval / prompt"
    )
    return EDIT_FIELD


async def edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None and update.message.text is not None
    assert context.user_data is not None
    field = update.message.text.strip().lower()

    if field not in ("url", "interval", "prompt"):
        await update.message.reply_text("請輸入 url、interval 或 prompt。")
        return EDIT_FIELD

    context.user_data["edit_field"] = field

    prompts = {
        "url": "請輸入新的 RSS feed URL：",
        "interval": "請輸入新的檢查間隔（分鐘）：",
        "prompt": "請輸入新的彙整 prompt：",
    }
    await update.message.reply_text(prompts[field])
    return EDIT_VALUE


async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None and update.message.text is not None
    assert context.user_data is not None
    value = update.message.text.strip()
    field = context.user_data["edit_field"]
    feed_id = str(context.user_data["edit_feed_id"])
    feed_name = context.user_data["edit_feed_name"]

    data = _bot_data(context)
    state = data["state"]
    scheduler = data["scheduler"]
    agent = data["agent"]

    # Validate and apply
    if field == "interval":
        try:
            interval = int(value)
            if interval < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ 請輸入正整數。")
            return EDIT_VALUE
        state.update_feed(feed_id, check_interval_minutes=interval)
        reschedule_feed_job(scheduler, feed_id, interval)
        await update.message.reply_text(
            f"✅ 已更新！「{feed_name}」檢查間隔改為每 {interval} 分鐘。"
        )
    elif field == "url":
        # Validate URL
        feed = feedparser.parse(value)
        if feed.bozo and not feed.entries:
            await update.message.reply_text("❌ 無法解析此 URL。")
            return EDIT_VALUE
        state.update_feed(feed_id, url=value)
        # Re-register job with new feed config
        row = state.get_feed(feed_id)
        assert row is not None
        remove_feed_job(scheduler, feed_id)
        add_feed_job(scheduler, agent, feed_id, state.row_to_feed_config(row))
        await update.message.reply_text(f"✅ 已更新！「{feed_name}」的 URL 已變更。")
    elif field == "prompt":
        state.update_feed(feed_id, prompt=value)
        # Re-register job with new feed config
        row = state.get_feed(feed_id)
        assert row is not None
        remove_feed_job(scheduler, feed_id)
        add_feed_job(scheduler, agent, feed_id, state.row_to_feed_config(row))
        await update.message.reply_text(f"✅ 已更新！「{feed_name}」的 prompt 已變更。")

    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------------------


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None
    await update.message.reply_text("已取消操作。")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Register all handlers
# ---------------------------------------------------------------------------


def register_handlers(app: Application) -> None:  # type: ignore[type-arg]
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("list", list_handler))
    app.add_handler(CommandHandler("delete", delete_handler))
    app.add_handler(CommandHandler("pause", pause_handler))
    app.add_handler(CommandHandler("resume", resume_handler))
    app.add_handler(CommandHandler("check", check_handler))

    # /new conversation
    new_conv = ConversationHandler(
        entry_points=[CommandHandler("new", new_start)],
        states={
            NEW_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_url)],
            NEW_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_interval)],
            NEW_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_prompt)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
    )
    app.add_handler(new_conv)

    # /edit conversation
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
    )
    app.add_handler(edit_conv)
