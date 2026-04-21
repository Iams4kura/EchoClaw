<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Vue-3.5-4FC08D?logo=vuedotjs&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-green" />
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white" />
</p>

# 🦞 EchoClaw

> **从终端 AI 助手到云端数字分身** —— 一套完整的 AI Agent 系统。

EchoClaw 由两个子项目组成：

| 项目 | 定位 | 一句话描述 |
|------|------|-----------|
| **mini_claude** | 🧠 终端 AI 编程助手 | Claude Code 的 Python 复刻，在终端里对话、读写文件、执行命令 |
| **mini_claw** | 🤖 云端数字分身平台 | 基于 mini_claude 引擎，接入 Telegram / 飞书 / 企微 / Web，7×24 自主运行 |

---

## 📖 目录

- [架构总览](#架构总览)
- [快速开始](#快速开始)
  - [mini_claude — 终端助手](#mini_claude--终端助手)
  - [mini_claw — 数字分身](#mini_claw--数字分身)
- [配置说明](#配置说明)
- [Docker 部署](#docker-部署)
- [项目结构](#项目结构)
- [开发指南](#开发指南)
- [License](#license)

---

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                      EchoClaw                           │
│                                                         │
│  ┌─────────────────┐       ┌──────────────────────────┐ │
│  │   mini_claude    │       │       mini_claw          │ │
│  │                  │       │                          │ │
│  │  Terminal REPL   │       │  ┌─────┐ ┌────┐ ┌────┐  │ │
│  │  ┌────────────┐  │       │  │ TG  │ │飞书│ │企微│  │ │
│  │  │ Engine     │  │◄──────│  └──┬──┘ └─┬──┘ └─┬──┘  │ │
│  │  │ ┌────────┐ │  │       │     └──────┼──────┘     │ │
│  │  │ │ Tools  │ │  │       │        Gateway          │ │
│  │  │ │ LLM    │ │  │       │     ┌──────┴──────┐     │ │
│  │  │ │Context │ │  │       │     │    Brain     │     │ │
│  │  │ └────────┘ │  │       │     │ (认知循环)    │     │ │
│  │  └────────────┘  │       │     └──────┬──────┘     │ │
│  │                  │       │  ┌────┐ ┌──┴──┐ ┌────┐  │ │
│  │  Session / MCP   │       │  │Soul│ │Hands│ │Mem │  │ │
│  └─────────────────┘       │  └────┘ └─────┘ └────┘  │ │
│                             │       Web Console       │ │
│                             └──────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（仅 mini_claw Web 控制台需要）
- 一个 LLM API Key（OpenAI 兼容接口即可，如 Anthropic、DeepSeek、通义千问等）

### mini_claude — 终端助手

mini_claude 是一个终端 AI 编程助手，支持对话式编程、文件读写、命令执行、Web 搜索等。

#### 1. 安装

```bash
cd mini_claude
pip install -e ".[dev]"
```

#### 2. 配置

```bash
cp config/settings.example.yaml config/settings.yaml
```

编辑 `config/settings.yaml`，填入你的 LLM 配置：

```yaml
llm:
  default_model: "deepseek-chat"          # 你的模型名称
  api_key: "sk-xxx"                       # API 密钥
  base_url: "https://api.deepseek.com/v1" # API 地址
  temperature: 0.7
  max_tokens: 4096
```

#### 3. 启动

```bash
# 使用命令行入口
mini_claude

# 或者直接运行
python -m src.main

# 指定模型
mini_claude --model gpt-4o

# 恢复上次会话
mini_claude --resume
```

#### 4. 内置工具

| 工具 | 功能 |
|------|------|
| `bash` | 执行终端命令 |
| `file_read` | 读取文件内容 |
| `file_write` | 写入文件 |
| `file_edit` | 精确编辑文件 |
| `grep` | 正则搜索代码 |
| `glob` | 文件模式匹配 |
| `web_search` | 搜索互联网 |
| `memory` | 持久化记忆 |
| `agent` | 启动子 Agent 并行处理 |
| `mcp` | MCP 协议工具扩展 |

---

### mini_claw — 数字分身

mini_claw 是一个云端 AI 数字分身平台，拥有人格、记忆、自主决策能力，支持多平台接入。

#### 1. 安装

```bash
cd mini_claw
pip install -e ".[dev]"
```

#### 2. 配置

```bash
cp config/settings.example.yaml config/settings.yaml
```

编辑 `config/settings.yaml`，至少配置 LLM 和一个消息平台：

```yaml
# LLM 配置（Brain 使用）
llm:
  default_model: "deepseek-chat"
  api_key: "sk-xxx"
  base_url: "https://api.deepseek.com/v1"

# 选择一个或多个平台接入
telegram:
  bot_token: "your-telegram-bot-token"

# 或使用内置 Web 聊天（无需额外配置，启动即可用）
server:
  host: "0.0.0.0"
  port: 8080
```

#### 3. 启动

```bash
# 使用命令行入口
mini_claw

# 指定配置文件
mini_claw --config /path/to/settings.yaml
```

启动后访问 `http://localhost:8080` 即可使用内置 Web 聊天界面。

#### 4. 核心模块

| 模块 | 说明 |
|------|------|
| **Soul** 🎭 | 人格系统，从 `workspace/IDENTITY.md` 和 `SOUL.md` 加载性格与行为准则 |
| **Brain** 🧠 | 认知循环，意图识别 → 计划 → 执行 → 反思，支持多轮对话 |
| **Hands** 🤲 | 执行层，调用 mini_claude 引擎池完成编码、文件操作等任务 |
| **Memory** 💾 | 记忆系统，自动提取对话要点，持久化到 `workspace/memory/` |
| **Gateway** 🌐 | 网关层，统一消息协议，适配 Telegram / 飞书 / 企微 / Webhook |
| **Routine** ⏰ | 自驱日程，支持定时任务和心跳检查，从 `HEARTBEAT.md` 加载 |
| **Recovery** 🔧 | 自愈系统，任务失败时自动诊断和修复 |
| **Avatar** 🎨 | 人设预设，coder / ops / general 等多种角色快速切换 |

#### 5. Workspace 文件说明

mini_claw 的"灵魂"由 `workspace/` 目录下的 Markdown 文件定义：

```
workspace/
├── IDENTITY.md          # 名字、角色、表达习惯
├── SOUL.md              # 行为准则、边界、气质
├── AGENTS.md            # Agent 规则与限制
├── BOOTSTRAP.md         # 首次启动引导流程
├── HEARTBEAT.md         # 心跳任务定义
├── MEMORY.md            # 记忆管理策略
├── TOOLS.md             # 可用工具说明
├── USER.md              # 主人画像
└── memory/              # 持久化记忆
    ├── learnings/       # 学到的经验
    └── sessions/        # 对话记录
```

#### 6. 支持的平台

| 平台 | 状态 | 配置项 |
|------|------|--------|
| **Web 聊天** | ✅ 内置 | 启动即用，`http://localhost:8080` |
| **Telegram** | ✅ | `telegram.bot_token` |
| **飞书** | ✅ | `feishu.app_id` + `feishu.app_secret` |
| **企业微信** | ✅ | `wecom.corp_id` + `wecom.corp_secret` |

---

## 配置说明

### 环境变量

所有配置项均可通过环境变量覆盖（优先级高于配置文件）：

| 环境变量 | 说明 |
|---------|------|
| `OPENAI_API_KEY` | LLM API 密钥 |
| `OPENAI_BASE_URL` | LLM API 地址 |
| `OPENAI_MODEL` | 模型名称 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `FEISHU_APP_ID` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 飞书应用 Secret |
| `WECOM_CORP_ID` | 企微企业 ID |
| `WECOM_WEBHOOK_URL` | 企微群机器人 Webhook |
| `MINI_CLAW_PORT` | 服务端口（默认 8080） |
| `MINI_CLAW_WORKSPACE_DIR` | Workspace 目录路径 |
| `BAIDU_AI_SEARCH_API_KEY` | 百度 AI 搜索 API Key |

---

## Docker 部署

推荐使用 Docker Compose 一键部署 mini_claw：

```bash
cd mini_claw/deploy

# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入 API Key 等配置
vim .env

# 启动
docker compose up -d

# 查看日志
docker compose logs -f
```

服务启动后访问 `http://your-server:8080` 即可。

---

## 项目结构

```
EchoClaw/
├── mini_claude/                # 终端 AI 编程助手
│   ├── src/
│   │   ├── main.py             # CLI 入口
│   │   ├── commands/           # 内置命令 (/help, /clear, ...)
│   │   ├── config/             # 配置加载
│   │   ├── engine/             # 查询引擎、流式输出、Headless 模式
│   │   ├── models/             # 数据模型 (消息/工具/状态)
│   │   ├── services/           # 核心服务 (LLM/上下文/权限/MCP)
│   │   ├── tools/              # 工具集 (bash/file/grep/agent/...)
│   │   ├── ui/                 # 终端 UI (Rich)
│   │   └── utils/              # 工具函数
│   ├── tests/                  # 测试
│   ├── config/                 # 配置文件
│   └── pyproject.toml
│
├── mini_claw/                  # 云端数字分身平台
│   ├── script/
│   │   ├── main.py             # 服务入口
│   │   ├── brain/              # 认知循环 (LLM/规划/对话)
│   │   ├── gateway/            # 网关 (Telegram/飞书/企微/Webhook)
│   │   ├── hands/              # 执行层 (mini_claude 引擎池)
│   │   ├── heartbeat/          # 心跳监控
│   │   ├── memory/             # 记忆系统 (提取/存储/加载)
│   │   ├── recovery/           # 自愈系统
│   │   ├── routine/            # 定时任务调度
│   │   ├── scheduler/          # 任务管理
│   │   ├── soul/               # 人格管理
│   │   └── avatar/             # 角色预设
│   ├── web/                    # Vue3 Web 控制台
│   ├── workspace/              # 人格定义 & 记忆
│   ├── deploy/                 # Docker 部署
│   ├── tests/                  # 测试
│   └── pyproject.toml
│
└── README.md
```

---

## 开发指南

### 运行测试

```bash
# mini_claude 测试
cd mini_claude
pytest

# mini_claw 测试
cd mini_claw
pytest
```

### 构建 Web 控制台

```bash
cd mini_claw/web
npm install
npm run dev      # 开发模式
npm run build    # 构建生产版本
```

### 代码规范

项目使用 [Ruff](https://github.com/astral-sh/ruff) 进行代码检查，[Black](https://github.com/psf/black) 进行格式化：

```bash
ruff check .
black .
```

---

## License

[MIT](LICENSE)