# Mini Claude Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Implement a Python clone of Claude Code with 11 tools, parallel orchestration, 5-layer context compression, and Rich terminal UI.

**Architecture:** Five-layer implementation (Foundation → Services → Tools → Engine → UI) with reactive state management and AsyncIO concurrency.

**Tech Stack:** Python 3.10+, Rich, Pydantic, LiteLLM, Typer, PyYAML

---

## MILESTONE 0: Project Skeleton ✓ (COMPLETED)

Project structure created and verified.

**Created:**
- [x] `pyproject.toml` - Package config with 12 dependencies
- [x] `README.md` - Project overview
- [x] `.gitignore` - Python + project ignores
- [x] Directory structure: `mini_claude/{models,utils,tools,config}/`
- [x] `models/message.py` - Message, TextBlock, ToolUseBlock, ToolResultBlock
- [x] `models/state.py` - AppState, TaskInfo, AgentInfo
- [x] `models/tool.py` - ToolResult, ToolChunk typedicts
- [x] `utils/ids.py` - ID generators
- [x] `tools/base.py` - BaseTool abstract class
- [x] `tools/registry.py` - ToolRegistry
- [x] `config/settings.py` - Config class, load_config()

**Verification:** `python3 -c "from mini_claude.models import Message; from mini_claude.tools import BaseTool"`

---

## MILESTONE 1: LLM Client (Layer 2 - Services)

Implements API client with retry, streaming, and token estimation.

### Task 1.1: LLM Client with LiteLLM

**Files:**
- Create: `mini_claude/services/llm.py`
- Test: `tests/unit/test_llm.py`

Dependencies: anthropic, litellm, tenacity

- [ ] **Step 1: Install dependencies**
```bash
cd mini_claude && pip install anthropic litellm tenacity
```

- [ ] **Step 2: Create LLMClient class**
```python
"""LLM client with retry and streaming."""
from tenacity import retry, stop_after_attempt, wait_exponential
import litellm

class LLMClient:
    def __init__(self, config: Config):
        self.model = config.model
        self.api_key = config.api_key
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def complete(self, messages: list, tools: list = None) -> Message:
        """Non-streaming completion."""
        pass
    
    async def complete_streaming(self, messages: list, tools: list = None):
        """Yields chunks for streaming display."""
        pass
```

- [ ] **Step 3: Test with mock API**
```python
async def test_complete_mock():
    from unittest.mock import patch
    # Test that complete() calls litellm.acompletion
```

- [ ] **Step 4: Commit**

### Task 1.2: Token Estimation

**Files:**
- Create: `mini_claude/services/tokens.py`
- Create: `mini_claude/utils/tokens.py` (optional local utils)

- [ ] **Step 1: Implement token counting**
```python
import tiktoken

def estimate_tokens(messages: list) -> int:
    """Estimate token count for Claude-compatible messages."""
    # Use Claude tokenizer approximation or tiktoken as fallback
    pass

def is_context_full(messages: list, max_tokens: int = 200000) -> bool:
    return estimate_tokens(messages) > max_tokens * 0.9
```

---

## MILESTONE 2: Basic Tools (Layer 3)

Implement 4 essential tools: Bash, FileRead, FileWrite, Glob.

### Task 2.1: BashTool (Streaming)

**Files:**
- Create: `mini_claude/tools/bash.py`

Reference: `src/tools/BashTool/BashTool.ts`

- [ ] **Step 1: Define tool schema**
```python
class BashTool(BaseTool):
    name = "Bash"
    description = "Execute shell commands"
    permission_category = PermissionCategory.EXTERNAL
    supports_streaming = True
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "timeout": {"type": "integer", "default": 120},
            "description": {"type": "string", "description": "What this command does"}
        },
        "required": ["command", "description"]
    }
```

- [ ] **Step 2: Implement execute_streaming with asyncio subprocess**
```python
async def execute_streaming(self, params, abort_event):
    proc = await asyncio.create_subprocess_shell(
        params["command"],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Stream stdout/stderr, check abort_event periodically
    # Yield ToolChunk every 100ms or on newline
```

- [ ] **Step 3: Add to registry and test**
```bash
python3 -c "from mini_claude.tools.bash import BashTool; print('Bash tool OK')"
```

### Task 2.2: File Tools (Read/Write)

**Files:**
- Create: `mini_claude/tools/file_read.py`
- Create: `mini_claude/tools/file_write.py`

- [ ] **Step 1: FileReadTool**
Permission: READ, no streaming needed
```python
async def execute(self, params, abort_event):
    path = Path(params["file_path"])
    content = await aiofiles.open(path).read()
    return {"content": content, "is_error": False}
```

- [ ] **Step 2: FileWriteTool**
Permission: WRITE
```python
input_schema = {
    "file_path": str, "content": str,
    "append": bool (default false)
}
```

### Task 2.3: GlobTool

**Files:**
- Create: `mini_claude/tools/glob_tool.py`

- [ ] **Step 1: Implement with pathspec for .gitignore support**
```python
from pathlib import Path
import fnmatch

class GlobTool(BaseTool):
    name = "Glob"
    input_schema = {"pattern": str, "path": str (default ".")}
    
    async def execute(self, params, abort_event):
        pattern = params["pattern"]
        base_path = Path(params.get("path", "."))
        # Use Path.glob or glob module, respect .gitignore
```

---

## MILESTONE 3: Query Engine (Layer 4)

Main conversation loop with tool execution.

### Task 3.1: Query Loop

**Files:**
- Create: `mini_claude/engine/query.py`
- Modify: `mini_claude/services/llm.py` (add tool support)

Reference: `src/query.ts`

- [ ] **Step 1: Create QueryEngine class**
```python
class QueryEngine:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, state: AppState):
        self.llm = llm
        self.tools = tools
        self.state = state
    
    async def run_turn(self, user_input: str) -> None:
        # 1. Add user message to state
        # 2. Build messages for API (system + history)
        # 3. Check context compression
        # 4. Call LLM with tools
        # 5. Route tool_uses to tools
        # 6. Add results to messages
        # 7. Loop if more tool_uses
```

- [ ] **Step 2: Tool execution routing**
```python
async def execute_tools(self, tool_uses: list) -> list:
    results = []
    for tool_use in tool_uses:
        tool = self.tools.get(tool_use.name)
        if tool:
            result = await tool.execute(tool_use.input, self.state.abort_event)
            results.append({
                "tool_use_id": tool_use.id,
                **result
            })
    return results
```

- [ ] **Step 3: Test single turn**
Create test that mocks LLM to return a tool_use and verify routing

### Task 3.2: FileEditTool with Diff

**Files:**
- Create: `mini_claude/tools/file_edit.py`
- Install: diff-match-patch

- [ ] **Step 1: Implement edit with diff_match_patch**
```python
from diff_match_patch import diff_match_patch

class FileEditTool(BaseTool):
    name = "FileEdit"
    permission_category = PermissionCategory.DESTRUCTIVE
    
    async def execute(self, params, abort_event):
        old_string = params["old_string"]
        new_string = params["new_string"]
        # Use diff_match_patch for smart matching
```

---

## MILESTONE 4: Context & Permissions (Layer 2)

Git integration, CLAUDE.md loading, permission checks.

### Task 4.1: Git & Context Assembly

**Files:**
- Create: `mini_claude/utils/git.py`
- Create: `mini_claude/services/context.py`

- [ ] **Step 1: Git operations**
```python
async def get_git_status() -> Optional[str]:
    """Get git status for system context."""
    # Use subprocess to get: branch, status, recent commits
```

- [ ] **Step 2: CLAUDE.md loader**
```python
async def load_claude_md(working_dir: Path) -> Optional[str]:
    for filename in ["CLAUDE.md", ".claude/CLAUDE.md"]:
        path = working_dir / filename
        if path.exists():
            return await aiofiles.open(path).read()
```

### Task 4.2: Permission System

**Files:**
- Create: `mini_claude/services/permissions.py`

- [ ] **Step 1: PermissionManager**
```python
class PermissionManager:
    def __init__(self, config: Config):
        self.mode = config.permission_mode
        self.rules = config.permission_rules
    
    def check(self, tool: BaseTool, params: dict) -> PermissionResult:
        # Check if tool needs approval based on category
        # Check path patterns if file operation
        # Return: ALLOW, ASK, DENY
```

- [ ] **Step 2: Interactive approval in UI**
Implement prompt for user Y/N when permission check returns ASK

---

## MILESTONE 5: Basic UI (Layer 5)

Rich terminal interface with live streaming.

### Task 5.1: Rich UI Components

**Files:**
- Create: `mini_claude/ui/components.py`
- Create: `mini_claude/ui/app.py`

- [ ] **Step 1: Install Rich**
```bash
pip install rich
```

- [ ] **Step 2: Basic chat display with Live**
```python
from rich.live import Live
from rich.console import Console
from rich.panel import Panel

class MiniClaudeUI:
    def __init__(self, state: AppState):
        self.state = state
        self.console = Console()
    
    async def run(self):
        with Live(self.render(), refresh_per_second=4) as live:
            while True:
                live.update(self.render())
                await asyncio.sleep(0.25)
    
    def render(self) -> Panel:
        # Render current messages
        # Show streaming buffer
        # Show active tools/tasks
```

- [ ] **Step 3: Message rendering"
```python
def render_message(self, msg: Message) -> RenderableType:
    # Format based on role (user/assistant/system)
    # Handle tool_use blocks specially
    # Use syntax highlighting for code
```

### Task 5.2: Input Handler

**Files:**
- Modify: `mini_claude/ui/app.py`

- [ ] **Step 1: Async input handling"
```python
async def get_user_input(self) -> str:
    # Use asyncio.to_thread for input() since it's blocking
    return await aioconsole.ainput("> ")
```

- [ ] **Step 2: Command parsing (/exit, /model, etc.)"
Parse slash commands before sending to LLM

---

## MILESTONE 6: Core Integration

Wire everything together in main.py.

### Task 6.1: main.py Entry Point

**Files:**
- Create: `mini_claude/main.py`

- [ ] **Step 1: CLI with Typer**
```python
import typer
app = typer.Typer()

@app.command()
def main(
    model: str = None,
    resume: bool = False,
    config: Path = None,
):
    # Load config
    # Register tools
    # Initialize state
    # Start UI loop
```

- [ ] **Step 2: Integration test"
Test: mini_claude --help returns usage

---

## MILESTONE 7: Advanced Tools

Agent, Todo, Task, AskUser, Grep, WebSearch, Skill.

### Task 7.1: GrepTool

**Files:**
- Create: `mini_claude/tools/grep_tool.py`

```python
class GrepTool(BaseTool):
    name = "Grep"
    # Use ripgrep if available, fallback to pathlib + re
```

### Task 7.2: TodoWriteTool

**Files:**
- Create: `mini_claude/tools/todo.py`
- Create: `mini_claude/services/todos.py` (state management)

```python
class TodoWriteTool(BaseTool):
    name = "TodoWrite"
    # Write to .claude/todos.json
```

### Task 7.3: AskUserQuestionTool

**Files:**
- Create: `mini_claude/tools/ask_user.py`

```python
class AskUserQuestionTool(BaseTool):
    name = "AskUserQuestion"
    # Interrupt flow to ask, return answer
```

### Task 7.4: TaskStopTool

**Files:**
- Create: `mini_claude/tools/task.py`

```python
class TaskStopTool(BaseTool):
    name = "TaskStop"
    # Stop background task by ID
```

### Task 7.5: AgentTool with Color Manager

**Files:**
- Create: `mini_claude/tools/agent/color.py`
- Create: `mini_claude/tools/agent/runner.py`
- Create: `mini_claude/tools/agent/tool.py`

Reference: `src/tools/AgentTool/`

```python
AGENT_COLORS = ["blue", "green", "yellow", "purple", "cyan"]

class AgentColorManager:
    _assigned: Dict[str, str] = {}
    
    @classmethod
    def assign(cls, agent_id: str) -> str:
        # Round-robin from AGENT_COLORS

class AgentRunner:
    async def spawn(self, name: str, prompt: str, parent_id: str):
        # Create agent, start background task
        # QueryEngine loop for agent

class AgentTool(BaseTool):
    name = "Agent"
    # Spawn agents, handle status queries
```

### Task 7.6: SkillTool

**Files:**
- Create: `mini_claude/tools/skill.py`
- Create: `mini_claude/skills/` directory

```python
class SkillTool(BaseTool):
    name = "Skill"
    # Load .claude/skills/<name>/SKILL.md
    # Inject skill instructions into context
```

### Task 7.7: WebSearchTool

**Files:**
- Create: `mini_claude/tools/web_search.py`

```python
class WebSearchTool(BaseTool):
    name = "WebSearch"
    # Use DuckDuckGo API or similar
    # Return search results as text
```

---

## MILESTONE 8: Advanced Services

Parallel orchestration, context compression, persistence.

### Task 8.1: ToolOrchestrator (Parallel Execution)

**Files:**
- Create: `mini_claude/tools/orchestration.py`

Reference: `src/services/tools/toolOrchestration.ts`

```python
class ToolOrchestrator:
    def analyze_dependencies(self, tool_uses: list) -> ExecutionGraph:
        # Group independent tools for parallel execution
    
    async def execute_parallel(self, tool_uses: list) -> list:
        # Gather independent tools
        # Execute with asyncio.gather
        # Wait for dependencies before dependent tools
```

### Task 8.2: ContextCompactor (5-Layer)

**Files:**
- Create: `mini_claude/services/compaction.py`

Reference: `src/services/compact/`, `src/utils/tokens.ts`

```python
COMPACTION_STRATEGIES = [
    "remove_tool_details",
    "remove_assistant_thinking",
    "summarize_messages",
    "remove_early_tool_calls",
    "hard_truncation",
]

class ContextCompactor:
    async def compact(self, messages: list, target: int) -> list:
        for strategy in COMPACTION_STRATEGIES:
            if estimate_tokens(messages) <= target:
                break
            messages = await self._apply(messages, strategy)
        return messages
```

### Task 8.3: Session Persistence

**Files:**
- Create: `mini_claude/services/persistence.py`

```python
class SessionPersistence:
    def save(self, state: AppState) -> str:
        # Serialize to JSON, save to config/sessions/
    
    def load(self, session_id: str) -> AppState:
        # Deserialize from file
    
    def list_sessions(self) -> list:
        # Return recent sessions
```

---

## MILESTONE 9: Polish & Testing

Permissions integration, error handling, comprehensive tests.

### Task 9.1: Permission UI Integration

**Files:**
- Modify: `mini_claude/ui/app.py`
- Modify: `mini_claude/engine/query.py`

- [ ] Before executing tool, check PermissionManager
- [ ] If ASK, prompt user with formatted tool preview
- [ ] Handle user response (yes/no/always/never)

### Task 9.2: Error Handling & Edge Cases

**Files:**
- Various

- [ ] Handle LLM API errors (rate limits, timeouts)
- [ ] Handle file not found, permission denied
- [ ] Handle tool execution failures gracefully
- [ ] Ctrl+C handling for graceful abort

### Task 9.3: Comprehensive Tests

**Files:**
- tests/integration/test_query_flow.py
- tests/integration/test_tools.py

- [ ] Full query loop with mocked LLM
- [ ] Tool execution end-to-end
- [ ] Context compaction effectiveness
- [ ] Session save/load roundtrip

---

## MILESTONE 10: Final Integration

Complete working system.

### Task 10.1: Register All Tools

**Files:**
- Modify: `mini_claude/main.py`

```python
def register_all_tools(registry: ToolRegistry):
    from mini_claude.tools.bash import BashTool
    from mini_claude.tools.file_read import FileReadTool
    # ... all 11 tools
    
    registry.register(BashTool())
    registry.register(FileReadTool())
    # ...
```

### Task 10.2: Full System Test

**Command:**
```bash
python -m mini_claude --help
python -m mini_claude --model anthropic/claude-3-haiku
# Test interactive session
```

### Task 10.3: Documentation

**Files:**
- Update: `README.md`
- Create: `mini_claude/config/claude.md` (self-documenting)

---

## File Inventory

Total new files to create:

**Models (4):** Already done
**Utils (1):** Already done
**Config (2):** Already done
**Tools (13):** bash, file_read, file_write, file_edit, glob, grep, ask_user, todo, task, web_search, skill, agent/* (3 files)
**Tools infra (1):** orchestration
**Services (6):** llm, tokens, context, permissions, compaction, persistence
**Engine (1):** query
**UI (3):** app, components, state_sync
**Main (1):** main.py
**Tests (5+):** 5+ test files

**Total: ~40 files**

---

## Spec Coverage Check

| Spec Section | Implementation Tasks |
|--------------|---------------------|
| 5-layer architecture | Main structure complete, orchestration optional + advanced |
| 11 Tools | Tasks 2.1-2.3, 7.1-7.7 |
| Parallel orchestration | Task 8.1 (MILESTONE 8) |
| 5-layer compression | Task 8.2 (MILESTONE 8) |
| Permission system | Tasks 4.2, 9.1 |
| Agent subsystem | Task 7.5 |
| Rich UI | Task 5.1-5.2 |
| Session persistence | Task 8.3 |

---

## Dependencies by Milestone

- **M1:** `anthropic litellm tenacity`
- **M2:** `aiofiles` (file I/O)
- **M3:** `diff-match-patch`
- **M4:** Already have
- **M5:** `rich aioconsole` (input handling)
- **M7:** `duckduckgo-search` (web search)
- **M8:** `tiktoken` (optional, for token counting)

---

## Open Questions (from Spec)

| Question | Current Decision | Status |
|----------|-----------------|---------|
| Edit algorithm | diff_match_patch | Decided |
| Token counting | tiktoken approximation | Decided |
| Web search | DuckDuckGo API | Decided |

---

**Plan Status:** COMPLETE

**Next:** Choose execution approach:
1. Subagent-Driven: Fresh subagent per milestone, parallel tasks
2. Inline Execution: Sequential tasks in this conversation

**Or:** Start immediately with MILESTONE 1 (LLM Client)
