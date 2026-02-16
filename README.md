# RSS Telegram Agent

定時追蹤 RSS feed，使用 Claude Agent SDK 彙整新文章，自動推送到 Telegram 頻道。透過 Telegram Bot 指令動態管理 feed，走 Claude Max plan 額度（不花 API 費用）。

## 前置需求

- Python 3.13+
- Node.js 18+（Claude Code CLI 需要）
- Claude Code CLI：`npm install -g @anthropic-ai/claude-code`
- Claude Max plan 已登入：`claude login`
- Telegram Bot Token（透過 [@BotFather](https://t.me/BotFather) 建立）

## Quick Start（Docker 部署）

```bash
# 1. 主機登入 Claude（一次性）
npm install -g @anthropic-ai/claude-code
claude login

# 2. Clone 專案
git clone <repo-url> rss-track
cd rss-track

# 3. 設定環境變數
cp .env.example .env
# 編輯 .env，填入 TELEGRAM_BOT_TOKEN

# 4. 啟動
docker compose up -d

# 5. 在 Telegram 對 Bot 發送 /new 新增第一個 feed
```

## 設定說明

### .env

```bash
# 必填
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# 選填
TELEGRAM_CHAT_ID=1234567890   # 新增 feed 時的預設 chat ID
LOG_LEVEL=INFO

# ⚠️ 不要設定 ANTHROPIC_API_KEY！會走 API 計費而非 Max plan 額度。
```

## Telegram Bot 指令

| 指令 | 說明 |
|------|------|
| `/start` | 顯示歡迎訊息與指令列表 |
| `/new` | 新增 RSS feed（互動式引導） |
| `/list` | 列出所有 feed |
| `/edit <名稱>` | 修改 feed 設定（URL / 間隔 / prompt） |
| `/delete <名稱>` | 刪除 feed |
| `/pause <名稱>` | 暫停 feed |
| `/resume <名稱>` | 恢復 feed |
| `/check <名稱>` | 立即檢查一次 |
| `/cancel` | 取消當前操作 |

### 新增 Feed 範例

1. 對 Bot 發送 `/new`
2. 貼上 RSS feed URL（Bot 會自動偵測 feed 名稱）
3. 輸入檢查間隔（分鐘），或直接按 Enter 使用預設 60
4. 輸入彙整 prompt（例如：「用繁體中文摘要每篇新聞，2-3 句話」）

### 從 feeds.yaml 遷移

如果有現有的 `feeds.yaml`，可以一次性匯入到資料庫：

```bash
uv run rss-track --migrate --config feeds.yaml
```

## 認證維護

```bash
# Token 過期時，在主機重新登入
claude login

# 容器 mount 的 auth.json 會即時更新，不需重啟
# 如需立即確認，可以重啟
docker compose restart
```

## 本機開發

```bash
# 安裝依賴
uv sync

# 設定
cp .env.example .env  # 填入 TELEGRAM_BOT_TOKEN

# 啟動 Bot + 排程器
uv run rss-track

# 執行一次所有 active feeds（測試用）
uv run rss-track --once

# Lint
uv run ruff check src/
uv run mypy src/
```

## 日常維運

```bash
# 更新程式碼
git pull && docker compose up -d --build

# 手動觸發一次所有 feed
docker compose run --rm rss-agent uv run rss-track --once

# 查看 logs
docker compose logs -f --tail 50

# 清除 SQLite 重置狀態
docker compose down
docker volume rm rss-telegram-agent_agent-data
docker compose up -d
```
