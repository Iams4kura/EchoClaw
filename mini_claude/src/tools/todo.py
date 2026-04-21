"""TodoWriteTool - Task list management.

Reference: src/tools/TodoWriteTool/TodoWriteTool.ts
"""

import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult


class TodoWriteTool(BaseTool):
    """Manage a task list for the current session."""

    name = "TodoWrite"
    description = (
        "Create and manage a task list. Supports creating tasks, "
        "updating status (pending/in_progress/completed), and listing all tasks."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "update", "list"],
                "description": "Action to perform",
            },
            "subject": {
                "type": "string",
                "description": "Task subject (for create)",
            },
            "description": {
                "type": "string",
                "description": "Task description (for create)",
            },
            "task_id": {
                "type": "string",
                "description": "Task ID (for update)",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "New status (for update)",
            },
        },
        "required": ["action"],
    }
    permission_category = PermissionCategory.WRITE

    def __init__(self):
        super().__init__()
        self._todos: Dict[str, dict] = {}
        self._next_id = 1

    async def execute(
        self, params: dict, abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        action = params["action"]

        if action == "create":
            return self._create(params)
        elif action == "update":
            return self._update(params)
        elif action == "list":
            return self._list()
        else:
            return ToolResult(content=f"Unknown action: {action}", is_error=True)

    def _create(self, params: dict) -> ToolResult:
        subject = params.get("subject", "Untitled")
        description = params.get("description", "")
        task_id = str(self._next_id)
        self._next_id += 1

        self._todos[task_id] = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "created": datetime.now().isoformat(),
        }
        return ToolResult(content=f"Created task #{task_id}: {subject}", is_error=False)

    def _update(self, params: dict) -> ToolResult:
        task_id = params.get("task_id", "")
        if task_id not in self._todos:
            return ToolResult(content=f"Task #{task_id} not found", is_error=True)

        status = params.get("status")
        if status:
            self._todos[task_id]["status"] = status

        return ToolResult(
            content=f"Updated task #{task_id}: status={self._todos[task_id]['status']}",
            is_error=False,
        )

    def _list(self) -> ToolResult:
        if not self._todos:
            return ToolResult(content="No tasks.", is_error=False)

        lines = []
        for tid, task in self._todos.items():
            icon = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}.get(task["status"], "[ ]")
            lines.append(f"{icon} #{tid} {task['subject']} ({task['status']})")

        return ToolResult(content="\n".join(lines), is_error=False)
