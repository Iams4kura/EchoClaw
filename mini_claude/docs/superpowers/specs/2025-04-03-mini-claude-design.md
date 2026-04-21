# Mini Claude Design Document

**Date:** 2025-04-03  
**Project:** mini_claude - Python Implementation of Claude Code Core  
**Reference:** /src (TypeScript Claude Code source)

## 1. Executive Summary

Mini Claude is a terminal-based AI coding assistant implemented in Python, replicating Claude Code's core architecture and advanced engineering patterns while maintaining a mini footprint for educational and practical use.

### Core Features
- **11 Tools:** Bash, FileRead, FileWrite, FileEdit, Glob, Grep, Agent, WebSearch, Todo, Task, AskUser, Skill
- **Advanced Patterns:** Parallel tool orchestration, 5-layer context compression, streaming execution, permission system
- **UI Framework:** Rich for terminal rendering with Live updates and global reactive state
- **API Layer:** LiteLLM proxy supporting multiple LLM providers via OpenAI-compatible interface

---

## 2. System Architecture

### 2.1 Five-Layer Implementation

```
Layer 5: UI Layer (Rich Live, panels, streaming display)
  ↑
Layer 4: Core Loop (QueryEngine, message flow, tool dispatch)
  ↑
Layer 3: Tool System (BaseTool class, registry, streaming execution)
  ↑
Layer 2: Services (LLM client, API retry, context management, permissions)
  ↑
Layer 1: Foundation (config, state models, persistence, git integration)
```

### 2.2 Module Dependencies

```
mini_claude/
├── config/              # Configuration (Layer 1)
├── models/              # Data models: Message, AppState, AgentState (Layer 1)
├── utils/               # Token estimation, git status, file helpers (Layer 1)
├── services/
│   ├── llm.py          # LiteLLM client + retry + streaming (Layer 2)
│   ├── context.py      # Context assembly, CLAUDE.md loading (Layer 2)
│   ├── permissions.py  # Permission classification + rules (Layer 2)
│   └── compaction.py   # 5-layer context compression (Layer 2)
├── tools/
│   ├── base.py         # BaseTool abstract class (Layer 3)
│   ├── registry.py     # Tool discovery and registry (Layer 3)
│   ├── orchestration.py # Parallel tool scheduling (Layer 3)
│   └── [11 tools]/     # Individual tool implementations (Layer 3)
├── engine/
│   ├── query.py        # Main conversation loop (Layer 4)
│   └── streaming.py    # Streaming response handling (Layer 4)
├── ui/
│   ├── app.py          # Rich UI app with Live display (Layer 5)
│   ├── components.py   # Reusable UI components (Layer 5)
│   └── state_sync.py   # State → UI bindings (Layer 5)
└── main.py             # CLI entry point
```

---

## 3. Core Data Models

### 3.1 Message (Compatible with Anthropic SDK)

```python
from dataclasses import dataclass
from typing import Literal, Optional, Any
from datetime import datetime

@dataclass
class TextBlock:
    type: Literal["text"]
    text: str

@dataclass
class ToolUseBlock:
    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, Any]

@dataclass
class ToolResultBlock:
    type: Literal["tool_result"]
    tool_use_id: str
    content: str | list[Any]
    is_error: bool = False

MessageContent = TextBlock | ToolUseBlock | ToolResultBlock

@dataclass
class Message:
    role: Literal["user", "assistant", "system"]
    content: list[MessageContent] | str
    timestamp: datetime
    metadata: dict[str, Any] = None
    # For context compression tracking
    original_length: Optional[int] = None
    is_summarized: bool = False
```

### 3.2 AppState (Global Reactive State)

Reference: `src/state/AppState.tsx`

```python
from typing import Dict, List, Optional
from dataclasses import dataclass, field
import asyncio

@dataclass
class TaskInfo:
    task_id: str
    type: Literal["bash", "agent"]
    status: Literal["pending", "running", "completed", "failed"]
    description: str
    start_time: float
    output_file: Optional[str] = None
    output_offset: int = 0

@dataclass
class AgentInfo:
    agent_id: str
    name: str
    color: str  # From AGENT_COLORS palette
    model: str
    status: Literal["idle", "running", "completed", "failed"]
    parent_tool_use_id: Optional[str]
    messages: List[Message] = field(default_factory=list)
    assigned_files: List[str] = field(default_factory=list)
    # For Agent communication
    can_communicate_with: List[str] = field(default_factory=list)

@dataclass
class AppState:
    # Conversation
    messages: List[Message] = field(default_factory=list)
    system_prompt: str = ""
    
    # Session metadata
    session_id: str = field(default_factory=lambda: generate_session_id())
    working_dir: str = field(default_factory=os.getcwd)
    
    # Active operations
    active_tasks: Dict[str, TaskInfo] = field(default_factory=dict)
    active_agents: Dict[str, AgentInfo] = field(default_factory=dict)
    
    # UI state
    is_streaming: bool = False
    current_tool_use: Optional[str] = None
    pending_tool_uses: List[str] = field(default_factory=list)
    
    # Streaming buffer
    stream_buffer: str = ""
    
    # Cancellation
    abort_controller: Optional[asyncio.CancelScope] = None
    
    # Context tracking for compaction
    total_tokens: int = 0
    compact_boundary_index: int = 0  # Messages before this are compacted
```

### 3.3 AgentState Lifecycle

Reference: `src/tools/AgentTool/`, `src/Task.ts`

```python
class AgentStateMachine:
    STATES = ["pending", "running", "completed", "failed", "killed"]
    
    @staticmethod
    def is_terminal(status: str) -> bool:
        return status in ["completed", "failed", "killed"]
    
    @staticmethod
    def can_transition(from_state: str, to_state: str) -> bool:
        # Running -> [completed, failed, killed]
        # Pending -> [running, killed]
        # Terminal states are final
        pass
```

---

## 4. Module Detailed Design

### 4.1 Tools System (Layer 3)

#### 4.1.1 BaseTool Abstract Class

Reference: `src/Tool.ts`

```python
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, TypedDict
import json

class ToolResult(TypedDict):
    content: str | list[Any]
    is_error: bool

class ToolChunk(TypedDict):
    type: Literal["text", "error", "end"]
    content: str

class BaseTool(ABC):
    """Abstract base class for all tools."""
    
    # Tool metadata
    name: str
    description: str
    input_schema: dict  # JSON Schema
    
    # Optional: streaming output support
    supports_streaming: bool = False
    
    # Permission classification
    permission_category: Literal["read", "write", "destructive", "external"] = "read"
    
    @abstractmethod
    async def execute(self, params: dict, signal: asyncio.CancelScope) -> ToolResult:
        """Execute the tool with given parameters."""
        pass
    
    async def execute_streaming(
        self, params: dict, signal: asyncio.CancelScope
    ) -> AsyncIterator[ToolChunk]:
        """Override for streaming tools (like Bash with live output)."""
        raise NotImplementedError("This tool doesn't support streaming")
```

#### 4.1.2 Tool Registry

```python
class ToolRegistry:
    """Central registry for tool discovery and execution."""
    
    _tools: Dict[str, BaseTool] = {}
    
    @classmethod
    def register(cls, tool_instance: BaseTool):
        cls._tools[tool_instance.name] = tool_instance
    
    @classmethod
    def get(cls, name: str) -> Optional[BaseTool]:
        # Support name normalization (BashTool -> Bash)
        normalized = name.replace("Tool", "").lower()
        for key, tool in cls._tools.items():
            if key.lower() == normalized or key.lower() == name.lower():
                return tool
        return None
    
    @classmethod
    def get_tools_for_prompt(cls) -> list[dict]:
        """Generate tool definitions for LLM system prompt."""
        return [{
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema
        } for tool in cls._tools.values()]
```

#### 4.1.3 Tools List (11 Tools)

| Tool | Category | Streaming | Reference Source |
|------|----------|-----------|------------------|
| **Bash** | external | Yes | `src/tools/BashTool/BashTool.ts` |
| **FileRead** | read | No | `src/tools/FileReadTool/FileReadTool.ts` |
| **FileWrite** | write | No | `src/tools/FileWriteTool/FileWriteTool.ts` |
| **FileEdit** | destructive | No | `src/tools/FileEditTool/FileEditTool.ts` |
| **Glob** | read | No | `src/tools/GlobTool/GlobTool.ts` |
| **Grep** | read | No | `src/tools/GrepTool/GrepTool.ts` |
| **Agent** | external | Yes (progress) | `src/tools/AgentTool/AgentTool.ts` |
| **WebSearch** | external | No | `src/tools/WebSearchTool/` |
| **Todo** | write | No | `src/tools/TodoWriteTool/TodoWriteTool.ts` |
| **Task** | external | Yes | `src/tools/TaskStopTool/TaskStopTool.ts` |
| **AskUser** | read | Yes (dialog) | `src/tools/AskUserQuestionTool/AskUserQuestionTool.ts` |
| **Skill** | read | No | `src/tools/SkillTool/SkillTool.ts` |

### 4.2 LLM Client (Layer 2)

#### 4.2.1 LiteLLM Integration

```python
import litellm
from tenacity import retry, stop_after_attempt, wait_exponential

class LLMClient:
    """LiteLLM-based client with retry and streaming."""
    
    def __init__(self, config: Config):
        self.model = config.model  # e.g., "anthropic/claude-3-5-sonnet-20241022"
        self.api_key = config.api_key
        self.base_url = config.base_url  # For custom endpoints
        
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((litellm.Timeout, litellm.RateLimitError))
    )
    async def complete(
        self,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        signal: asyncio.CancelScope = None
    ) -> Message:
        """Non-streaming completion with retry."""
        pass
    
    async def complete_streaming(
        self,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
    ) -> AsyncIterator[MessageChunk]:
        """Streaming completion for real-time display."""
        pass
```

#### 4.2.2 Context Assembly

Reference: `src/context.ts`

```python
class ContextAssembler:
    """Assembles system and user context."""
    
    async def get_system_context(self) -> dict:
        """Git status, current date, etc."""
        git_status = await get_git_status() if is_git_repo() else None
        return {
            "git_status": git_status,
            "current_date": datetime.now().isoformat(),
        }
    
    async def get_user_context(self) -> dict:
        """CLAUDE.md, memory files."""
        claude_md = await self.load_claude_md()
        return {
            "claude_md": claude_md,
        }
    
    def build_system_prompt(self, system_ctx: dict, user_ctx: dict) -> str:
        """Combine contexts into final system prompt."""
        pass
```

### 4.3 Parallel Tool Orchestration

Reference: `src/services/tools/toolOrchestration.ts`, `src/utils/toolGrouping.ts`

```python
class ToolOrchestrator:
    """Analyzes tool calls and schedules parallel execution."""
    
    def analyze_dependencies(self, tool_uses: list[ToolUseBlock]) -> ExecutionGraph:
        """
        Build dependency graph for tool calls.
        
        Tools are independent if:
        - They don't depend on each other's outputs
        - They don't modify the same files
        - They don't have parent-child relationships
        
        Returns: Graph of which tools can run in parallel.
        """
        pass
    
    async def execute_parallel(
        self,
        tool_uses: list[ToolUseBlock],
        state: AppState
    ) -> list[ToolResult]:
        """
        Execute tools optimizing for parallelism.
        
        1. Group independent tools
        2. Execute each group in parallel (asyncio.gather)
        3. Wait for group completion before dependent tools
        4. Stream all results simultaneously to UI
        """
        pass
```

### 4.4 Context Compression (5-Layer)

Reference: `src/utils/tokens.ts`, `src/services/compact/`

```python
class ContextCompactor:
    """
    5-layer progressive context compression strategy.
    Triggered when approaching token limit or on 'prompt_too_long' error.
    """
    
    STRATEGIES = [
        "remove_tool_result_details",    # Keep summaries, remove full output
        "remove_assistant_thinking",     # Remove detailed reasoning
        "summarize_messages",           # LLM-based message compression
        "remove_early_tool_calls",      # Remove oldest tool blocks
        "hard_truncation",              # Final resort: keep most recent
    ]
    
    def estimate_tokens(self, messages: list[Message]) -> int:
        """Token estimation using tiktoken or similar."""
        pass
    
    async def compact(self, messages: list[Message], target_tokens: int) -> list[Message]:
        """Apply strategies progressively until under limit."""
        current = messages.copy()
        
        for strategy in self.STRATEGIES:
            if self.estimate_tokens(current) <= target_tokens:
                break
            current = await self._apply_strategy(current, strategy)
            
        return current
    
    async def _summarize_messages(self, messages: list[Message]) -> list[Message]:
        """Use LLM to compress message history to summary."""
        pass
```

### 4.5 Permissions System

Reference: `src/hooks/useCanUseTool.tsx`, `src/types/permissions.ts`

```python
class PermissionCategory(Enum):
    READ = "read"           # FileRead, Glob, Grep - generally safe
    WRITE = "write"         # FileWrite, Todo - modifying state
    DESTRUCTIVE = "destructive"  # FileEdit, delete operations
    EXTERNAL = "external"   # Bash, WebSearch, Agent - external world

@dataclass
class PermissionRule:
    category: PermissionCategory
    mode: Literal["ask", "auto_approve", "auto_deny"]
    patterns: Optional[list[str]] = None  # File path patterns, command patterns

class PermissionManager:
    """Manages tool execution permissions."""
    
    def __init__(self, config: Config):
        self.rules = config.permission_rules
        self.mode = config.permission_mode  # "ask", "auto", "restricted"
    
    def check_permission(
        self,
        tool: BaseTool,
        params: dict
    ) -> PermissionResult:
        """
        Check if tool execution is allowed.
        
        Returns:
        - auto_allow: Execute immediately
        - ask: Prompt user for confirmation
        - deny: Reject execution
        """
        # Check category-based rules
        # Check path patterns for file operations
        # Check command patterns for bash
        pass
```

### 4.6 Agent Subsystem

Reference: `src/tools/AgentTool/`

```python
AGENT_COLORS = [
    "blue", "green", "yellow", "purple", 
    "cyan", "magenta", "red", "white"
]

class AgentColorManager:
    """Assigns unique colors to agents for UI distinction."""
    
    _assigned: Dict[str, str] = {}  # agent_id -> color
    _available: list[str] = AGENT_COLORS.copy()
    
    @classmethod
    def assign(cls, agent_id: str, name: str) -> str:
        """Assign color to new agent, or return existing."""
        if agent_id in cls._assigned:
            return cls._assigned[agent_id]
        color = cls._available.pop(0) if cls._available else "gray"
        cls._assigned[agent_id] = color
        return color

class AgentRunner:
    """
    Runs an Agent as a long-lived sub-conversation.
    
    Unlike regular tools, Agents:
    - Maintain their own message history
    - Can execute tools independently
    - Report progress incrementally
    - Can be communicated with by name
    - Have parent reference to originating tool_use
    """
    
    async def spawn(
        self,
        name: str,
        prompt: str,
        parent_tool_use_id: str,
        state: AppState
    ) -> str:
        """Spawn new agent, return agent_id."""
        agent_id = generate_agent_id()
        color = AgentColorManager.assign(agent_id, name)
        
        agent_info = AgentInfo(
            agent_id=agent_id,
            name=name,
            color=color,
            model=state.preferred_model,  # or specified
            status="running",
            parent_tool_use_id=parent_tool_use_id,
        )
        
        state.active_agents[agent_id] = agent_info
        
        # Start agent execution in background
        asyncio.create_task(self._run_agent(agent_info, prompt, state))
        
        return agent_id
    
    async def _run_agent(self, agent: AgentInfo, prompt: str, state: AppState):
        """Main agent execution loop (similar to main query loop)."""
        # Build agent system prompt with teammate addendum
        # Run conversation loop
        # Stream output to UI via state updates
        pass
```

---

## 5. UI Implementation

### 5.1 Rich Components

```python
from rich.live import Live
from rich.panel import Panel
from rich.console import Group
from rich.layout import Layout

class MiniClaudeUI:
    """Main UI application using Rich."""
    
    def __init__(self, state: AppState):
        self.state = state
        self.console = Console()
        self.layout = self._create_layout()
        
    def _create_layout(self) -> Layout:
        """Create responsive layout."""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="input", size=3),
        )
        layout["main"].split_row(
            Layout(name="chat", ratio=3),
            Layout(name="sidebar", ratio=1, visible=False),  # Agents/Tasks panel
        )
        return layout
    
    async def run(self):
        """Main UI loop."""
        with Live(self.layout, refresh_per_second=4) as live:
            while True:
                self._update_display()
                await asyncio.sleep(0.25)  # 4 FPS refresh
    
    def _update_display(self):
        """Render current state."""
        # Chat panel: conversation history
        # Agent panel: active agents with colors
        # Task panel: running tasks
        # Input panel: current user input
        pass
```

### 5.2 State Synchronization

```python
class ReactiveState:
    """Wrapper around AppState that triggers UI updates."""
    
    def __init__(self, state: AppState, ui: MiniClaudeUI):
        self._state = state
        self._ui = ui
        self._lock = asyncio.Lock()
    
    async def update(self, updater: Callable[[AppState], AppState]):
        """Thread-safe state update that triggers UI refresh."""
        async with self._lock:
            self._state = updater(self._state)
            # UI Live display auto-refreshes
```

---

## 6. Directory Structure

```
mini_claude/
├── mini_claude/              # Main package
│   ├── __init__.py
│   ├── __main__.py          # python -m mini_claude entry
│   ├── main.py              # CLI entry point
│   │
│   ├── config/              # Layer 1: Configuration
│   │   ├── __init__.py
│   │   ├── settings.py      # Config class, loading
│   │   └── defaults.py      # Default values
│   │
│   ├── models/              # Layer 1: Data Models
│   │   ├── __init__.py
│   │   ├── message.py       # Message, content blocks
│   │   ├── state.py         # AppState, TaskInfo, AgentInfo
│   │   └── tool.py          # Tool-related types
│   │
│   ├── utils/               # Layer 1: Utilities
│   │   ├── __init__.py
│   │   ├── tokens.py        # Token estimation
│   │   ├── git.py           # Git operations
│   │   ├── files.py         # File helpers
│   │   └── ids.py           # ID generators
│   │
│   ├── services/            # Layer 2: Business Logic
│   │   ├── __init__.py
│   │   ├── llm.py           # LiteLLM client
│   │   ├── context.py       # Context assembly
│   │   ├── permissions.py   # Permission system
│   │   ├── compaction.py    # 5-layer compression
│   │   └── persistence.py   # Session save/load
│   │
│   ├── tools/               # Layer 3: Tool System
│   │   ├── __init__.py
│   │   ├── base.py          # BaseTool class
│   │   ├── registry.py      # Tool registration
│   │   ├── orchestration.py # Parallel execution
│   │   ├── bash.py          # BashTool
│   │   ├── file_read.py
│   │   ├── file_write.py
│   │   ├── file_edit.py     # With diff/Patch
│   │   ├── glob_tool.py
│   │   ├── grep_tool.py
│   │   ├── agent/           # Agent subsystem
│   │   │   ├── __init__.py
│   │   │   ├── tool.py      # AgentTool
│   │   │   ├── runner.py    # Agent execution
│   │   │   └── color.py     # Color manager
│   │   ├── web_search.py
│   │   ├── todo.py
│   │   ├── task.py
│   │   ├── ask_user.py
│   │   └── skill.py
│   │
│   ├── engine/              # Layer 4: Core Loop
│   │   ├── __init__.py
│   │   ├── query.py         # Main conversation loop
│   │   └── streaming.py     # Stream handling
│   │
│   └── ui/                  # Layer 5: User Interface
│       ├── __init__.py
│       ├── app.py           # Rich UI main app
│       ├── components.py    # Reusable components
│       ├── panels.py        # Chat, Agent, Task panels
│       └── state_sync.py    # Reactive bindings
│
├── config/                  # Runtime configuration (创建于 init)
│   ├── settings.yaml        # User settings
│   ├── claude.md            # CLAUDE.md for this project
│   └── sessions/            # Saved conversation sessions
│       └── 2025-04-03-xxx.json
│
├── tests/                   # Test suite
│   ├── unit/
│   └── integration/
│
├── docs/                    # Documentation
│   └── superpowers/
│       └── specs/
│           └── 2025-04-03-mini-claude-design.md  # This file
│
├── pyproject.toml           # Package metadata
├── requirements.txt         # Dependencies
└── README.md
```

---

## 7. Startup Flow

```python
# main.py

async def main():
    # 1. Environment checks
    check_python_version()  # >= 3.10
    
    # 2. Configuration loading
    config = load_config()
    # Priority: env vars > local config > global config > defaults
    
    # 3. State initialization
    state = AppState(
        working_dir=os.getcwd(),
    )
    
    # 4. Load previous session if --resume
    if config.resume_session:
        state = await load_session(config.resume_session)
    
    # 5. Tool registration
    registry = ToolRegistry()
    registry.register(BashTool())
    registry.register(FileReadTool())
    ...
    
    # 6. Context assembly
    context_asm = ContextAssembler(config)
    state.system_prompt = await context_asm.build_system_prompt()
    
    # 7. Session persistence setup
    persistence = SessionPersistence(config.sessions_dir)
    
    # 8. UI initialization
    ui = MiniClaudeUI(state)
    
    # 9. Enter main loop
    await ui.run()
```

---

## 8. Dependencies

### Core
- `anthropic` - Anthropic SDK (fallback)
- `litellm` - LLM proxy for multiple providers
- `rich` - Terminal UI framework
- `pydantic` - Data validation
- `typer` - CLI framework

### Advanced Features
- `diff-match-patch` - File edit operations
- `tiktoken` - Token counting (optional, for OpenAI models)
- `httpx` - Async HTTP client
- `tenacity` - Retry logic

### Utilities
- `pyyaml` - Config and skill file parsing
- `aiofiles` - Async file operations
- `pathspec` - .gitignore-style pattern matching

```toml
# pyproject.toml
[tool.poetry.dependencies]
python = "^3.10"
anthropic = "^0.30.0"
litellm = "^1.0.0"
rich = "^13.0.0"
pydantic = "^2.0.0"
typer = "^0.12.0"
diff-match-patch = "^20230430"
httpx = "^0.27.0"
tenacity = "^9.0.0"
pyyaml = "^6.0.0"
aiofiles = "^24.0.0"
pathspec = "^0.12.0"
```

---

## 9. Configuration Schema

```yaml
# mini_claude/config/settings.yaml

llm:
  default_model: "anthropic/claude-3-5-sonnet-20241022"
  api_key: "${ANTHROPIC_API_KEY}"  # Can reference env vars
  base_url: null  # For custom endpoints
  temperature: 0.7
  max_tokens: 4096

permissions:
  mode: "ask"  # ask | auto | restricted
  rules:
    - category: "destructive"
      mode: "ask"
    - category: "external"
      mode: "ask"
      # Can add command patterns for Bash
      patterns:
        - "rm -rf *"
        - "git push*"
    - category: "read"
      mode: "auto_approve"

ui:
  theme: "default"
  show_git_status: true
  stream_buffer_delay_ms: 50

context:
  claude_md_paths:
    - "CLAUDE.md"
    - ".claude/CLAUDE.md"
  max_context_tokens: 200000  # For Claude 3.5
  auto_compact_threshold: 0.9  # Compact at 90% of limit

agent:
  max_concurrent: 5
  default_model: "anthropic/claude-3-haiku-20240307"

persistence:
  auto_save: true
  sessions_dir: "config/sessions"
  max_sessions: 50
```

---

## 10. Testing Strategy

### Unit Tests
- Tool execution with mock LLM
- Context assembly
- Token estimation
- Permission rule matching

### Integration Tests
- Full query loop with fake LLM
- Parallel tool orchestration
- Context compression effectiveness
- Session save/load round-trip

### E2E Tests
- Real API calls with small prompts (optional, behind flag)
- File operation permissions
- Agent spawning and communication

---

## 11. Open Questions / TBD

| Item | Decision | Notes |
|------|----------|-------|
| Edit algorithm | `diff-match-patch` or hand-rolled | `src` uses custom algorithm |
| Token counting | tiktoken or LiteLLM's tokenizer | tiktoken only for OpenAI models |
| Web search implementation | DuckDuckGo, Google, or pluggable | Start with DuckDuckGo (no API key) |
| Skill storage format | YAML or Markdown | Match `src/skills/bundled/` structure |

---

## Appendix: Source Code References

Key files in `/src` to reference during implementation:

| Feature | Primary Reference | Secondary References |
|---------|-------------------|---------------------|
| Tool base | `src/Tool.ts` | `src/tools.ts` |
| Bash tool | `src/tools/BashTool/BashTool.ts` | |
| Agent tool | `src/tools/AgentTool/AgentTool.ts` | `src/tools/AgentTool/agentColorManager.ts` |
| Query loop | `src/query.ts` | `src/QueryEngine.ts` |
| AppState | `src/state/AppState.tsx` | `src/state/AppStateStore.ts` |
| Token management | `src/utils/tokens.ts` | `src/services/compact/` |
| Tool orchestration | `src/services/tools/toolOrchestration.ts` | `src/utils/toolGrouping.ts` |
| Context assembly | `src/context.ts` | |
| Permissions | `src/hooks/useCanUseTool.tsx` | `src/types/permissions.ts` |
| Task system | `src/Task.ts` | `src/tasks.ts` |

---

**Status:** Design Complete  
**Next Step:** Implementation Plan (Invoke `superpowers:writing-plans`)
