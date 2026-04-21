"""Rich UI app - Claude Code style terminal interface."""

import asyncio
import time
import threading
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.text import Text
    from rich.theme import Theme

    from rich.live import Live
    from rich.spinner import Spinner
    from rich.columns import Columns
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from ..models.state import AppState
from ..models.message import TextBlock, ToolUseBlock
from ..engine.query import QueryEngine
from ..services.persistence import SessionPersistence
from ..services.permissions import PermissionDecision
from ..commands import CommandRegistry, register_builtins

THEME = Theme({
    "user": "bold cyan",
    "assistant": "bold green",
    "tool": "bold yellow",
    "error": "bold red",
    "info": "dim",
    "permission": "bold magenta",
    "cost": "dim cyan",
    "status_line": "dim",
}) if HAS_RICH else None

# Spinner frames for thinking animation
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class SpinnerThread:
    """Background thread that shows a spinner while the model is thinking."""

    def __init__(self, console: Optional["Console"], label: str = "Thinking"):
        self.console = console
        self.label = label
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.console:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None
        # Clear the spinner line
        if self.console:
            self.console.file.write("\r\033[K")
            self.console.file.flush()

    def update_label(self, label: str) -> None:
        self.label = label

    def _run(self) -> None:
        idx = 0
        while not self._stop.is_set():
            frame = SPINNER_FRAMES[idx % len(SPINNER_FRAMES)]
            msg = f"\r  {frame} \033[1;35m{self.label}...\033[0m"
            try:
                self.console.file.write(msg)
                self.console.file.flush()
            except Exception:
                break
            idx += 1
            self._stop.wait(0.08)


# 缓存 skill 命令列表（启动时扫描一次）
_skill_commands: list[str] = []
# 模块级引用，由 App.__init__ 设置，供 completer 使用
_command_registry: Optional[CommandRegistry] = None


def _refresh_skill_commands() -> None:
    """扫描 .claude/skills/ 目录，刷新 skill 斜杠命令列表。"""
    global _skill_commands
    try:
        from ..tools.skill import SkillTool
        _skill_commands = [f"/{name}" for name, _ in SkillTool.list_skills()]
    except Exception:
        _skill_commands = []


def _build_slash_completer() -> WordCompleter:
    """Build a prompt_toolkit WordCompleter for slash commands."""
    builtin = [f"/{n}" for n in _command_registry.all_names()] if _command_registry else []
    all_commands = builtin + _skill_commands
    return WordCompleter(all_commands, sentence=True)


def _build_key_bindings() -> KeyBindings:
    """Build key bindings: backslash + Enter inserts a newline (multiline editing)."""
    kb = KeyBindings()

    @kb.add(Keys.Enter)
    def _(event):
        buf = event.current_buffer
        # Check if the current line (up to cursor) ends with backslash
        line_before_cursor = buf.document.current_line_before_cursor
        if line_before_cursor.endswith("\\"):
            buf.delete_before_cursor(1)  # remove the trailing backslash
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()

    return kb


class App:
    """Main terminal UI application with Claude Code style interface."""

    def __init__(
        self,
        engine: QueryEngine,
        state: AppState,
        persistence: Optional[SessionPersistence] = None,
    ):
        global _command_registry
        self.engine = engine
        self.state = state
        self.persistence = persistence
        self.console = Console(theme=THEME) if HAS_RICH else None
        self._stream_buffer = ""
        self._spinner: Optional[SpinnerThread] = None
        self._turn_start_time: float = 0
        self._turn_tokens_before: int = 0
        self._last_thinking: str = ""
        self._thinking_buffer: str = ""
        self._thinking_expanded: bool = False
        # Slash command context injection: commands set this to notify the model
        self._command_context: Optional[str] = None
        # Command registry
        self.commands = CommandRegistry()
        register_builtins(self.commands)
        _command_registry = self.commands
        _refresh_skill_commands()
        self._prompt_session = self._build_prompt_session()

    @staticmethod
    def _build_prompt_session() -> PromptSession:
        """构建 prompt_toolkit 会话：斜杠补全 + 反斜杠续行多行编辑。"""
        return PromptSession(
            completer=_build_slash_completer(),
            key_bindings=_build_key_bindings(),
            multiline=False,  # Enter submits by default; our keybinding overrides for backslash
        )

    async def run(self) -> None:
        """Main REPL loop."""
        self._print_header()

        while True:
            try:
                user_input = await self._get_input()
                if user_input is None:
                    break

                if user_input.startswith("/"):
                    if not await self._handle_command(user_input):
                        break
                    continue

                if not user_input.strip():
                    continue

                # Track timing and tokens for this turn
                self._stream_buffer = ""
                self._stream_marker_printed = False
                self._turn_start_time = time.time()
                self._turn_tokens_before = self.state.total_tokens

                response = await self.engine.run_turn(user_input)

                # Ensure newline after streamed output
                if self._stream_buffer:
                    if self.console:
                        self.console.print()  # newline
                    else:
                        print()
                elif response:
                    self._print_assistant(response)

            except KeyboardInterrupt:
                # Stop spinner if running
                if self._spinner:
                    self._spinner.stop()
                    self._spinner = None
                if self.state.is_streaming or self.state.current_tool_use:
                    self.state.abort_event.set()
                    self._print_info("\nAborting current operation...")
                    await asyncio.sleep(0.2)
                    self.state.abort_event.clear()
                else:
                    self._print_info("\nUse /exit to quit.")
            except EOFError:
                break

        # Auto-save session on exit
        if self.persistence and self.state.messages:
            path = self.persistence.save(self.state)
            self._print_info(f"Session saved: {path}")

        self._print_info("Goodbye!")

    async def _get_input(self) -> Optional[str]:
        """Claude Code 风格的输入提示：❯ 符号 + 颜色区分。
        支持反斜杠 \\ 续行：行尾输入 \\ 后按回车进入多行编辑模式，
        可自由用方向键在所有行之间移动和修改，最终无 \\ 时回车提交。
        """
        prompt_msg = HTML("<cyan><b>❯</b></cyan> ") if self.console else "> "
        try:
            raw = await asyncio.to_thread(
                self._prompt_session.prompt, prompt_msg
            )
            return raw
        except (EOFError, KeyboardInterrupt):
            return None

    async def _handle_command(self, command: str) -> bool:
        """Handle slash commands via registry. Returns False to exit."""
        parts = command.strip().split(maxsplit=1)
        name = parts[0].lower().lstrip("/")
        arg = parts[1] if len(parts) > 1 else ""

        # Reset command context before execution
        self._command_context = None

        # 1. Look up in command registry
        cmd = self.commands.get(name)
        if cmd:
            result = await cmd.handler(self, arg)
            # Inject context notification so the model knows what happened
            self._inject_command_context()
            return result

        # 2. Fallback: try as skill
        handled = await self._try_run_skill(name, arg)
        if not handled:
            self._print_info(f"Unknown command: {command}")
        return True

    def _inject_command_context(self) -> None:
        """Inject a lightweight user+assistant message pair into history.

        This lets the model know a slash command was executed without
        requiring an actual LLM call. The assistant reply is a brief
        acknowledgement so the model stays aware of state changes.
        """
        from ..models.message import Message
        ctx = self._command_context
        if not ctx:
            return
        self._command_context = None
        self.state.messages.append(Message(role="user", content=f"[system: {ctx}]"))
        self.state.messages.append(Message(role="assistant", content="Understood."))


    async def _try_run_skill(self, skill_name: str, args: str) -> bool:
        """尝试将命令作为 skill 执行。成功返回 True。"""
        from ..tools.skill import SkillTool

        tool = SkillTool()
        content = tool._find_skill(skill_name)
        if content is None:
            return False

        # 构造提示：skill 内容 + 用户参数，发送给引擎
        prompt = f"<skill>{content}</skill>"
        if args:
            prompt += f"\n\nUser arguments: {args}"

        self._print_info(f"Running skill: /{skill_name}")
        self._stream_buffer = ""
        self._turn_start_time = time.time()
        self._turn_tokens_before = self.state.total_tokens

        response = await self.engine.run_turn(prompt)

        if self._stream_buffer:
            if self.console:
                self.console.print()
            else:
                print()
        elif response:
            self._print_assistant(response)

        return True

    # ── Display helpers ──────────────────────────────────────────

    def _print_header(self) -> None:
        if self.console:
            self.console.print()
            self.console.print(
                Text.assemble(
                    ("  Mini Claude ", "bold white on blue"),
                    (" v0.1.0 ", "dim"),
                )
            )
            self.console.print(
                f"  Model: [bold]{self.engine.llm.config.model}[/bold]  "
                f"| /help for commands | /exit to quit",
                style="dim",
            )
            self.console.print()
        else:
            print("\nMini Claude v0.1.0")
            print(f"Model: {self.engine.llm.config.model}")
            print()

    def _print_assistant(self, text: str) -> None:
        if self.console:
            self.console.print()
            self.console.print("[bold green]⏺[/bold green] ", end="")
            try:
                self.console.print(Markdown(text))
            except Exception:
                self.console.print(text, style="assistant")
            self.console.print()
        else:
            print(text)
            print()

    def _print_info(self, text: str) -> None:
        if self.console:
            self.console.print(text, style="info")
        else:
            print(text)

    def _print_cost_line(self, tokens_used: int, elapsed: float, total_tokens: int) -> None:
        """Print token usage summary after a turn."""
        if self.console:
            parts = []
            if tokens_used > 0:
                parts.append(f"[bold]{tokens_used:,}[/bold] tokens")
            parts.append(f"{elapsed:.1f}s")
            parts.append(f"total: {total_tokens:,} tokens")
            self.console.print()  # 空行分隔回复内容和统计
            self.console.print(
                f"  [cost]{' | '.join(parts)}[/cost]"
            )
            self.console.print()
        else:
            print()
            print(f"  [{tokens_used} tokens | {elapsed:.1f}s | total: {total_tokens}]")
            print()

    # ── Callbacks wired to QueryEngine ───────────────────────────

    async def on_thinking(self, status: Optional[str]) -> None:
        """Show/hide thinking spinner."""
        if status is None:
            # Stop spinner
            if self._spinner:
                self._spinner.stop()
                self._spinner = None
        else:
            # Start or update spinner
            if self._spinner:
                self._spinner.update_label(status)
            else:
                self._spinner = SpinnerThread(self.console, label=status)
                self._spinner.start()

    async def on_thinking_content(self, text: str) -> None:
        """Stream thinking content to terminal (dim, collapsible)."""
        if self._spinner:
            self._spinner.stop()
            self._spinner = None
            if self.console:
                self.console.print()

        self._thinking_buffer += text
        if self.console:
            self.console.file.write(f"\033[2m{text}\033[0m")
            self.console.file.flush()
        else:
            print(text, end="", flush=True)

    def _finalize_thinking(self) -> None:
        """Called when thinking stream ends — save and show collapsed panel."""
        if self._thinking_buffer:
            self._last_thinking = self._thinking_buffer
            self._thinking_buffer = ""
            if self.console:
                # Clear the streamed dim text, show collapsed panel
                self.console.file.write("\r\033[K")
                self.console.file.flush()
                from .components import ThinkingPanel
                self.console.print(ThinkingPanel.render(self._last_thinking, collapsed=True))

    async def on_text(self, text: str) -> None:
        """Stream text token to terminal."""
        # Finalize any thinking block before text starts
        if self._thinking_buffer:
            self._finalize_thinking()
        # Stop spinner when first text arrives
        if self._spinner:
            self._spinner.stop()
            self._spinner = None
            # Print a newline to start fresh after spinner
            if self.console:
                self.console.print()

        # 首个 token 前打印 ⏺ 标记
        if not self._stream_marker_printed:
            self._stream_marker_printed = True
            if self.console:
                self.console.file.write("\n\033[1;32m⏺\033[0m ")
                self.console.file.flush()

        self._stream_buffer += text
        if self.console:
            self.console.file.write(text)
            self.console.file.flush()
        else:
            print(text, end="", flush=True)

    async def on_tool_start(self, tool_use: ToolUseBlock) -> None:
        """Show tool execution start."""
        # Stop any spinner
        if self._spinner:
            self._spinner.stop()
            self._spinner = None

        # Track tool name for on_tool_end display logic
        self._last_tool_name = tool_use.name

        if self.console:
            # Ensure we're on a new line
            self.console.print()

            # WebSearch: 延迟到 on_tool_end 打印标题行（需要知道搜索源）
            if tool_use.name != "WebSearch":
                # Show tool with icon
                tool_display = tool_use.name
                icon = _tool_icon(tool_use.name)
                self.console.print(f"  {icon} [tool]{tool_display}[/tool]", highlight=False)

            # Show key params
            params_summary = _summarize_params(tool_use.name, tool_use.input)
            if params_summary:
                self.console.print(f"    [dim]{params_summary}[/dim]")

            # Start a spinner for tool execution
            self._spinner = SpinnerThread(self.console, label=f"Running {tool_use.name}")
            self._spinner.start()
        else:
            print(f"\n> {tool_use.name}")

    async def on_tool_end(self, tool_use_id: str, result: dict) -> None:
        """Show tool execution result, with diff highlighting for edits."""
        # Stop tool spinner
        if self._spinner:
            self._spinner.stop()
            self._spinner = None

        content = result.get("content", "")
        is_error = result.get("is_error", False)
        tool_name = getattr(self, "_last_tool_name", "")

        if self.console:
            if is_error:
                self.console.print(f"    [error]{content}[/error]")
            elif tool_name == "WebSearch":
                # WebSearch: 先打印带搜索源的标题行，再显示结果
                source = self._extract_search_source(content)
                icon = _tool_icon("WebSearch")
                if source:
                    self.console.print(
                        f"  {icon} [tool]WebSearch[/tool] - Source: {source}",
                        highlight=False,
                    )
                else:
                    self.console.print(
                        f"  {icon} [tool]WebSearch[/tool]", highlight=False,
                    )
                self._print_search_results(content)
            elif "```diff\n" in content:
                # Split content into message + diff
                parts = content.split("```diff\n", 1)
                msg = parts[0].strip()
                diff_raw = parts[1].rstrip("`").rstrip("\n") if len(parts) > 1 else ""

                if msg:
                    self.console.print(f"    [dim]{msg}[/dim]")

                if diff_raw:
                    diff_lines = diff_raw.split("\n")
                    if len(diff_lines) > 50:
                        diff_raw = "\n".join(diff_lines[:30]) + f"\n... ({len(diff_lines)} lines total)"
                    from .components import DiffPanel
                    self.console.print(DiffPanel.render(diff_raw))
            else:
                # Truncate long output
                if len(content) > 1500:
                    content = content[:1500] + f"\n    ... ({len(result.get('content', ''))} chars total)"
                # Show abbreviated result
                lines = content.split("\n")
                if len(lines) > 8:
                    preview = "\n".join(lines[:6])
                    self.console.print(f"    [dim]{preview}[/dim]")
                    self.console.print(f"    [dim]... ({len(lines)} lines)[/dim]")
                else:
                    self.console.print(f"    [dim]{content}[/dim]")
        else:
            prefix = "ERROR: " if is_error else ""
            print(f"  {prefix}{content}")

    @staticmethod
    def _extract_search_source(content: str) -> str:
        """从 WebSearch 结果中提取搜索源标识。"""
        import re
        m = re.search(r"\[source:(.+?)]", content)
        return m.group(1) if m else ""

    def _print_search_results(self, content: str) -> None:
        """WebSearch 结果专用显示：提取标题+链接，过滤内部元数据。"""
        import re
        # 提取 [title](url) 格式的链接
        links = re.findall(r"\[(.+?)]\((https?://\S+?)\)", content)
        if links:
            for title, url in links[:5]:  # 最多显示 5 条
                self.console.print(f"    [dim]{title}[/dim]")
                self.console.print(f"    [dim underline]{url}[/dim underline]")
            if len(links) > 5:
                self.console.print(f"    [dim]... +{len(links) - 5} more results[/dim]")
        else:
            # fallback: 无链接时显示前几行
            lines = [l for l in content.split("\n") if l.strip() and not l.startswith("[")]
            for line in lines[:4]:
                self.console.print(f"    [dim]{line.strip()}[/dim]")

    async def on_turn_end(self, stats: dict) -> None:
        """Show token usage after a turn completes."""
        elapsed = time.time() - self._turn_start_time
        tokens_used = stats.get("total_tokens", 0) - self._turn_tokens_before
        total = stats.get("total_tokens", 0)
        self._print_cost_line(tokens_used, elapsed, total)

    async def on_permission_ask(self, tool_name: str, params: dict) -> bool:
        """Prompt user for tool permission."""
        # Stop spinner
        if self._spinner:
            self._spinner.stop()
            self._spinner = None

        summary = f"{tool_name}"
        if "command" in params:
            summary += f": {params['command'][:80]}"
        elif "file_path" in params:
            summary += f": {params['file_path']}"

        if self.console:
            self.console.print(
                f"\n  [permission]Allow {summary}?[/permission] "
                f"[dim]\\[y/N/a(lways)][/dim] ",
                end="",
            )
        else:
            print(f"\nAllow {summary}? [y/N/a(lways)] ", end="")

        try:
            answer = await asyncio.to_thread(input)
            answer = answer.strip().lower()
            if answer in ("a", "always"):
                self.engine.permissions.set_session_override(
                    tool_name, PermissionDecision.ALLOW
                )
                return True
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False


def _tool_icon(tool_name: str) -> str:
    """Return an icon for each tool type."""
    icons = {
        "Bash": "$ ",
        "FileRead": ">> ",
        "FileWrite": "<< ",
        "FileEdit": "~~ ",
        "Glob": "** ",
        "Grep": "// ",
        "Agent": ">> ",
        "TodoWrite": "[] ",
        "WebSearch": "@@ ",
        "AskUser": "?? ",
    }
    return icons.get(tool_name, "-- ")


def _summarize_params(tool_name: str, params: dict) -> str:
    """Create a one-line summary of tool parameters."""
    if tool_name == "Bash":
        cmd = params.get("command", "")
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        return cmd
    elif tool_name in ("FileRead", "FileWrite", "FileEdit"):
        return params.get("file_path", "")
    elif tool_name == "Glob":
        p = params.get("pattern", "")
        path = params.get("path", "")
        return f"{p}" + (f" in {path}" if path else "")
    elif tool_name == "Grep":
        return params.get("pattern", "")
    elif tool_name == "Agent":
        return params.get("description", "")
    return ""
