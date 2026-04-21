"""Hook system for event-driven extensibility.

Reference: src/hooks/toolPermission/ (permission checks on tool invocations)
"""

import asyncio
import fnmatch
import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class HookEvent(Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"


@dataclass
class Hook:
    """A registered hook."""
    event: HookEvent
    command: str              # Shell command template with env var substitution
    tool_filter: str = "*"   # fnmatch pattern for tool name filtering
    timeout: int = 10        # Seconds before killing the hook process
    source: str = "config"   # Where this hook was defined


@dataclass
class HookResult:
    """Result of executing a hook."""
    hook: Hook
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False

    @property
    def blocked(self) -> bool:
        """Whether this hook result should block tool execution."""
        return self.exit_code != 0 and self.hook.event == HookEvent.PRE_TOOL_USE


class HookRegistry:
    """Manages and executes lifecycle hooks."""

    def __init__(self) -> None:
        self._hooks: Dict[HookEvent, List[Hook]] = {e: [] for e in HookEvent}
        self._fire_count: int = 0

    def register(self, hook: Hook) -> None:
        self._hooks[hook.event].append(hook)

    def get_hooks(self, event: Optional[HookEvent] = None) -> List[Hook]:
        if event:
            return list(self._hooks[event])
        return [h for hooks in self._hooks.values() for h in hooks]

    def load_from_config(self, hooks_config: list) -> None:
        """Load hooks from settings.yaml config list."""
        for cfg in hooks_config:
            try:
                event = HookEvent(cfg["event"])
                hook = Hook(
                    event=event,
                    command=cfg["command"],
                    tool_filter=cfg.get("tool_filter", "*"),
                    timeout=cfg.get("timeout", 10),
                    source=cfg.get("source", "config"),
                )
                self.register(hook)
            except (KeyError, ValueError) as e:
                logger.warning("Invalid hook config: %s — %s", cfg, e)

    async def fire(
        self,
        event: HookEvent,
        context: Optional[dict] = None,
    ) -> List[HookResult]:
        """Fire all hooks for the given event.

        For PRE_TOOL_USE, if any hook returns non-zero exit code,
        it signals that the tool execution should be blocked.

        Context keys are exposed as environment variables with MC_ prefix:
            tool_name -> MC_TOOL_NAME
            file_path -> MC_FILE_PATH
            etc.
        """
        hooks = self._hooks.get(event, [])
        if not hooks:
            return []

        ctx = context or {}
        tool_name = ctx.get("tool_name", "")

        # Filter hooks by tool_filter
        matching = [
            h for h in hooks
            if fnmatch.fnmatch(tool_name, h.tool_filter) or h.tool_filter == "*"
        ]
        if not matching:
            return []

        # Build environment variables
        env = os.environ.copy()
        for key, value in ctx.items():
            env_key = f"MC_{key.upper()}"
            if isinstance(value, (dict, list)):
                env[env_key] = json.dumps(value, ensure_ascii=False, default=str)
            else:
                env[env_key] = str(value)
        # Also set legacy names without prefix for convenience
        if tool_name:
            env["TOOL_NAME"] = tool_name
        if "tool_input" in ctx:
            env["TOOL_INPUT"] = json.dumps(ctx["tool_input"], ensure_ascii=False, default=str)
        if "file_path" in ctx:
            env["FILE_PATH"] = str(ctx["file_path"])

        results = []
        for hook in matching:
            result = await self._execute_hook(hook, env)
            results.append(result)
            self._fire_count += 1

        return results

    async def _execute_hook(self, hook: Hook, env: dict) -> HookResult:
        """Execute a single hook command."""
        try:
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=hook.timeout,
                )
                return HookResult(
                    hook=hook,
                    stdout=stdout.decode(errors="replace").strip(),
                    stderr=stderr.decode(errors="replace").strip(),
                    exit_code=proc.returncode or 0,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("Hook timed out after %ds: %s", hook.timeout, hook.command)
                return HookResult(
                    hook=hook,
                    stderr=f"Hook timed out after {hook.timeout}s",
                    exit_code=-1,
                    timed_out=True,
                )
        except Exception as e:
            logger.error("Hook execution failed: %s — %s", hook.command, e)
            return HookResult(
                hook=hook,
                stderr=str(e),
                exit_code=-1,
            )

    @property
    def fire_count(self) -> int:
        return self._fire_count
