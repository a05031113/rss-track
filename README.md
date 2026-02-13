# RSS Telegram Agent

定時追蹤 RSS feed，使用 Claude Agent SDK 彙整新文章，自動推送到 Telegram 頻道。支援多組 feed 各自設定檢查間隔與彙整 prompt，走 Claude Max plan 額度（不花 API 費用）。

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

# 4. 設定 feeds
# 編輯 feeds.yaml，設定你的 RSS feed 和 Telegram 頻道

# 5. 啟動
docker compose up -d

# 查看 logs
docker compose logs -f
```

## 設定說明

### .env

```bash
# 必填
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# 選填
LOG_LEVEL=INFO

# ⚠️ 不要設定 ANTHROPIC_API_KEY！會走 API 計費而非 Max plan 額度。
```

### feeds.yaml

```yaml
feeds:
  - name: "機票優惠追蹤"
    url: "https://example.com/flight-deals/rss"
    telegram_chat_id: "-1001234567890"
    check_interval_minutes: 60
    max_entries_per_check: 10
    prompt: |
      整理機票優惠資訊，包含航線、價格、旅行期間。
```

每個 feed 獨立設定：
- `name`: 顯示名稱（用於 log）
- `url`: RSS feed URL
- `telegram_chat_id`: Telegram 頻道 ID（頻道通常是負數）
- `check_interval_minutes`: 檢查間隔（分鐘），預設 60
- `max_entries_per_check`: 每次最多處理幾篇，預設 10
- `prompt`: 給 Claude 的彙整指示

## 新增 Feed 範例

在 `feeds.yaml` 的 `feeds:` 下新增一個項目：

```yaml
  - name: "科技新聞"
    url: "https://feeds.feedburner.com/TechCrunch"
    telegram_chat_id: "-1009876543210"
    check_interval_minutes: 120
    max_entries_per_check: 5
    prompt: |
      用繁體中文摘要每篇新聞，2-3 句話。
```

修改後重啟：`docker compose restart`

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

# 執行一次（測試用）
uv run rss-track --once

# 啟動排程器
uv run rss-track

# Lint
uv run ruff check src/
uv run mypy src/
```

## 日常維運

```bash
# 修改 feeds.yaml 後重啟
docker compose restart

# 更新程式碼
git pull && docker compose up -d --build

# 手動觸發一次
docker compose run --rm rss-agent uv run rss-track --once

# 查看 logs
docker compose logs -f --tail 50

# 清除 SQLite 重置狀態
docker compose down
docker volume rm rss-telegram-agent_agent-data
docker compose up -d
```
