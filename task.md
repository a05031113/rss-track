# RSS Telegram Agent — 開發規格文件

> 本文件用於 Claude Code 開發參考。請根據本規格建構完整專案。

## 1. 專案概述

建構一個 RSS 追蹤彙整 Agent。使用者設定 RSS feed URL、Telegram 頻道資訊及彙整指示後，Agent 定時檢查 RSS 更新，將新文章交給 Claude 依指示彙整，自動推送到指定 Telegram 頻道。

### 使用情境範例

> 每一個小時去查看某個機票優惠 RSS feed，若有更新就整理機票優惠相關資訊：從哪裡去哪裡、價格多少、時間範圍是什麼，然後傳到我的 Telegram 頻道。

### 目標

- 支援多組 RSS feed + Telegram 頻道的獨立配置
- 每組 feed 可自訂檢查間隔與彙整 prompt
- 使用 Claude Agent SDK，走 Max plan 額度（不花 API 費用）
- **Docker 部署優先**：新電腦只需幾個步驟即可啟動
- SQLite 持久化追蹤已處理文章，避免重複推播

---

## 2. 架構設計

```
┌──────────────┐     ┌───────────────────────────┐     ┌──────────────┐
│  Scheduler   │────▶│  Claude Agent SDK          │────▶│  Telegram    │
│ (APScheduler)│     │  (claude-agent-sdk-python)  │     │  Bot API     │
└──────────────┘     │                             │     └──────────────┘
                     │  Custom Tools (@tool):      │
                     │  └─ send_to_telegram        │
                     └─────────────┬───────────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     │  feedparser    │  SQLite   │
                     │  (RSS 解析)    │ (state.db)│
                     └───────────────┴───────────┘
```

### 核心流程

1. APScheduler 依各 feed 的 `check_interval_minutes` 觸發任務
2. 使用 `feedparser` 抓取 RSS feed
3. 查 SQLite 過濾掉已處理的文章
4. 若有新文章，透過 Claude Agent SDK 送出任務，Claude 自主呼叫 `send_to_telegram` tool
5. 成功推送後標記文章為已處理

### 認證方式

Claude Agent SDK 底層使用 Claude Code CLI，走 Max plan 訂閱額度：
- **不需要** `ANTHROPIC_API_KEY`（設了反而會走 API 計費）
- 主機上先執行 `claude login` 完成 OAuth 登入
- Docker 容器透過 mount `auth.json` 共用認證
- Token 過期時在主機重新 `claude login` 即可，容器自動讀到新 token

---

## 3. 技術選型

| 用途 | 套件 | 版本 |
|------|------|------|
| Agent 框架 | `claude-agent-sdk` | >=0.1.35 |
| RSS 解析 | `feedparser` | >=6.0.11 |
| HTTP client | `httpx` | >=0.27.0 |
| 設定檔 | `pyyaml` | >=6.0.2 |
| 環境變數 | `python-dotenv` | >=1.0.1 |
| 排程器 | `apscheduler` | >=3.10.4 |

### 前置需求

- Python 3.12+
- Node.js 18+（Claude Code CLI 需要）
- Claude Code CLI 已安裝（`npm install -g @anthropic-ai/claude-code`）
- 部署主機已執行 `claude login` 完成 Max plan 登入
- Telegram Bot Token（透過 @BotFather 建立）

---

## 4. 專案結構

```
rss-telegram-agent/
├── main.py              # 進入點：CLI 解析 + APScheduler 排程
├── agent.py             # Claude Agent SDK client + tool 註冊
├── tools.py             # RSS 抓取 + Telegram 發送 + @tool 定義
├── state.py             # SQLite 狀態管理
├── config.py            # YAML + .env 設定載入
├── feeds.yaml           # RSS feed 配置檔
├── requirements.txt
├── .env.example
├── Dockerfile
├── compose.yaml         # Docker Compose（推薦部署方式）
├── .dockerignore
├── .gitignore
└── README.md
```

---

## 5. 各模組詳細規格

### 5.1 config.py — 設定載入

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass
class FeedConfig:
    name: str                        # feed 顯示名稱
    url: str                         # RSS feed URL
    telegram_chat_id: str            # Telegram chat ID（頻道通常是負數）
    prompt: str                      # 給 Claude 的彙整指示
    check_interval_minutes: int = 60 # 檢查間隔（分鐘）
    max_entries_per_check: int = 10  # 每次最多處理幾篇

@dataclass
class AppConfig:
    telegram_bot_token: str          # 從 .env 讀取
    log_level: str                   # 預設 INFO
    db_path: Path                    # data/state.db
    feeds: list[FeedConfig]          # 從 feeds.yaml 讀取
    # 注意：不需要 anthropic_api_key，Agent SDK 走 Max plan

def load_config(feeds_path: str = "feeds.yaml") -> AppConfig:
    """
    載入 .env 環境變數 + feeds.yaml 設定檔。
    db_path 的父目錄不存在時自動建立。
    缺少 TELEGRAM_BOT_TOKEN 時拋出明確錯誤。
    若偵測到 ANTHROPIC_API_KEY 環境變數，log warning 提醒會走 API 計費。
    """
```

### 5.2 state.py — SQLite 狀態追蹤

```sql
CREATE TABLE IF NOT EXISTS seen_entries (
    feed_url   TEXT NOT NULL,
    entry_id   TEXT NOT NULL,
    title      TEXT,
    seen_at    TEXT NOT NULL,         -- ISO 8601 UTC
    PRIMARY KEY (feed_url, entry_id)
);

CREATE TABLE IF NOT EXISTS feed_checks (
    feed_url       TEXT PRIMARY KEY,
    last_checked   TEXT NOT NULL
);
```

```python
class StateStore:
    def __init__(self, db_path: Path): ...
    def is_seen(self, feed_url: str, entry_id: str) -> bool: ...
    def mark_seen(self, feed_url: str, entry_id: str, title: str = "") -> None: ...
    def get_seen_ids(self, feed_url: str) -> list[str]: ...
    def mark_checked(self, feed_url: str) -> None: ...
    def cleanup_old_entries(self, days: int = 30) -> int:
        """清理 N 天前的舊記錄，回傳刪除筆數。"""
```

### 5.3 tools.py — RSS 抓取 + Telegram 發送 + @tool 定義

#### fetch_rss_entries（Python 函式，非 tool）

```python
def fetch_rss_entries(url: str, max_entries: int = 10) -> list[dict]:
    """
    使用 feedparser 解析 RSS feed。不需要暴露給 Claude，Python 端直接呼叫。
    
    回傳 list of dict:
    - id: str        (取值優先順序: entry.id > entry.link > entry.title)
    - title: str
    - link: str
    - summary: str   (取值: entry.content[0].value > entry.summary > entry.description)
    - published: str
    
    - summary 移除 HTML 標籤 (re.sub(r"<[^>]+>", "", text))
    - summary 截斷至 3000 字元
    - bozo flag 只在 entries 為空時視為失敗
    - 失敗回傳空 list
    """
```

#### send_to_telegram（Claude Agent SDK @tool）

```python
from claude_agent_sdk import tool

def create_telegram_tool(bot_token: str):
    """用 closure 包住 bot_token，回傳 @tool 裝飾的函式。"""

    @tool(
        name="send_to_telegram",
        description="將彙整好的訊息傳送到指定的 Telegram 頻道。支援 Markdown 格式。"
    )
    def send_to_telegram(chat_id: str, message: str) -> dict:
        """
        chat_id: Telegram 頻道 ID
        message: 要傳送的訊息，支援 Markdown
        
        實作要點:
        - httpx POST https://api.telegram.org/bot{token}/sendMessage
        - parse_mode: "Markdown", disable_web_page_preview: true
        - 超過 4096 字元在換行符處分段
        - Markdown 失敗 fallback 純文字
        - 回傳 {"success": true} 或 {"success": false, "error": "..."}
        """

    return send_to_telegram
```

### 5.4 agent.py — Claude Agent SDK Client

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

SYSTEM_PROMPT = """你是一個 RSS 內容彙整助手。你會收到一批 RSS 新文章和使用者的彙整指示。

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

class RSSAgent:
    def __init__(self, config: AppConfig, state: StateStore):
        self.config = config
        self.state = state
        self.telegram_tool = create_telegram_tool(config.telegram_bot_token)

    async def check_feed(self, feed: FeedConfig) -> None:
        """
        處理單一 feed 的完整流程:
        
        1. fetch_rss_entries() 抓取文章
        2. state.get_seen_ids() 過濾新文章
        3. 若無新文章，return
        4. 組合 user message（新文章 + feed.prompt + feed.telegram_chat_id）
        5. 建立 ClaudeSDKClient，註冊 send_to_telegram tool
        6. 送出 message，SDK 自動處理 tool call loop
        7. 成功後 mark_seen 所有新文章
        """
        # 1-3: RSS 抓取 + 過濾
        entries = fetch_rss_entries(feed.url, feed.max_entries_per_check)
        seen_ids = self.state.get_seen_ids(feed.url)
        new_entries = [e for e in entries if e["id"] not in seen_ids]
        
        if not new_entries:
            logger.info(f"[{feed.name}] 沒有新文章")
            return

        # 4: 組合 user message
        user_message = self._build_user_message(feed, new_entries)

        # 5-6: Claude Agent SDK
        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            max_turns=10,
        )
        client = ClaudeSDKClient(options=options)
        client.register_tool(self.telegram_tool)

        try:
            response = await client.send_message(user_message)
            logger.info(f"[{feed.name}] Agent 完成: {response}")
        except Exception as e:
            logger.error(f"[{feed.name}] Agent 錯誤: {e}")
            return  # 不標記，下次重試

        # 7: 標記已處理
        for entry in new_entries:
            self.state.mark_seen(feed.url, entry["id"], entry.get("title", ""))
        self.state.mark_checked(feed.url)

    def _build_user_message(self, feed: FeedConfig, entries: list[dict]) -> str:
        """
        組合格式:
        
        ## 彙整指示
        {feed.prompt}
        
        ## Telegram 頻道
        chat_id: {feed.telegram_chat_id}
        
        ## 新文章（共 N 篇）
        
        ### 文章 1
        標題: ...
        連結: ...
        摘要: ...
        發布時間: ...
        """
```

**重要：** Claude Agent SDK 仍在快速迭代中。`ClaudeSDKClient`、`ClaudeAgentOptions`、`@tool` decorator 的實際 API 可能與上方範例略有差異。開發前請先：
1. `pip install --upgrade claude-agent-sdk`
2. 查看 [官方 README](https://github.com/anthropics/claude-agent-sdk-python)
3. 依最新 API 調整程式碼

### 5.5 main.py — 進入點 + 排程

```python
"""
CLI 介面:
    python main.py                         # 啟動排程器
    python main.py --once                  # 執行一次所有 feed
    python main.py --config path/to.yaml   # 指定設定檔

排程器:
    - APScheduler BlockingScheduler
    - 每個 feed 獨立 IntervalTrigger
    - 啟動時立即執行一次
    - 每天凌晨 3:00 UTC 清理 30 天前舊記錄
    - SIGINT / SIGTERM 優雅關閉

Logging:
    - "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    - LOG_LEVEL 環境變數控制，預設 INFO

注意：因為 Claude Agent SDK 是 async 的，
排程器呼叫 check_feed 時需要用 asyncio.run() 或 anyio.run() 包裝。
"""
```

---

## 6. 設定檔格式

### .env.example

```bash
# === 必填 ===
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# === 選填 ===
LOG_LEVEL=INFO

# ⚠️ 不要設定 ANTHROPIC_API_KEY！
# Agent SDK 使用 Claude Code CLI 的 Max plan 登入認證。
# 若設了 ANTHROPIC_API_KEY，會走 API 計費而非 Max plan 額度。
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
      你是一個機票優惠彙整助手。請從以下文章中提取機票優惠資訊。

      針對每一則優惠，整理成以下格式：
      ✈️ *航線*: 出發地 → 目的地
      💰 *價格*: 價格資訊（含幣別）
      📅 *旅行期間*: 可出發的日期範圍
      ⏰ *優惠截止*: 截止日期（如有）
      🔗 [查看詳情](連結)

      多則優惠用分隔線隔開。不含機票優惠的文章請跳過。

  - name: "科技新聞摘要"
    url: "https://feeds.feedburner.com/TechCrunch"
    telegram_chat_id: "-1009876543210"
    check_interval_minutes: 120
    max_entries_per_check: 5
    prompt: |
      用繁體中文摘要以下科技新聞，每篇 2-3 句話。
      格式：
      📰 *{標題}*
      摘要內容
      🔗 [原文連結](URL)
```

---

## 7. Docker 部署

### 7.1 Dockerfile

```dockerfile
FROM python:3.12-slim

# 安裝 Node.js（Claude Code CLI 需要）
RUN apt-get update && apt-get install -y curl git \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 安裝 Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

RUN mkdir -p /app/data

VOLUME ["/app/data"]

CMD ["python", "main.py"]
```

### 7.2 compose.yaml

```yaml
services:
  rss-agent:
    build: .
    container_name: rss-telegram-agent
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      # RSS 設定（唯讀）
      - ./feeds.yaml:/app/feeds.yaml:ro
      # SQLite 持久化
      - agent-data:/app/data
      # Claude Code 認證（唯讀 mount 主機的 auth.json）
      - ~/.config/claude-code/auth.json:/root/.config/claude-code/auth.json:ro
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  agent-data:
```

### 7.3 .dockerignore

```
.env
.env.example
data/
__pycache__/
*.pyc
.git/
.gitignore
README.md
```

### 7.4 在新電腦上部署步驟

```bash
# ===== 一次性設定（每台新電腦做一次）=====

# 1. 安裝 Claude Code CLI 並登入 Max plan
npm install -g @anthropic-ai/claude-code
claude login
# 瀏覽器會開啟，完成 OAuth 登入

# 2. 確認認證檔案存在
ls ~/.config/claude-code/auth.json

# ===== 部署專案 =====

# 3. Clone 專案
git clone <repo-url> rss-telegram-agent
cd rss-telegram-agent

# 4. 設定 .env（只需要 Telegram Bot Token）
cp .env.example .env
vi .env  # 填入 TELEGRAM_BOT_TOKEN

# 5. 設定 feeds.yaml
vi feeds.yaml

# 6. 啟動
docker compose up -d

# 查看 logs
docker compose logs -f
```

### 7.5 認證維護

```bash
# Token 過期時，在主機上重新登入即可
# 容器 mount 的是唯讀檔案，會即時讀到新 token
claude login

# 驗證 token 是否有效（在主機上）
claude --version

# 如果容器已在跑，不需要重啟，下次 agent 呼叫時會自動用新 token
# 但如果想立即確認，可以重啟
docker compose restart
```

### 7.6 日常維運

```bash
# 修改 feeds.yaml 後重啟
docker compose restart

# 更新程式碼
git pull
docker compose up -d --build

# 手動觸發一次（測試用）
docker compose run --rm rss-agent python main.py --once

# 查看即時 logs
docker compose logs -f --tail 50

# 清除 SQLite 重置狀態
docker compose down
docker volume rm rss-telegram-agent_agent-data
docker compose up -d
```

---

## 8. 關鍵實作細節

### 8.1 RSS 解析

- `entry_id` 優先：`entry.id` → `entry.link` → `entry.title`
- summary 移除 HTML：`re.sub(r"<[^>]+>", "", text)`
- summary 截斷 3000 字元
- `bozo` flag 只在 entries 為空時視為失敗

### 8.2 Telegram Bot API

- `POST https://api.telegram.org/bot{token}/sendMessage`
- 單則上限 4096 字元，超過在換行處分段
- Markdown 失敗 fallback 純文字
- `disable_web_page_preview: true`
- Bot 需加入頻道並設為管理員

### 8.3 Claude Agent SDK

- 底層使用 Claude Code CLI，走 Max plan 額度
- **環境中不可有 `ANTHROPIC_API_KEY`**，否則走 API 計費
- `@tool` decorator 定義 custom tools，以 in-process MCP server 執行
- `max_turns` 設 10 防無限循環
- SDK 是 async 的，搭配 `asyncio` 使用
- **API 仍在快速迭代**，開發前先 `pip install --upgrade claude-agent-sdk` 並查最新 README

### 8.4 錯誤處理

| 錯誤場景 | 處理方式 |
|----------|---------|
| RSS 抓取失敗 | log warning，跳過，下次重試 |
| Telegram 傳送失敗 | log error，**不標記**（下次重試） |
| Agent SDK 錯誤 | log error，跳過 |
| 認證過期 | log error + 明確提示「請在主機執行 claude login」 |

所有 feed 彼此獨立，一個失敗不影響其他。

### 8.5 認證過期偵測

Agent SDK 呼叫時若 token 過期，通常會拋出認證相關 exception。程式應：
1. 捕捉該 exception
2. Log 明確訊息：`"Claude 認證已過期，請在主機執行 'claude login' 重新登入"`
3. 跳過本次，繼續排程（等用戶登入後自動恢復）

---

## 9. 測試

### 本機快速測試

```bash
# 確認 Claude Code 已登入
claude --version

# 安裝依賴
pip install -r requirements.txt

# 設定
cp .env.example .env  # 填入 TELEGRAM_BOT_TOKEN
vi feeds.yaml

# 執行一次
python main.py --once
```

### Docker 測試

```bash
# 確認 auth.json 存在
ls ~/.config/claude-code/auth.json

# 前景執行觀察
docker compose up --build

# 單次測試
docker compose run --rm rss-agent python main.py --once
```

### 驗證清單

- [ ] RSS feed 正確解析
- [ ] 新文章偵測正確（第一次全新，第二次為空）
- [ ] Claude 依 prompt 正確彙整
- [ ] Telegram 訊息送達且格式正確
- [ ] 長訊息正確分段（>4096 字元）
- [ ] Markdown 失敗 fallback 純文字
- [ ] 排程器按間隔觸發
- [ ] `--once` 執行後退出
- [ ] Docker volume 重啟後資料保留
- [ ] 認證過期時有明確 log 提示
- [ ] SIGINT/SIGTERM 優雅關閉
- [ ] 30 天舊記錄自動清理

---

## 10. README.md 內容大綱

請產生 README.md，包含：

- 專案簡介（一段話）
- 前置需求（Node.js、Claude Code CLI、Max plan 登入、Telegram Bot）
- Quick Start（Docker 部署步驟）
- 設定說明（.env 和 feeds.yaml 範例）
- 新增 Feed 範例
- 認證維護（token 過期怎麼辦）
- 本機開發方式
- 日常維運指令

---

## 11. 未來擴展方向（不需現在實作）

- 全文擷取：RSS summary 太短時抓原文
- Telegram 指令：`/add_feed`、`/check_now`、`/status`
- 健康檢查 endpoint
- Token 自動刷新腳本（cron job 定期 ping claude CLI）