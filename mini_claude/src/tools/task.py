"""TaskTool - Background task management.

Reference: src/tools/TaskStopTool/TaskStopTool.ts
"""

import asyncio
from typing import Optional

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult
from ..models.state import AppState


class TaskStopTool(BaseTool):
    """Stop a running background task."""

    name = "TaskStop"
    description = "Stop a running background task by its ID."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the task to stop",
            },
        },
        "required": ["task_id"],
    }
    permission_category = PermissionCategory.EXTERNAL

    def __init__(self, state: Optional[AppState] = None):
        super().__init__()
        self._state = state

    async def execute(
        self, params: dict, abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        task_id = params["task_id"]

        if not self._state:
            return ToolResult(content="No state available", is_error=True)

        if task_id not in self._state.active_tasks:
            return ToolResult(content=f"Task {task_id} not found", is_error=True)

        task = self._state.active_tasks[task_id]
        if task.status in ("completed", "failed", "killed"):
            return ToolResult(
                content=f"Task {task_id} already {task.status}",
                is_error=False,
            )

        task.status = "killed"
        return ToolResult(content=f"Task {task_id} stopped", is_error=False)
