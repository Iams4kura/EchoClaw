"""Reusable UI components for Rich terminal display."""

from typing import Optional

try:
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
    from rich.syntax import Syntax
    from rich.markdown import Markdown
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


class StatusBar:
    """Bottom status bar showing session info."""

    def __init__(self, model: str = "", tokens: int = 0, branch: Optional[str] = None):
        self.model = model
        self.tokens = tokens
        self.branch = branch

    def render(self):
        if not HAS_RICH:
            return f"[{self.model}] tokens:{self.tokens}"
        parts = []
        if self.model:
            parts.append(f"[bold]{self.model}[/bold]")
        if self.branch:
            parts.append(f"[cyan]{self.branch}[/cyan]")
        parts.append(f"tokens: {self.tokens:,}")
        return Text.from_markup(" | ".join(parts))


class MessagePanel:
    """Renders a conversation message."""

    @staticmethod
    def render(role: str, content: str, tool_name: Optional[str] = None):
        if not HAS_RICH:
            prefix = {"user": "You", "assistant": "Assistant"}.get(role, role)
            return f"{prefix}: {content}"
        style_map = {"user": "cyan", "assistant": "green", "system": "yellow"}
        border = style_map.get(role, "white")
        title = tool_name or role.capitalize()
        body = Markdown(content) if role == "assistant" else content
        return Panel(body, title=title, border_style=border, padding=(0, 1))


class ToolOutput:
    """Renders tool execution output."""

    @staticmethod
    def render(tool_name: str, content: str, is_error: bool = False):
        if not HAS_RICH:
            return f"[{tool_name}] {'ERROR' if is_error else 'OK'}: {content}"
        style = "red" if is_error else "dim"
        title = f"{'ERROR: ' if is_error else ''}{tool_name}"
        return Panel(content, title=title, border_style=style, padding=(0, 1))


class ThinkingPanel:
    """Renders extended thinking content with collapsible display."""

    @staticmethod
    def render(text: str, collapsed: bool = True):
        if not HAS_RICH:
            if collapsed:
                preview = text[:100] + "..." if len(text) > 100 else text
                return f"[Thinking] {preview}"
            return f"[Thinking]\n{text}"
        if collapsed:
            preview = text[:100] + "..." if len(text) > 100 else text
            return Panel(
                Text(preview, style="dim"),
                title="Thinking",
                border_style="dim",
                subtitle="[dim]/thinking to expand[/dim]",
            )
        return Panel(
            Text(text, style="dim"),
            title="Thinking",
            border_style="dim",
        )


class DiffPanel:
    """Renders a unified diff with syntax highlighting."""

    @staticmethod
    def render(diff_text: str, file_path: str = ""):
        if not HAS_RICH:
            return diff_text
        title = f"Diff: {file_path}" if file_path else "Diff"
        return Syntax(diff_text, "diff", theme="monokai", line_numbers=False)


class AgentPanel:
    """Renders active agents sidebar."""

    @staticmethod
    def render(agents: dict):
        if not HAS_RICH:
            return "\n".join(f"  [{a.color}] {a.name} ({a.status})" for a in agents.values()) or "(no agents)"
        table = Table(title="Agents", show_header=False, border_style="blue")
        table.add_column("Agent", style="bold")
        table.add_column("Status")
        for info in agents.values():
            table.add_row(
                Text(info.name, style=f"bold {info.color}"),
                Text(info.status, style="green" if info.status == "running" else "dim"),
            )
        return table if agents else Text("(no agents)", style="dim")
