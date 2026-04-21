"""BashTool - Execute shell commands with streaming output.

Reference: src/tools/BashTool/BashTool.ts
"""

import asyncio
import logging
import os
import re
import signal
from typing import Optional, AsyncIterator, List

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult, ToolChunk

logger = logging.getLogger(__name__)

# Dangerous command patterns — matched against the full command string
BLOCKED_PATTERNS: List[str] = [
    r"rm\s+-[rf]*\s+/\s*$",       # rm -rf /
    r"rm\s+-[rf]*\s+/\*",         # rm -rf /*
    r"mkfs\b",                     # mkfs.*
    r">\s*/dev/sd[a-z]",           # > /dev/sda
    r":\(\)\s*\{\s*:\|:&\s*\};:", # fork bomb
    r"dd\s+if=/dev/zero\s+of=/dev/sd",  # dd wipe disk
]

_BLOCKED_RE = [re.compile(p) for p in BLOCKED_PATTERNS]


def is_command_blocked(command: str) -> Optional[str]:
    """Check if a command matches any blocked pattern. Returns the pattern if blocked."""
    for pattern, compiled in zip(BLOCKED_PATTERNS, _BLOCKED_RE):
        if compiled.search(command):
            return pattern
    return None


class BashTool(BaseTool):
    """Execute shell commands in the user's environment."""

    name = "Bash"
    description = (
        "Executes a shell command and returns its output. "
        "Use for running scripts, installing packages, git operations, "
        "and other system commands."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120)",
                "default": 120,
            },
            "description": {
                "type": "string",
                "description": "Brief description of what this command does",
            },
        },
        "required": ["command"],
    }
    supports_streaming = True
    permission_category = PermissionCategory.EXTERNAL

    def __init__(self, state=None, extra_blocked_patterns: Optional[List[str]] = None):
        super().__init__()
        self._state = state
        # Instance-level blocked patterns (extends globals without mutating them)
        self._extra_blocked_re: List[re.Pattern] = []
        if extra_blocked_patterns:
            for p in extra_blocked_patterns:
                try:
                    self._extra_blocked_re.append(re.compile(p))
                except re.error:
                    logger.warning("Invalid blocked pattern: %s", p)

    def _check_extra_blocked(self, command: str) -> Optional[str]:
        """Check instance-level extra blocked patterns."""
        for compiled in self._extra_blocked_re:
            if compiled.search(command):
                return compiled.pattern
        return None

    @property
    def _cwd(self) -> str:
        """Get working directory from state, falling back to os.getcwd()."""
        if self._state and hasattr(self._state, 'working_dir'):
            return self._state.working_dir
        return os.getcwd()

    async def execute(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        """Execute command and return full output."""
        command = params["command"]
        timeout = params.get("timeout", 120)

        # Check command blacklist (global + instance patterns)
        blocked = is_command_blocked(command)
        if not blocked:
            blocked = self._check_extra_blocked(command)
        if blocked:
            return ToolResult(
                content=f"Blocked: dangerous command detected (pattern: {blocked})",
                is_error=True,
            )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env={**os.environ, "TERM": "dumb"},
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                # Graceful shutdown: SIGTERM first, then SIGKILL after 3s
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except (asyncio.TimeoutError, ProcessLookupError):
                    proc.kill()
                    await proc.wait()
                return ToolResult(
                    content=f"Command timed out after {timeout}s",
                    is_error=True,
                )

            # Check for abort
            if abort_event and abort_event.is_set():
                proc.kill()
                return ToolResult(content="Command aborted", is_error=True)

            output = stdout.decode("utf-8", errors="replace")
            err_output = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                combined = output + err_output if output else err_output
                return ToolResult(
                    content=f"Exit code {proc.returncode}\n{combined}".strip(),
                    is_error=True,
                )

            # Combine stdout and stderr
            combined = output
            if err_output:
                combined += f"\n{err_output}" if combined else err_output

            return ToolResult(content=combined or "(no output)", is_error=False)

        except Exception as e:
            return ToolResult(content=f"Error executing command: {e}", is_error=True)

    async def execute_streaming(
        self,
        params: dict,
        abort_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[ToolChunk]:
        """Execute command with streaming stdout/stderr."""
        command = params["command"]
        timeout = params.get("timeout", 120)

        # Check command blacklist (global + instance patterns)
        blocked = is_command_blocked(command)
        if not blocked:
            blocked = self._check_extra_blocked(command)
        if blocked:
            yield ToolChunk(type="error", content=f"Blocked: dangerous command (pattern: {blocked})")
            yield ToolChunk(type="end", content="1")
            return

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env={**os.environ, "TERM": "dumb"},
            )

            # Use asyncio.Queue for interleaved streaming
            queue: asyncio.Queue = asyncio.Queue()
            streams_done = asyncio.Event()

            async def _reader(stream, chunk_type: str):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace")
                    await queue.put(ToolChunk(type=chunk_type, content=text))

            stdout_task = asyncio.create_task(_reader(proc.stdout, "text"))
            stderr_task = asyncio.create_task(_reader(proc.stderr, "error"))

            async def _wait_readers():
                await asyncio.gather(stdout_task, stderr_task)
                streams_done.set()
                await queue.put(None)  # sentinel

            waiter = asyncio.create_task(_wait_readers())

            # Yield chunks in real-time with timeout
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    # Timeout: graceful then forceful kill
                    try:
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(), timeout=3)
                    except (asyncio.TimeoutError, ProcessLookupError):
                        proc.kill()
                        await proc.wait()
                    yield ToolChunk(type="error", content=f"Command timed out after {timeout}s")
                    yield ToolChunk(type="end", content="-1")
                    waiter.cancel()
                    return

                if abort_event and abort_event.is_set():
                    proc.kill()
                    yield ToolChunk(type="error", content="Aborted")
                    yield ToolChunk(type="end", content="-1")
                    waiter.cancel()
                    return

                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    continue
                if chunk is None:
                    break
                yield chunk

            await proc.wait()
            yield ToolChunk(
                type="end",
                content=str(proc.returncode),
            )

        except Exception as e:
            yield ToolChunk(type="error", content=f"Error: {e}")
            yield ToolChunk(type="end", content="1")
