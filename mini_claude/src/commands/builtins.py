"""Built-in slash commands extracted from App._handle_command.

Each handler has the signature:
    async def handler(app: App, args: str) -> bool
        Returns False to signal "exit the REPL", True otherwise.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .registry import Command, CommandRegistry

if TYPE_CHECKING:
    from ..ui.app import App


# ── Handlers ────────────────────────────────────────────────


async def cmd_exit(app: "App", args: str) -> bool:
    return False


async def cmd_help(app: "App", args: str) -> bool:
    lines = []
    for cmd in app.commands.list_all():
        desc = f" - {cmd.description}" if cmd.description else ""
        aliases = ""
        if cmd.aliases:
            aliases = f" ({', '.join('/' + a for a in cmd.aliases)})"
        lines.append(f"  /{cmd.name:<12}{desc}{aliases}")

    # Append available skills
    try:
        from ..tools.skill import SkillTool
        skills = SkillTool.list_skills()
        if skills:
            lines.append("")
            lines.append("Skills:")
            for name, desc in skills:
                desc_text = f" - {desc}" if desc else ""
                lines.append(f"  /{name:<12}{desc_text}")
    except Exception:
        pass

    help_text = "\n".join(lines)
    if app.console:
        from rich.panel import Panel
        app.console.print(Panel(help_text, title="Commands", border_style="blue"))
    else:
        print(help_text)
    return True


async def cmd_status(app: "App", args: str) -> bool:
    status = (
        f"Session: {app.state.session_id}\n"
        f"Messages: {len(app.state.messages)}\n"
        f"Tokens: ~{app.state.total_tokens:,}\n"
        f"Tasks: {app.state.get_active_task_count()}\n"
        f"Agents: {app.state.get_active_agent_count()}\n"
        f"Permissions: {app.engine.permissions.mode}"
    )
    if app.console:
        from rich.panel import Panel
        app.console.print(Panel(status, title="Status", border_style="green"))
    else:
        print(status)
    return True


async def cmd_clear(app: "App", args: str) -> bool:
    from ..models.state import TokenUsage
    app.state.messages.clear()
    app.state.token_usage = TokenUsage()
    app._print_info("Conversation cleared.")
    app._command_context = "User ran /clear. All previous conversation history has been cleared."
    return True


async def cmd_model(app: "App", args: str) -> bool:
    if args:
        old_model = app.engine.llm.config.model
        app.engine.llm.config.model = args
        app._print_info(f"Model set to: {args}")
        app._command_context = f"User ran /model. Model switched from {old_model} to {args}."
    else:
        app._print_info(f"Current model: {app.engine.llm.config.model}")
    return True


async def cmd_save(app: "App", args: str) -> bool:
    if app.persistence:
        path = app.persistence.save(app.state)
        app._print_info(f"Session saved: {path}")
    else:
        app._print_info("Persistence not configured.")
    return True


async def cmd_load(app: "App", args: str) -> bool:
    if not app.persistence:
        app._print_info("Persistence not configured.")
    elif args:
        loaded = app.persistence.load(args)
        if loaded:
            app.state.messages = loaded.messages
            app.state.session_id = loaded.session_id
            app._print_info(f"Loaded session {loaded.session_id} ({len(loaded.messages)} messages)")
            app._command_context = (
                f"User ran /load. Loaded session {loaded.session_id} "
                f"with {len(loaded.messages)} messages from a previous conversation."
            )
        else:
            app._print_info(f"Session not found: {args}")
    else:
        sessions = app.persistence.list_sessions(limit=10)
        if sessions:
            for s in sessions:
                app._print_info(
                    f"  {s['session_id'][:12]}  {s['messages']} msgs  {s.get('saved_at', '')}"
                )
        else:
            app._print_info("No saved sessions.")
    return True


async def cmd_compact(app: "App", args: str) -> bool:
    before = len(app.state.messages)
    app.state.messages = await app.engine.compactor.compact(app.state.messages)
    after = len(app.state.messages)
    app._print_info(f"Compacted: {before} -> {after} messages")
    app._command_context = f"User ran /compact. Context compacted from {before} to {after} messages."
    return True


async def cmd_permissions(app: "App", args: str) -> bool:
    if args and args in ("ask", "auto", "restricted", "default", "plan", "bypass"):
        app.engine.permissions.mode = args
        app._print_info(f"Permission mode: {args}")
        app._command_context = f"User ran /permissions. Permission mode set to '{args}'."
    else:
        app._print_info(f"Permission mode: {app.engine.permissions.mode}")
    return True


async def cmd_cost(app: "App", args: str) -> bool:
    """Show token usage and estimated cost for this session."""
    from ..services.pricing import format_cost_report
    model = app.engine.llm.config.model
    usage = app.state.token_usage
    msgs = len(app.state.messages)
    report = f"Messages: {msgs}\n{format_cost_report(model, usage)}"
    if app.console:
        from rich.panel import Panel
        app.console.print(Panel(report, title="Cost", border_style="cyan"))
    else:
        print(report)
    return True


async def cmd_memory(app: "App", args: str) -> bool:
    """View, edit, or clear project memory."""
    from ..tools.memory import MemoryWriteTool

    if args.strip() == "edit":
        memory_path = MemoryWriteTool(working_dir=app.state.working_dir)._memory_path
        editor = os.environ.get("EDITOR", "vi")
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        if not memory_path.exists():
            memory_path.write_text("", encoding="utf-8")
        import subprocess
        subprocess.run([editor, str(memory_path)])
        # Invalidate context cache so next prompt picks up changes
        if hasattr(app.engine, '_context_asm'):
            app.engine._context_asm.invalidate_cache()
        app._print_info("Memory file edited.")
        return True

    if args.strip() == "clear":
        tool = MemoryWriteTool(working_dir=app.state.working_dir)
        if tool._memory_path.exists():
            tool._memory_path.write_text("", encoding="utf-8")
        app._print_info("Memory cleared.")
        return True

    # Default: show all entries
    entries = MemoryWriteTool.list_entries(app.state.working_dir)
    if not entries:
        app._print_info("No memory entries. Use MemoryWrite tool or /memory edit.")
        return True
    lines = []
    for key, content in entries.items():
        preview = content[:80].replace("\n", " ")
        if len(content) > 80:
            preview += "..."
        lines.append(f"  [{key}] {preview}")
    if app.console:
        from rich.panel import Panel
        app.console.print(Panel("\n".join(lines), title="Memory", border_style="green"))
    else:
        print("\n".join(lines))
    return True


async def cmd_thinking(app: "App", args: str) -> bool:
    """Toggle display of last thinking block."""
    if not app._last_thinking:
        app._print_info("No thinking content from last response.")
        return True
    app._thinking_expanded = not app._thinking_expanded
    if app.console:
        from ..ui.components import ThinkingPanel
        app.console.print(ThinkingPanel.render(
            app._last_thinking, collapsed=not app._thinking_expanded,
        ))
    else:
        if app._thinking_expanded:
            print(f"[Thinking]\n{app._last_thinking}")
        else:
            preview = app._last_thinking[:100] + "..."
            print(f"[Thinking] {preview}")
    return True


async def cmd_mcp(app: "App", args: str) -> bool:
    """List MCP servers or restart one."""
    # Access mcp_clients from engine (stored as attribute)
    clients = getattr(app.engine, '_mcp_clients', [])
    if not clients:
        app._print_info("No MCP servers configured. Add mcp_servers in settings.yaml.")
        return True

    if args.startswith("restart"):
        name = args.replace("restart", "").strip()
        for c in clients:
            if c.name == name:
                await c.restart()
                count = await app.engine.tools.register_mcp_tools(c)
                app._print_info(f"Restarted '{name}': {count} tools")
                return True
        app._print_info(f"MCP server not found: {name}")
        return True

    lines = []
    for c in clients:
        status = "connected" if c.is_connected else "disconnected"
        try:
            tools = await c.list_tools()
            tool_count = len(tools)
        except Exception:
            tool_count = "?"
        lines.append(f"  {c.name:<16} {status:<14} {tool_count} tools")
    info = "\n".join(lines)
    if app.console:
        from rich.panel import Panel
        app.console.print(Panel(info, title="MCP Servers", border_style="magenta"))
    else:
        print(info)
    return True


async def cmd_hooks(app: "App", args: str) -> bool:
    """List registered hooks."""
    hooks = app.engine.hooks.get_hooks()
    if not hooks:
        app._print_info("No hooks registered. Add hooks in settings.yaml.")
        return True
    lines = []
    for h in hooks:
        lines.append(f"  {h.event.value:<16} filter={h.tool_filter:<8} timeout={h.timeout}s  {h.command}")
    info = f"Hooks ({len(hooks)} registered, {app.engine.hooks.fire_count} fired):\n" + "\n".join(lines)
    if app.console:
        from rich.panel import Panel
        app.console.print(Panel(info, title="Hooks", border_style="yellow"))
    else:
        print(info)
    return True


async def cmd_context(app: "App", args: str) -> bool:
    """Show system prompt summary."""
    sp = app.state.system_prompt or "(no system prompt)"
    lines = sp.split("\n")
    sections = [l for l in lines if l.startswith("# ")]
    info = (
        f"System prompt length: {len(sp)} chars\n"
        f"Sections: {', '.join(s.lstrip('# ') for s in sections) or '(none)'}\n"
        f"Working dir: {app.state.working_dir}"
    )
    if app.console:
        from rich.panel import Panel
        app.console.print(Panel(info, title="Context", border_style="blue"))
    else:
        print(info)
    return True


# ── Registration ────────────────────────────────────────────


def register_builtins(registry: CommandRegistry) -> None:
    """Register all built-in slash commands."""
    registry.register(Command(
        name="help", handler=cmd_help,
        description="Show this help",
    ))
    registry.register(Command(
        name="exit", handler=cmd_exit,
        description="Exit",
        aliases=["quit", "q"],
    ))
    registry.register(Command(
        name="clear", handler=cmd_clear,
        description="Clear conversation",
    ))
    registry.register(Command(
        name="status", handler=cmd_status,
        description="Session status",
    ))
    registry.register(Command(
        name="model", handler=cmd_model,
        description="Switch model",
    ))
    registry.register(Command(
        name="save", handler=cmd_save,
        description="Save session",
    ))
    registry.register(Command(
        name="load", handler=cmd_load,
        description="Load session / list sessions",
    ))
    registry.register(Command(
        name="compact", handler=cmd_compact,
        description="Force context compaction",
    ))
    registry.register(Command(
        name="permissions", handler=cmd_permissions,
        description="View/set permission mode",
    ))
    registry.register(Command(
        name="cost", handler=cmd_cost,
        description="Show token usage and cost",
    ))
    registry.register(Command(
        name="context", handler=cmd_context,
        description="Show system prompt summary",
    ))
    registry.register(Command(
        name="memory", handler=cmd_memory,
        description="View/edit project memory",
    ))
    registry.register(Command(
        name="thinking", handler=cmd_thinking,
        description="Toggle last thinking block",
    ))
    registry.register(Command(
        name="hooks", handler=cmd_hooks,
        description="List registered hooks",
    ))
    registry.register(Command(
        name="mcp", handler=cmd_mcp,
        description="List/restart MCP servers",
    ))
