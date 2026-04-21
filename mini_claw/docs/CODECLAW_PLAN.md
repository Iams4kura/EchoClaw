# Codeclaw 云平台开发计划

> Codeclaw — 基于 mini_claude 执行引擎的 7x24 云端自动化平台
> 通过手机 IM 下发任务，多分身并行执行，越用越懂你。
>
> **项目结构**：
> - `mini_claw/` — 云平台本体（网关、调度、分身、记忆、心跳）
> - `mini_claude/` — 执行引擎（已有，mini_claw 调用它来执行具体任务）

---

## 一、愿景

在 mini_claude 执行引擎之上，构建 **mini_claw** 云平台层，实现**远程操控、多实例运行、持续学习**的 AI 自动化平台。

核心体验：
- **随时随地**：手机发消息即可布置任务
- **多面分身**：代码手、运维眼、写作助理……各司其职
- **越用越懂**：结构化记忆 + 自动提取，积累用户画像
- **永不下线**：Docker 部署，7x24 心跳守护

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────────────┐
│                     Codeclaw Cloud Platform                   │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │                   IM Gateway Layer                    │     │
│  │  Telegram │ Feishu │ WeChat Work │ Webhook/API       │     │
│  │           ↓ 统一消息协议 (MessageBus) ↓               │     │
│  └──────────────────────┬──────────────────────────────┘     │
│                         │                                     │
│  ┌──────────────────────▼──────────────────────────────┐     │
│  │                  Scheduler Layer                      │     │
│  │  TaskRouter · 意图识别 · 任务拆分 · 优先级队列         │     │
│  └───────┬──────────────┬──────────────┬───────────────┘     │
│          │              │              │                      │
│  ┌───────▼───┐  ┌───────▼───┐  ┌──────▼────┐                │
│  │ Avatar:   │  │ Avatar:   │  │ Avatar:   │  ← 分身执行层   │
│  │ 代码手    │  │ 运维眼    │  │ 临时#37   │                 │
│  │ (resident)│  │ (resident)│  │(ephemeral)│                 │
│  │           │  │           │  │           │                 │
│  │ mini_claw  │  │ mini_claw │  │ mini_claw │                 │
│  │ →engine(mc)│  │ →engine(mc│  │ →engine(mc│                 │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘                │
│        │              │              │                        │
│  ┌─────▼──────────────▼──────────────▼─────────────────┐     │
│  │                 Shared Infrastructure                 │     │
│  │  Memory System │ Heartbeat │ Tool Sandbox │ Storage   │     │
│  └─────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、模块设计

### 3.1 IM 网关层 (`script/gateway/`)

**目标**：所有 IM 平台消息归一化，后续加平台只写 adapter。

#### 核心数据结构

```python
# script/gateway/models.py
@dataclass
class UnifiedMessage:
    platform: str          # "telegram" | "feishu" | "wecom" | "webhook"
    user_id: str           # 平台无关的用户标识
    chat_id: str           # 会话 ID
    content: str           # 纯文本（富文本降级）
    attachments: list      # 文件/图片路径
    reply_to: str | None   # 引用消息 ID
    timestamp: float
    metadata: dict         # 平台特有字段透传

@dataclass
class BotResponse:
    text: str              # 回复文本（支持 markdown）
    attachments: list      # 附件
    reply_to: str | None   # 回复目标
    buttons: list | None   # 交互按钮（可选）
```

#### 文件结构

```
script/gateway/
├── __init__.py
├── models.py              # UnifiedMessage, BotResponse
├── base_adapter.py        # BaseAdapter 抽象基类
├── adapters/
│   ├── __init__.py
│   ├── telegram.py        # P0: 第一个实现 ✅
│   ├── feishu.py          # P3: 飞书
│   ├── wecom.py           # P3: 企微
│   └── webhook.py         # P0: HTTP API 直接调用（调试用）✅
└── middleware/
    ├── __init__.py
    ├── auth.py             # 用户鉴权
    ├── rate_limit.py       # 频率限制
    └── logging.py          # 消息日志
```

#### Adapter 接口规范

```python
# script/gateway/base_adapter.py
class BaseAdapter(ABC):
    @abstractmethod
    async def start(self) -> None:
        """启动长连接/webhook 监听"""

    @abstractmethod
    async def stop(self) -> None:
        """优雅关闭"""

    @abstractmethod
    async def send(self, chat_id: str, response: BotResponse) -> None:
        """发送消息到 IM"""

    def on_message(self, callback: Callable[[UnifiedMessage], Awaitable[None]]) -> None:
        """注册消息回调"""
        self._callback = callback
```

---

### 3.2 调度中心 (`script/scheduler/`)

**目标**：接收消息 → 理解意图 → 路由到正确的分身 → 管理任务生命周期。

#### 文件结构

```
script/scheduler/
├── __init__.py
├── router.py              # TaskRouter — 意图识别 + 分身路由
├── task_manager.py        # 任务 CRUD + 生命周期
├── task_queue.py          # 基于 Redis 的优先级队列
└── models.py              # Task, TaskStatus 数据结构
```

#### 任务模型

```python
# script/scheduler/models.py
class TaskStatus(str, Enum):
    PENDING = "pending"          # 排队中
    ASSIGNED = "assigned"        # 已分配分身
    RUNNING = "running"          # 执行中
    WAITING_USER = "waiting"     # 等待用户输入
    COMPLETED = "completed"      # 完成
    FAILED = "failed"            # 失败
    CANCELLED = "cancelled"      # 取消

@dataclass
class Task:
    id: str                      # 唯一 ID
    source_message: UnifiedMessage
    status: TaskStatus
    assigned_avatar: str | None  # 分身 ID
    parent_task: str | None      # 父任务（子任务场景）
    subtasks: list[str]          # 子任务 ID 列表
    priority: int                # 0=紧急 9=低
    created_at: float
    updated_at: float
    result: str | None           # 执行结果
    progress: str | None         # 当前进度描述
    error: str | None            # 错误信息
```

#### 路由策略

```python
# script/scheduler/router.py
class TaskRouter:
    """意图识别 + 分身路由"""

    async def route(self, msg: UnifiedMessage) -> tuple[str, Task]:
        """
        路由逻辑优先级：
        1. 显式指定: "@代码手 xxx" → 直接路由
        2. 意图识别: 用 LLM 快速分类消息意图
        3. 默认分身: 无法判断时路由到通用分身
        """

    async def should_split(self, task: Task) -> list[Task] | None:
        """判断是否需要拆分为子任务"""
```

---

### 3.3 分身系统 (`script/avatar/`)

**目标**：每个分身内部持有一个 mini_claude 执行引擎实例，加上自己的角色设定、工具集和记忆空间。

#### 文件结构

```
script/avatar/
├── __init__.py
├── models.py              # Avatar, AvatarConfig 数据结构
├── manager.py             # AvatarManager — 分身生命周期管理
├── registry.py            # 已注册分身的目录
├── runner.py              # AvatarRunner — 单个分身的执行循环
└── presets/               # 预置分身配置
    ├── coder.yaml         # 代码手
    ├── ops.yaml           # 运维眼
    ├── writer.yaml        # 写作助理
    └── general.yaml       # 通用助手
```

#### 核心数据结构

```python
# script/avatar/models.py
class AvatarType(str, Enum):
    RESIDENT = "resident"      # 常驻分身 — 长期存在
    EPHEMERAL = "ephemeral"    # 临时分身 — 任务完成即回收

class AvatarStatus(str, Enum):
    IDLE = "idle"              # 空闲
    BUSY = "busy"              # 执行中
    SLEEPING = "sleeping"      # 休眠（长时间无任务）
    DEAD = "dead"              # 已销毁

@dataclass
class AvatarConfig:
    id: str
    name: str                  # 显示名称
    type: AvatarType
    system_prompt: str         # 角色专属 system prompt
    tools_whitelist: list[str] # 可用工具列表
    memory_namespace: str      # 私有记忆空间路径
    model: str                 # LLM 模型（不同分身可用不同模型）
    max_concurrent_tasks: int  # 最大并发任务数
    heartbeat_interval: int    # 心跳间隔（秒）
    max_idle_time: int         # 最大空闲时间 → 休眠

@dataclass
class Avatar:
    config: AvatarConfig
    status: AvatarStatus
    current_tasks: list[str]   # 正在执行的任务 ID
    engine: "QueryEngine"      # mini_claude 执行引擎实例（来自 mini_claude 包）
    created_at: float
    last_active: float
```

#### 分身管理器

```python
# script/avatar/manager.py
class AvatarManager:
    """管理所有分身的生命周期"""

    async def start_resident(self, config: AvatarConfig) -> Avatar:
        """启动常驻分身"""

    async def spawn_ephemeral(
        self, parent: Avatar, task: Task, config_override: dict | None = None
    ) -> Avatar:
        """从常驻分身派生临时分身"""

    async def reclaim(self, avatar_id: str) -> None:
        """回收临时分身（结果已回传）"""

    async def sleep(self, avatar_id: str) -> None:
        """休眠空闲分身（释放资源但保留状态）"""

    async def wake(self, avatar_id: str) -> Avatar:
        """唤醒休眠分身"""

    def list_available(self) -> list[Avatar]:
        """列出空闲可用的分身"""
```

#### 预置分身配置示例

```yaml
# script/avatar/presets/coder.yaml
id: coder
name: "代码手"
type: resident
system_prompt: |
  你是「代码手」，一个专注于软件开发的 AI 助手。
  你的核心职责：代码编写、Review、重构、调试、测试。
  你精通 Python/TypeScript/Go，擅长分析复杂代码库。
  在不确定时，优先保持代码安全和可读性。
tools_whitelist:
  - bash
  - file_read
  - file_write
  - file_edit
  - glob
  - grep
  - web_search
memory_namespace: "avatars/coder"
model: "anthropic/claude-sonnet-4-20250514"
max_concurrent_tasks: 2
heartbeat_interval: 30
max_idle_time: 3600
```

---

### 3.4 记忆系统 (`script/memory/`)

**目标**：三层结构化记忆，支持"越用越懂我"。

#### 文件结构

```
script/memory/
├── __init__.py
├── models.py              # MemoryEntry, MemoryType 数据结构
├── store.py               # MemoryStore — 记忆读写
├── index.py               # MemoryIndex — MEMORY.md 索引管理
├── extractor.py           # MemoryExtractor — 对话后自动提取记忆
└── loader.py              # MemoryLoader — 按相关性加载记忆到 context
```

#### 记忆存储结构

```
data/memory/
├── global/                     # 全局记忆（所有分身共享）
│   ├── MEMORY.md               # 全局索引
│   ├── user_profile.md         # 用户画像
│   ├── user_preferences.md     # 偏好习惯
│   └── feedback_*.md           # 行为反馈
│
├── avatars/                    # 分身私有记忆
│   ├── coder/
│   │   ├── MEMORY.md           # 分身索引
│   │   ├── project_*.md        # 项目上下文
│   │   └── code_style.md       # 代码风格偏好
│   ├── ops/
│   │   ├── MEMORY.md
│   │   └── runbook_*.md
│   └── writer/
│       ├── MEMORY.md
│       └── writing_style.md
│
└── conversations/              # 对话归档
    ├── 2026-04-10_task_001.jsonl
    └── ...
```

#### 记忆提取器（核心：越用越懂我）

```python
# script/memory/extractor.py
class MemoryExtractor:
    """对话结束后，自动提取值得记住的内容"""

    EXTRACTION_PROMPT = """
    回顾以下对话，提取值得长期记住的信息。
    分类为：user(用户画像)、feedback(行为反馈)、project(项目信息)、reference(外部引用)。
    只提取新信息，不要重复已有记忆。
    输出 JSON 数组: [{type, name, description, content}]
    """

    async def extract(
        self,
        conversation: list[Message],
        existing_memories: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        """对话结束后调用：提取新记忆 / 更新已有记忆"""

    async def should_update(
        self, new: MemoryEntry, existing: MemoryEntry
    ) -> bool:
        """判断是更新已有记忆还是创建新记忆"""
```

#### 记忆加载器

```python
# script/memory/loader.py
class MemoryLoader:
    """根据当前上下文，加载最相关的记忆"""

    async def load_for_context(
        self,
        avatar_id: str,
        current_message: str,
        max_tokens: int = 2000,
    ) -> str:
        """
        加载策略：
        1. 始终加载: 全局 user_profile + user_preferences
        2. 始终加载: 当前分身的 MEMORY.md 索引
        3. 按相关性加载: 根据当前消息关键词匹配相关记忆文件
        4. Token 预算内尽量多加载
        """
```

---

### 3.5 心跳与监控 (`script/heartbeat/`)

**目标**：监控分身存活、任务进度、异常自愈。

#### 文件结构

```
script/heartbeat/
├── __init__.py
├── monitor.py             # HeartbeatMonitor — 心跳收集与判定
├── reporter.py            # ProgressReporter — 进度上报到 IM
└── recovery.py            # RecoveryManager — 异常自愈
```

#### 心跳协议

```python
# script/heartbeat/monitor.py
@dataclass
class Heartbeat:
    avatar_id: str
    timestamp: float
    status: AvatarStatus
    current_task: str | None
    progress: str | None          # 人类可读的进度描述
    last_tool_call: str | None    # 最近一次工具调用
    memory_usage_mb: float
    error: str | None

class HeartbeatMonitor:
    """收集和判定心跳状态"""

    async def receive(self, heartbeat: Heartbeat) -> None:
        """接收心跳"""

    async def check_stuck(self) -> list[str]:
        """检测卡住的分身（超过 N 分钟无工具调用）"""

    async def check_dead(self) -> list[str]:
        """检测已死分身（超过 M 次未收到心跳）"""
```

#### 异常自愈策略

```python
# script/heartbeat/recovery.py
class RecoveryManager:
    """分身异常时的自愈策略"""

    async def handle_stuck(self, avatar_id: str) -> None:
        """
        卡住处理：
        1. 发送中断信号，让分身总结当前状态
        2. 通知用户当前进度和卡住原因
        3. 等待用户指令（继续/重试/取消）
        """

    async def handle_dead(self, avatar_id: str) -> None:
        """
        死亡处理：
        1. 保存现场（当前对话、未完成任务状态）
        2. 重启分身实例
        3. 恢复未完成任务
        4. 通知用户
        """

    async def handle_oom(self, avatar_id: str) -> None:
        """
        上下文溢出处理：
        1. 触发 compaction（已有能力）
        2. 如果仍然超限，保存中间结果，新开对话继续
        """
```

#### 进度推送

```python
# script/heartbeat/reporter.py
class ProgressReporter:
    """定期向用户推送任务进度"""

    async def report(self, task: Task, heartbeat: Heartbeat) -> None:
        """
        推送策略：
        - 任务开始：立即通知
        - 执行中：每 5 分钟（或关键里程碑）推送一次
        - 等待用户：立即通知
        - 完成/失败：立即通知
        - 不要刷屏：合并短时间内的多条更新
        """
```

---

### 3.6 mini_claude 引擎的改造点

mini_claude 作为执行引擎，需要从"交互式终端工具"改造为"可被 mini_claw 远程调用的引擎"。
这些改动在 `mini_claude/` 仓库中完成。

| 改造项 | 现状 | 目标 |
|--------|------|------|
| 入口 | `main.py` → REPL 循环 | 新增 `run_headless(task)` 无头模式 |
| 输入 | 键盘 stdin | 纯文本 / 结构化输入 |
| 输出 | Rich 终端渲染 | 返回纯文本结果 + 回调 |
| 上下文 | 单会话 | 支持多会话并行（独立 AppState） |
| 权限 | 交互式确认 | 支持预授权白名单（无人值守） |
| 持久化 | 本地 JSON | 支持自定义存储路径 |

#### 需要在 mini_claude 中修改的文件

```
mini_claude/src/
├── engine/
│   ├── query.py           # [改] 新增 run_headless() 方法
│   └── headless.py        # [新] 无头执行入口，供 mini_claw 调用
├── models/
│   └── state.py           # [改] AppState 支持多实例隔离
├── services/
│   ├── permissions.py     # [改] 支持预授权白名单
│   └── persistence.py     # [改] 支持自定义存储路径
└── ui/
    └── headless_ui.py     # [新] 无 UI 输出适配器（结果回调）
```

#### mini_claw 调用 mini_claude 的方式

```python
# mini_claw 中的调用示例
from src.engine.headless import create_engine

engine = await create_engine(working_dir="/path/to/workspace", permission_mode="auto")
result = await engine.run_turn("帮我看看当前目录有哪些文件")
```

---

## 四、部署方案

### Docker Compose（单进程架构）

当前采用单镜像单服务架构，所有组件（网关、调度、分身、心跳）在一个进程内运行。

```yaml
# mini_claw/deploy/docker-compose.yml
services:
  mini_claw:
    build:
      context: ../..                    # Codeclaw 根目录
      dockerfile: mini_claw/deploy/Dockerfile
    ports:
      - "${MINI_CLAW_PORT:-8080}:8080"
    env_file:
      - .env                            # API keys 等敏感配置
    volumes:
      - mini_claw_/app/mini_claw/data       # 记忆持久化
      - mini_claw_logs:/app/mini_claw/logs       # 消息日志
      - mini_claw_workspace:/workspace           # 引擎工作目录
    restart: unless-stopped

volumes:
  mini_claw_
  mini_claw_logs:
  mini_claw_workspace:
```

### Dockerfile

```dockerfile
# mini_claw/deploy/Dockerfile — build context = Codeclaw 根目录
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# 分层安装：先装 mini_claude，再装 mini_claw（利用 Docker 缓存）
COPY mini_claude/ mini_claude/
RUN pip install --no-cache-dir ./mini_claude/

COPY mini_claw/ mini_claw/
RUN pip install --no-cache-dir ./mini_claw/

RUN mkdir -p /app/mini_claw/data/memory /app/mini_claw/logs

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

ENV MINI_CLAW_WORKING_DIR=/workspace MINI_CLAW_PORT=8080
EXPOSE 8080
CMD ["mini_claw"]
```

---

## 五、开发路线

### P0 — 跑通一条链路 ✅ (代码完成，待端到端验证)

**目标**：Telegram 发消息 → mini_claw 接收 → 调用 mini_claude 引擎执行 → 结果回传

**前置条件**：
- 注册 Telegram Bot（@BotFather）
- 有一台可访问外网的服务器（或本地 ngrok）

**步骤**：

```
P0.1  ✅ mini_claude 引擎改造（在 mini_claude/ 中）
      ├── 新增 mini_claude/src/engine/headless.py
      ├── create_engine() 工厂函数 + run_headless() 便捷函数
      ├── 无需终端 UI，直接返回文本结果
      └── 导入方式: from src.engine.headless import create_engine

P0.2  ✅ mini_claw 基础骨架 + Telegram Adapter（在 mini_claw/ 中）
      ├── 包目录: mini_claw/script/ (避免与 mini_claude 的 src/ 冲突)
      ├── script/gateway/adapters/telegram.py — polling 模式
      ├── /start /reset /status 命令，长消息自动分段
      └── 测试：手机 Telegram 发消息，Bot 回复结果

P0.3  ✅ Webhook Adapter（调试用）
      ├── script/gateway/adapters/webhook.py
      ├── POST /message, POST /reset/{user_id}, GET /health, GET /status
      └── 14/14 单元测试通过

P0.4  ⬜ 端到端测试
      ├── 手机发 "帮我看看项目有哪些文件"
      ├── mini_claw 接收 → 调用 mini_claude 引擎的 glob 工具
      └── 返回文件列表到手机
```

**已完成的文件**：
- `mini_claude/src/engine/headless.py` — 无头引擎入口
- `mini_claw/script/__init__.py` — 包入口
- `mini_claw/script/config.py` — YAML + 环境变量配置
- `mini_claw/script/main.py` — 主入口 (uvicorn + telegram)
- `mini_claw/script/engine_session.py` — 会话管理 (SessionManager)
- `mini_claw/script/gateway/models.py` — UnifiedMessage, BotResponse
- `mini_claw/script/gateway/adapters/telegram.py` — Telegram Bot
- `mini_claw/script/gateway/adapters/webhook.py` — FastAPI Webhook
- `mini_claw/config/settings.example.yaml` — 配置模板
- `mini_claw/tests/test_engine_session.py` — 8 个测试
- `mini_claw/tests/test_webhook.py` — 6 个测试

**新增依赖**：`python-telegram-bot`, `fastapi`, `uvicorn`

---

### P1 — 记忆系统

**目标**：对话后自动提取记忆，下次对话加载相关上下文

```
P1.1  记忆数据结构
      ├── 新增 mini_claw/script/memory/models.py
      ├── MemoryEntry, MemoryType
      └── 定义 frontmatter 格式

P1.2  记忆存储层
      ├── 新增 mini_claw/script/memory/store.py
      ├── 读写记忆文件到 data/memory/
      └── 管理 MEMORY.md 索引

P1.3  记忆提取器
      ├── 新增 mini_claw/script/memory/extractor.py
      ├── 对话结束 → LLM 提取值得记住的信息
      ├── 分类: user / feedback / project / reference
      └── 去重: 对比已有记忆，决定新增/更新

P1.4  记忆加载器
      ├── 新增 mini_claw/script/memory/loader.py
      ├── 每次对话开始 → 加载全局记忆 + 相关记忆
      └── Token 预算控制

P1.5  集成到分身运行器
      ├── mini_claw AvatarRunner: 对话结束触发 extractor
      ├── mini_claw AvatarRunner: 对话开始加载记忆注入 mini_claude context
      └── 端到端: 聊过的偏好，下次自动体现
```

---

### P2 — 多分身 + 心跳

**目标**：多个分身并行运行，心跳监控 + 进度推送

```
P2.1  分身配置系统
      ├── 新增 mini_claw/script/avatar/models.py
      ├── 新增 mini_claw/script/avatar/presets/*.yaml
      └── AvatarConfig 加载与验证

P2.2  分身管理器
      ├── 新增 mini_claw/script/avatar/manager.py
      ├── 常驻分身启动/休眠/唤醒
      ├── 临时分身派生/回收
      └── 进程隔离: 每个分身独立 asyncio loop（或子进程）

P2.3  任务调度
      ├── 新增 mini_claw/script/scheduler/
      ├── Redis 任务队列
      ├── 意图识别 → 分身路由（显式指定 + LLM 分类）
      └── 子任务拆分与聚合

P2.4  心跳系统
      ├── 新增 mini_claw/script/heartbeat/
      ├── 分身定期上报状态
      ├── 卡住检测 + 死亡检测
      └── 自愈策略（重启 / 通知用户）

P2.5  进度推送
      ├── 新增 mini_claw/script/heartbeat/reporter.py
      ├── 关键里程碑推送到 IM
      └── 防刷屏: 合并短时间多条更新

P2.6  分身间通信
      ├── 常驻分身向调度中心请求派生临时分身
      ├── 临时分身完成 → 结果回传给父分身
      └── Redis pub/sub 做消息通道
```

---

### P3 — 多平台 + 权限 ✅

**目标**：接入飞书/企微，完善权限体系

```
P3.1  ✅ BaseAdapter 抽象基类
      ├── 新增 script/gateway/base_adapter.py
      ├── start/stop/send 抽象方法
      ├── on_message 回调注册 + _dispatch 统一分发
      └── 飞书/企微适配器继承此基类

P3.2  ✅ 飞书 Adapter
      ├── 新增 script/gateway/adapters/feishu.py
      ├── 事件订阅模式 (FastAPI 路由注册)
      ├── URL 验证 (challenge)、消息处理、事件去重
      └── tenant_access_token 自动获取与缓存

P3.3  ✅ 企微 Adapter
      ├── 新增 script/gateway/adapters/wecom.py
      ├── XML 回调解析 + Webhook/应用消息双模式
      └── access_token 自动获取与缓存

P3.4  ✅ 用户权限系统
      ├── 新增 script/gateway/middleware/auth.py
      ├── 三级权限: ADMIN / USER / READONLY
      ├── 白名单 + 角色覆盖
      └── 操作级权限检查 (chat, reset, admin, view_status)

P3.5  ✅ 消息中间件
      ├── 新增 script/gateway/middleware/rate_limit.py — 令牌桶限流
      ├── 新增 script/gateway/middleware/logging_mw.py — JSONL 审计日志
      └── 中间件链: 日志 → 鉴权 → 限流 → 引擎

P3.6  ✅ 集成 + 配置 + 测试
      ├── config.py 新增 FeishuConfig, WecomConfig, MiddlewareConfig
      ├── main.py 集成中间件链 + 飞书/企微适配器
      └── 33/33 P3 测试通过，全量 105/105 通过
```

**已完成的文件**:
- `script/gateway/base_adapter.py` — 适配器抽象基类
- `script/gateway/adapters/feishu.py` — 飞书适配器
- `script/gateway/adapters/wecom.py` — 企微适配器
- `script/gateway/middleware/__init__.py` — 中间件包
- `script/gateway/middleware/auth.py` — 鉴权中间件
- `script/gateway/middleware/rate_limit.py` — 限流中间件
- `script/gateway/middleware/logging_mw.py` — 日志中间件
- `tests/test_gateway.py` — 33 个测试

---

### P4 — 云部署 + 7x24 ✅

**目标**：Docker Compose 一键部署，稳定运行

```
P4.1  ✅ Dockerfile
      ├── 新增 deploy/Dockerfile — 单镜像打包 mini_claude + mini_claw
      ├── python:3.11-slim + git
      ├── HEALTHCHECK 内置（python urllib）
      └── CMD mini_claw

P4.2  ✅ docker-compose.yml
      ├── 新增 deploy/docker-compose.yml
      ├── 3 个 volume: data（记忆）、logs（日志）、workspace（引擎）
      ├── env_file 注入敏感配置
      └── restart: unless-stopped

P4.3  ✅ 配置管理
      ├── 新增 deploy/.env.example — 所有环境变量模板
      ├── 更新 config/settings.example.yaml — 完整配置模板
      ├── config.py 新增 memory_root 可配
      └── 环境变量 > YAML > 默认值 三级优先级

P4.4  ✅ 数据持久化
      ├── 记忆文件 → mini_claw_data volume
      ├── 消息日志 → mini_claw_logs volume（默认写 logs/）
      └── 引擎工作目录 → mini_claw_workspace volume

P4.5  ✅ 运维工具
      ├── 健康检查端点 /health（已有，Dockerfile HEALTHCHECK 引用）
      ├── 新增 deploy/start.sh — 一键启动
      └── 新增 deploy/update.sh — git pull + rebuild + restart
```

**已完成的文件**:
- `deploy/Dockerfile` — 生产镜像
- `deploy/docker-compose.yml` — 编排配置
- `deploy/.env.example` — 环境变量模板
- `deploy/start.sh` — 一键启动脚本
- `deploy/update.sh` — 更新重启脚本
- `config/settings.example.yaml` — 完整配置模板（含 P3 新增项）

**部署方式**:
```bash
cd mini_claw/deploy
cp .env.example .env   # 编辑填入 API keys
bash start.sh          # 构建 + 启动
```

---

## 六、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| Web 框架 | FastAPI | 异步原生，与现有 async 代码一致 |
| Telegram SDK | python-telegram-bot v20+ | 原生 asyncio |
| 消息队列 | Redis Streams | 轻量，够用，Docker 一行起 |
| 心跳存储 | Redis Hash | TTL 天然支持过期检测 |
| 记忆存储 | 文件系统 (Markdown) | 与现有 MEMORY.md 方案一致，可读可编辑 |
| 进程管理 | asyncio + subprocess | 分身隔离，不引入额外框架 |
| 容器化 | Docker Compose | 单机够用，架构预留扩展 |
| 新增依赖 | `fastapi`, `uvicorn`, `redis[hiredis]`, `python-telegram-bot` | 最小依赖集 |

---

## 七、目录结构总览（当前实际结构）

```
Codeclaw/                          # 项目根目录
│
├── mini_claude/                   # 执行引擎（已有，独立包）
│   ├── src/                      # 包名: src
│   │   ├── main.py               # 终端 REPL 入口
│   │   ├── engine/
│   │   │   ├── query.py          # QueryEngine 对话循环
│   │   │   └── headless.py       # 无头执行入口（供 mini_claw 调用）
│   │   ├── models/
│   │   ├── services/
│   │   ├── tools/                # 11 个工具实现
│   │   └── ui/
│   ├── tests/
│   └── pyproject.toml
│
├── mini_claw/                     # 云平台（本 plan 的主体）
│   ├── script/                   # 包名: script（避免与 mini_claude 的 src/ 冲突）
│   │   ├── __init__.py
│   │   ├── config.py             # YAML + 环境变量配置
│   │   ├── main.py               # 云平台入口（uvicorn + 适配器 + 分身 + 心跳）
│   │   ├── engine_session.py     # 每用户 QueryEngine 会话管理
│   │   │
│   │   ├── gateway/              # IM 网关
│   │   │   ├── models.py         # UnifiedMessage, BotResponse
│   │   │   ├── base_adapter.py   # BaseAdapter 抽象基类
│   │   │   ├── adapters/
│   │   │   │   ├── telegram.py   # Telegram Bot（polling 模式）
│   │   │   │   ├── feishu.py     # 飞书（事件订阅模式）
│   │   │   │   ├── wecom.py      # 企微（XML 回调 + Webhook 双模式）
│   │   │   │   └── webhook.py    # HTTP API（调试用）
│   │   │   └── middleware/
│   │   │       ├── auth.py       # 鉴权（ADMIN/USER/READONLY）
│   │   │       ├── rate_limit.py # 令牌桶限流
│   │   │       └── logging_mw.py # JSONL 审计日志
│   │   │
│   │   ├── avatar/               # 分身系统
│   │   │   ├── models.py         # Avatar, AvatarConfig
│   │   │   ├── manager.py        # AvatarManager 生命周期管理
│   │   │   ├── runner.py         # AvatarRunner 执行循环
│   │   │   └── presets/          # 预置角色配置
│   │   │       ├── coder.yaml
│   │   │       ├── ops.yaml
│   │   │       └── general.yaml
│   │   │
│   │   ├── memory/               # 记忆系统
│   │   │   ├── models.py         # MemoryEntry, MemoryType
│   │   │   ├── store.py          # MemoryStore 读写
│   │   │   ├── extractor.py      # MemoryExtractor LLM 提取
│   │   │   └── loader.py         # MemoryLoader 按相关性加载
│   │   │
│   │   ├── scheduler/            # 调度中心
│   │   │   ├── models.py         # Task, TaskStatus
│   │   │   ├── router.py         # TaskRouter 意图识别 + 分身路由
│   │   │   └── task_manager.py   # 任务 CRUD + 生命周期
│   │   │
│   │   └── heartbeat/            # 心跳监控
│   │       ├── monitor.py        # HeartbeatMonitor 心跳收集与判定
│   │       ├── reporter.py       # ProgressReporter 进度上报
│   │       └── recovery.py       # RecoveryManager 异常自愈
│   │
│   ├── deploy/                   # Docker 部署
│   │   ├── Dockerfile            # 单镜像（mini_claude + mini_claw）
│   │   ├── docker-compose.yml    # 单服务 + 3 个持久化 volume
│   │   ├── .env.example          # 环境变量模板
│   │   ├── start.sh              # 一键启动
│   │   └── update.sh             # 更新重启
│   │
│   ├── config/
│   │   └── settings.example.yaml # 完整配置模板
│   │
│   ├── data/                     # [运行时] 记忆数据（.gitignore）
│   ├── logs/                     # [运行时] 消息日志（.gitignore）
│   │
│   ├── tests/                    # 105 个测试
│   │   ├── test_engine_session.py
│   │   ├── test_webhook.py
│   │   ├── test_memory.py
│   │   ├── test_avatar.py
│   │   ├── test_scheduler.py
│   │   ├── test_heartbeat.py
│   │   └── test_gateway.py
│   │
│   ├── docs/
│   │   └── CODECLAW_PLAN.md      # 本文件
│   │
│   └── pyproject.toml
│
├── CLAUDE.md                     # 英文版项目指引
└── CLAUDE.zh.md                  # 中文版项目指引
```

---

## 八、风险与注意事项

1. **API 费用**：记忆提取器每次对话多一次 LLM 调用，建议用便宜模型（haiku）做提取
2. **安全性**：无人值守模式下工具权限必须收紧，bash 执行需要沙箱
3. **Telegram 限制**：长消息需分段发送，文件大小有限制
4. **并发安全**：多分身写同一个记忆文件需要文件锁
5. **Token 消耗**：记忆加载占用 context，需严格控制 token 预算
6. **冷启动**：首次部署需手动配置 Bot Token、API Key 等

---

## 九、成功标准

- [x] P0: 代码实现完成，14/14 单元测试通过，待端到端验证
- [x] P1: 记忆系统完成，24/24 测试通过
- [x] P2: 多分身 + 心跳完成，34/34 测试通过
- [x] P3: 多平台 + 权限完成，33/33 测试通过，全量 105/105
- [x] P4: Docker 部署完成，Dockerfile + docker-compose + 运维脚本
