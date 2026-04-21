"""Parallel tool scheduling and execution.

Reference: src/services/tools/toolOrchestration.ts, src/utils/toolGrouping.ts
"""

import asyncio
import logging
from typing import List, Optional, Dict, Set
from dataclasses import dataclass, field

from ..models.message import ToolUseBlock
from ..models.tool import ToolResult
from ..models.state import AppState
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ExecutionGroup:
    """A group of tools that can run in parallel."""
    tool_uses: List[ToolUseBlock] = field(default_factory=list)


class ToolOrchestrator:
    """Analyzes tool calls and schedules parallel execution.

    Groups independent tools for concurrent execution while
    respecting dependencies.
    """

    def __init__(self, registry: ToolRegistry, state: AppState):
        self.registry = registry
        self.state = state

    def analyze_dependencies(
        self, tool_uses: List[ToolUseBlock]
    ) -> List[ExecutionGroup]:
        """Group tool calls into parallel execution groups.

        Tools are independent if they:
        - Don't write to the same files
        - Don't read files that another tool writes (read-after-write)
        - Are all read-only, or write to different targets
        - Bash commands are always serialized (shared cwd state)
        """
        if len(tool_uses) <= 1:
            return [ExecutionGroup(tool_uses=tool_uses)]

        # Track which files each tool reads and writes
        write_targets: Dict[int, Set[str]] = {}
        read_targets: Dict[int, Set[str]] = {}
        is_bash: Dict[int, bool] = {}

        for i, tu in enumerate(tool_uses):
            writes = set()
            reads = set()
            tool = self.registry.get(tu.name)
            is_bash[i] = (tu.name.lower() == "bash")

            file_path = tu.input.get("file_path", "")
            if tool and tool.permission_category in ("write", "destructive", "external"):
                if file_path:
                    writes.add(file_path)
                command = tu.input.get("command", "")
                if command and is_bash[i]:
                    # Bash commands share cwd — use sentinel to force serialization
                    writes.add("__bash_cwd__")
                    reads |= self._extract_bash_read_targets(command)
            elif file_path:
                reads.add(file_path)

            # Glob/Grep pattern targets
            pattern_path = tu.input.get("path", "")
            if pattern_path and tool and tool.permission_category == "read":
                reads.add(pattern_path)

            write_targets[i] = writes
            read_targets[i] = reads

        # Build groups respecting conflicts
        groups: List[ExecutionGroup] = []
        used = set()

        for i, tu in enumerate(tool_uses):
            if i in used:
                continue

            group = ExecutionGroup(tool_uses=[tu])
            used.add(i)
            group_writes = write_targets[i].copy()
            group_reads = read_targets[i].copy()
            group_has_bash = is_bash[i]

            for j in range(i + 1, len(tool_uses)):
                if j in used:
                    continue

                # Bash commands always conflict with each other (shared cwd)
                if group_has_bash and is_bash[j]:
                    continue

                has_conflict = bool(
                    (group_writes & write_targets[j])
                    or (group_writes & read_targets[j])
                    or (group_reads & write_targets[j])
                )
                if not has_conflict:
                    group.tool_uses.append(tool_uses[j])
                    used.add(j)
                    group_writes |= write_targets[j]
                    group_reads |= read_targets[j]
                    if is_bash[j]:
                        group_has_bash = True

            groups.append(group)

        return groups

    @staticmethod
    def _extract_bash_read_targets(command: str) -> Set[str]:
        """Extract likely file read targets from a bash command."""
        import re
        targets = set()
        # Match common read commands, extract all non-option arguments as paths
        for m in re.finditer(r'(?:cat|head|tail|less|source|\.)\s+((?:[^\|;&])+)', command):
            args_str = m.group(1)
            for token in args_str.split():
                if not token.startswith('-') and '/' in token:
                    targets.add(token)
        # Input redirection: < file
        for m in re.finditer(r'<\s*([^\s|;&]+)', command):
            targets.add(m.group(1))
        return targets

    async def execute_parallel(
        self,
        tool_uses: List[ToolUseBlock],
    ) -> List[ToolResult]:
        """Execute tools with maximum parallelism.

        1. Analyze dependencies to find groups
        2. Execute each group in parallel
        3. Collect results in original order
        """
        groups = self.analyze_dependencies(tool_uses)

        # Map tool_use.id -> result for ordered collection
        results_map: Dict[str, ToolResult] = {}

        for group in groups:
            if self.state.is_aborted():
                for tu in group.tool_uses:
                    results_map[tu.id] = ToolResult(content="Aborted", is_error=True)
                continue

            # Execute group in parallel
            tasks = []
            for tu in group.tool_uses:
                tasks.append(self._execute_single(tu))

            group_results = await asyncio.gather(*tasks, return_exceptions=True)

            for tu, result in zip(group.tool_uses, group_results):
                if isinstance(result, Exception):
                    results_map[tu.id] = ToolResult(
                        content=f"Error: {result}", is_error=True
                    )
                else:
                    results_map[tu.id] = result

        # Return in original order
        return [results_map.get(tu.id, ToolResult(content="Missing", is_error=True))
                for tu in tool_uses]

    async def _execute_single(self, tool_use: ToolUseBlock) -> ToolResult:
        """Execute a single tool."""
        tool = self.registry.get(tool_use.name)
        if not tool:
            return ToolResult(content=f"Unknown tool: {tool_use.name}", is_error=True)

        # Bash cd tracking: append pwd to cd commands
        params = tool_use.input
        if tool_use.name.lower() == "bash":
            params = self._maybe_track_cd(params)

        self.state.current_tool_use = tool_use.id
        try:
            result = await tool.execute(params, self.state.abort_event)
            # After Bash execution, check for cwd change
            if tool_use.name.lower() == "bash" and not result.get("is_error", False):
                self._update_cwd_from_result(params, result)
            return result
        except Exception as e:
            logger.error(f"Tool {tool_use.name} error: {e}")
            return ToolResult(content=f"Tool error: {e}", is_error=True)
        finally:
            self.state.current_tool_use = None

    def _maybe_track_cd(self, params: dict) -> dict:
        """If command contains cd, append `&& pwd` to track new cwd."""
        import re
        command = params.get("command", "")
        if re.search(r'\bcd\s', command):
            # Append pwd so we can extract the new cwd from output
            params = dict(params)
            params["command"] = f"{command} && echo '___MC_CWD___' && pwd"
        return params

    def _update_cwd_from_result(self, params: dict, result: ToolResult) -> None:
        """Extract cwd from Bash output if cd tracking was active."""
        content = result.get("content", "")
        marker = "___MC_CWD___"
        if marker in content:
            parts = content.split(marker)
            if len(parts) >= 2:
                # pwd output is the first non-empty line after the marker
                lines_after = [l.strip() for l in parts[-1].strip().split("\n") if l.strip()]
                new_cwd = lines_after[0] if lines_after else ""
                if new_cwd and new_cwd.startswith("/"):
                    import os
                    if os.path.isdir(new_cwd):
                        self.state.working_dir = new_cwd
                        logger.info("Updated working_dir to: %s", new_cwd)
            # Clean up the marker from the output
            result["content"] = content.replace(f"\n{marker}\n", "\n").replace(marker, "").strip()
