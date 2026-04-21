"""AskUserTool - Interactive user prompts.

Reference: src/tools/AskUserQuestionTool/AskUserQuestionTool.ts
"""

import asyncio
from typing import Any, Awaitable, Callable, List, Optional

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult

# callback signature: (question: str, options: list[str]) -> answer: str
AskUserCallback = Callable[[str, List[str]], Awaitable[str]]


class AskUserTool(BaseTool):
    """Ask the user a question and return their response."""

    name = "AskUser"
    description = (
        "Ask the user a question to gather information, clarify requirements, "
        "or get a decision. The user's response is returned as the tool result."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of choices to present",
            },
        },
        "required": ["question"],
    }
    supports_streaming = True
    permission_category = PermissionCategory.READ

    def __init__(self) -> None:
        super().__init__()
        self._on_ask_user: Optional[AskUserCallback] = None

    def set_callback(self, callback: Optional[AskUserCallback]) -> None:
        """注入外部回调，用于将交互路由到 Web 前端等非终端环境。"""
        self._on_ask_user = callback

    async def execute(
        self, params: dict, abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        question = params["question"]
        options = params.get("options", [])

        # 优先走外部回调（Web 前端等）
        if self._on_ask_user is not None:
            try:
                answer = await self._on_ask_user(question, options)
                return ToolResult(content=answer.strip(), is_error=False)
            except Exception:
                return ToolResult(content="(no response)", is_error=False)

        # 终端 fallback
        print(f"\n{question}")

        if options:
            for i, opt in enumerate(options, 1):
                print(f"  {i}. {opt}")
            print(f"  {len(options) + 1}. Other (type your answer)")

        try:
            answer = await asyncio.to_thread(input, "Your answer: ")

            # If options provided and user typed a number
            if options and answer.strip().isdigit():
                idx = int(answer.strip()) - 1
                if 0 <= idx < len(options):
                    answer = options[idx]

            return ToolResult(content=answer.strip(), is_error=False)

        except (EOFError, KeyboardInterrupt):
            return ToolResult(content="(no response)", is_error=False)
