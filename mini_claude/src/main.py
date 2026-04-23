"""CLI entry point for mini_claude."""

import asyncio
import sys
import os
from typing import Optional

from .config import Config, load_config
from .models.state import AppState
from .services.llm import LLMClient
from .services.context import ContextAssembler
from .services.permissions import PermissionManager
from .services.compaction import ContextCompactor
from .services.hooks import HookRegistry
from .services.persistence import SessionPersistence
from .tools.registry import ToolRegistry
from .tools.bash import BashTool
from .tools.file_read import FileReadTool
from .tools.file_write import FileWriteTool
from .tools.file_edit import FileEditTool
from .tools.glob_tool import GlobTool
from .engine.query import QueryEngine
from .ui.app import App


def register_all_tools(registry: ToolRegistry, state: Optional[AppState] = None) -> None:
    """Register all available tools."""
    # Core tools
    registry.register(BashTool(state=state), aliases=["bash", "shell"])
    registry.register(FileReadTool(), aliases=["read", "cat"])
    registry.register(FileWriteTool(), aliases=["write"])
    registry.register(FileEditTool(), aliases=["edit"])
    registry.register(GlobTool(), aliases=["glob", "find"])

    # Advanced tools - lazy import to avoid errors if deps are missing
    try:
        from .tools.grep_tool import GrepTool
        registry.register(GrepTool(), aliases=["grep", "search"])
    except (ImportError, Exception):
        pass

    try:
        from .tools.ask_user import AskUserTool
        registry.register(AskUserTool())
    except (ImportError, Exception):
        pass

    try:
        from .tools.todo import TodoWriteTool
        registry.register(TodoWriteTool())
    except (ImportError, Exception):
        pass

    try:
        from .tools.task import TaskStopTool
        registry.register(TaskStopTool())
    except (ImportError, Exception):
        pass

    try:
        from .tools.web_fetch import WebFetchTool
        registry.register(WebFetchTool(), aliases=["fetch", "curl", "http"])
    except (ImportError, Exception):
        pass

    try:
        from .tools.web_search import WebSearchTool
        from .tools.knowledge import SourceRegistry
        from .tools.knowledge.sources import WebSource, NewsSource, TechDocsSource

        # 注册知识源
        source_registry = SourceRegistry()
        source_registry.register(WebSource())
        source_registry.register(NewsSource())
        source_registry.register(TechDocsSource())

        # 创建 WebSearchTool 并注入知识源注册表
        web_search = WebSearchTool()
        web_search._source_registry = source_registry
        registry.register(web_search)
    except (ImportError, Exception):
        pass

    try:
        from .tools.memory import MemoryWriteTool
        working_dir = state.working_dir if state else os.getcwd()
        registry.register(MemoryWriteTool(working_dir=working_dir))
    except (ImportError, Exception):
        pass

    try:
        from .tools.skill import SkillTool
        registry.register(SkillTool())
    except (ImportError, Exception):
        pass


def register_agent_tool(registry: ToolRegistry, llm: LLMClient, state: AppState) -> None:
    """Register and configure the Agent tool (needs LLM and state)."""
    try:
        from .tools.agent import AgentTool
        agent_tool = AgentTool()
        agent_tool.configure(llm=llm, tools=registry, state=state)
        registry.register(agent_tool, aliases=["agent", "subagent"])
    except (ImportError, Exception):
        pass

    try:
        from .tools.agent.send_message import SendMessageTool
        registry.register(SendMessageTool(state=state))
    except (ImportError, Exception):
        pass


def _first_run_setup() -> None:
    """Check if this is the first run and guide user through setup."""
    from .config.settings import CONFIG_HOME, create_default_config

    config_file = CONFIG_HOME / "settings.yaml"
    if config_file.exists():
        return

    print("Welcome to Mini Claude!")
    print(f"Creating config directory: {CONFIG_HOME}")
    path = create_default_config()
    print(f"Default config created: {path}")
    print(f"Edit {path} to set your API key and model.\n")


async def async_main(
    model: Optional[str] = None,
    permission_mode: str = "ask",
    resume: Optional[str] = None,
) -> None:
    """Async entry point."""
    # 0. First-run setup
    _first_run_setup()

    # 1. Load config
    config = load_config()
    if model:
        config.model = model

    # 2. Initialize state — working_dir is wherever user launched mc
    state = AppState(working_dir=os.getcwd())

    # 2b. Resume session if requested
    if resume is not None:
        persistence = SessionPersistence(sessions_dir=config.sessions_dir)
        if resume == "":
            # --resume with no ID: list recent sessions
            sessions = persistence.list_sessions(limit=5)
            if not sessions:
                print("No saved sessions found.")
                return
            print("Recent sessions:")
            for i, s in enumerate(sessions):
                sid = s['session_id'][:16]
                msgs = s.get('messages', 0)
                saved = s.get('saved_at', 'unknown')
                print(f"  [{i+1}] {sid}  {msgs} msgs  ({saved})")
            try:
                choice = input("\nEnter number to resume (or Enter to cancel): ").strip()
                if not choice:
                    return
                idx = int(choice) - 1
                if 0 <= idx < len(sessions):
                    resume = sessions[idx]['session_id']
                else:
                    print("Invalid choice.")
                    return
            except (ValueError, EOFError, KeyboardInterrupt):
                return

        loaded = persistence.load(resume)
        if loaded:
            state.messages = loaded.messages
            state.session_id = loaded.session_id
            state.total_tokens = loaded.total_tokens
            if loaded.working_dir:
                state.working_dir = loaded.working_dir
            print(f"Resumed session {loaded.session_id} ({len(loaded.messages)} messages)")
        else:
            print(f"Session not found: {resume}")
            return

    # 3. Register tools
    registry = ToolRegistry()
    register_all_tools(registry, state=state)

    # 4. Create LLM client
    try:
        llm = LLMClient(config)
    except (ImportError, ValueError) as e:
        print(f"Error initializing LLM client: {e}")
        print("Configure API key in ~/.config/mini_claude/settings.yaml")
        print("Or set env: OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL")
        sys.exit(1)

    # 5. Register Agent tool (needs LLM)
    register_agent_tool(registry, llm, state)

    # 5b. Start MCP servers and register their tools
    mcp_clients = []
    try:
        import yaml as _yaml
        from .config.settings import CONFIG_HOME as _cfg_home
        _cfg_file = _cfg_home / "settings.yaml"
        if _cfg_file.exists():
            with open(_cfg_file, 'r') as _f:
                _cfg_data = _yaml.safe_load(_f) or {}
            for srv_cfg in _cfg_data.get("mcp_servers", []):
                from .services.mcp import MCPClient
                client = MCPClient(
                    name=srv_cfg["name"],
                    command=srv_cfg["command"],
                    args=srv_cfg.get("args", []),
                    env=srv_cfg.get("env"),
                )
                try:
                    await client.start()
                    count = await registry.register_mcp_tools(client)
                    mcp_clients.append(client)
                    print(f"MCP server '{client.name}': {count} tools registered")
                except Exception as e:
                    print(f"MCP server '{srv_cfg['name']}' failed: {e}")
    except Exception:
        pass

    # 6. Assemble context — reads CLAUDE.md from working_dir
    context_asm = ContextAssembler(state.working_dir)
    state.system_prompt = await context_asm.build_system_prompt()

    # 7. Create services
    permissions = PermissionManager(mode=permission_mode)
    compactor = ContextCompactor()
    hooks = HookRegistry()
    persistence = SessionPersistence(sessions_dir=config.sessions_dir)

    # 7b. Load hooks from config (settings.yaml hooks section)
    try:
        import yaml
        from .config.settings import CONFIG_HOME
        config_file = CONFIG_HOME / "settings.yaml"
        if config_file.exists():
            with open(config_file, 'r') as f:
                cfg_data = yaml.safe_load(f) or {}
            hooks_config = cfg_data.get("hooks", [])
            if hooks_config:
                hooks.load_from_config(hooks_config)
    except Exception:
        pass

    # 8. Create engine and wire UI
    engine = QueryEngine(
        llm=llm,
        tools=registry,
        state=state,
        permissions=permissions,
        compactor=compactor,
        hooks=hooks,
    )
    app = App(engine=engine, state=state, persistence=persistence)
    engine._on_text = app.on_text
    engine._on_tool_start = app.on_tool_start
    engine._on_tool_end = app.on_tool_end
    engine._on_permission_ask = app.on_permission_ask
    engine._on_thinking = app.on_thinking
    engine._on_turn_end = app.on_turn_end

    # 9. Run
    await app.run()


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="mini_claude",
        description="Mini Claude - Terminal AI Coding Assistant",
    )
    parser.add_argument("--model", "-m", help="LLM model to use")
    parser.add_argument(
        "--permissions", "-p",
        choices=["ask", "auto", "restricted"],
        default="ask",
        help="Permission mode (default: ask)",
    )
    parser.add_argument(
        "--resume", "-r",
        nargs="?",
        const="",
        default=None,
        help="Resume a previous session (optionally specify session ID)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(async_main(
            model=args.model,
            permission_mode=args.permissions,
            resume=args.resume,
        ))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
