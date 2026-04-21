"""TaskPlanner — 将复杂任务分解为可执行步骤。"""

import json
import logging
from typing import List, Optional

from .llm_client import BrainLLMClient
from .models import PlanStep, ThinkingContext

logger = logging.getLogger(__name__)


class TaskPlanner:
    """复杂任务的分解与规划。

    当 Brain 识别到 COMPLEX 意图时，调用 TaskPlanner
    将任务分解为多个 PlanStep。
    """

    def __init__(self, llm: BrainLLMClient) -> None:
        self._llm = llm

    async def plan(self, task: str, ctx: ThinkingContext) -> List[PlanStep]:
        """将复杂任务分解为可执行步骤。

        Args:
            task: 用户的任务描述
            ctx: 思考上下文（包含记忆、对话历史）

        Returns:
            有序的 PlanStep 列表
        """
        memory_context = ""
        if ctx.relevant_memories:
            memory_lines = [f"- {m.name}: {m.description}" for m in ctx.relevant_memories]
            memory_context = "相关记忆:\n" + "\n".join(memory_lines)

        system_prompt = f"""{ctx.soul_fragment}

你是一个任务规划助手。将复杂任务分解为有序的执行步骤。"""

        user_prompt = f"""请将以下任务分解为可执行的步骤：

## 任务
{task}

{memory_context}

## 规则
1. 每个步骤应该是原子性的、可独立执行的
2. executor 标记为 "brain"（思考/回复）或 "engine"（需要 mini_claude 执行代码/文件操作）
3. prompt 是该步骤实际执行时的指令
4. 如果某步骤依赖前面步骤的结果，在 depends_on 中标注步骤索引（从 0 开始）
5. 步骤数量控制在 2-6 步

返回 JSON 数组：
```json
[
  {{"description": "步骤描述", "executor": "brain|engine", "prompt": "执行指令", "depends_on": []}}
]
```

只输出 JSON 数组。"""

        try:
            text = await self._llm.think(system_prompt, user_prompt)
            steps = self._parse_steps(text)
            if steps:
                return steps
            logger.warning("任务规划解析为空，降级为单步执行")
        except Exception as e:
            logger.error("任务规划失败: %s", e)

        # 降级为单步执行
        return [
            PlanStep(
                description="直接执行任务",
                executor="engine",
                prompt=task,
            )
        ]

    async def replan(
        self,
        original_steps: List[PlanStep],
        completed_indices: List[int],
        error: Optional[str] = None,
    ) -> List[PlanStep]:
        """根据执行进度和错误重新规划。

        Args:
            original_steps: 原始步骤列表
            completed_indices: 已完成的步骤索引
            error: 最近一个步骤的错误信息

        Returns:
            调整后的剩余步骤
        """
        remaining = [
            s for i, s in enumerate(original_steps) if i not in completed_indices
        ]
        if not error:
            return remaining

        # 有错误时让 LLM 调整计划
        completed_desc = [
            original_steps[i].description for i in completed_indices
        ]
        remaining_desc = [s.description for s in remaining]

        user_prompt = f"""任务执行中遇到错误，请调整剩余计划。

## 已完成步骤
{json.dumps(completed_desc, ensure_ascii=False)}

## 错误信息
{error}

## 原剩余步骤
{json.dumps(remaining_desc, ensure_ascii=False)}

返回调整后的步骤 JSON 数组（格式同上）。只输出 JSON 数组。"""

        try:
            text = await self._llm.think("你是一个任务调整助手。", user_prompt)
            adjusted = self._parse_steps(text)
            return adjusted if adjusted else remaining
        except Exception as e:
            logger.warning("重新规划失败，继续原计划: %s", e)
            return remaining

    @staticmethod
    def _parse_steps(text: str) -> List[PlanStep]:
        """从 LLM 输出解析 PlanStep 列表。"""
        text = text.strip()

        # 提取 JSON 部分
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                cleaned = part.strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
                if cleaned.startswith("["):
                    text = cleaned
                    break

        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return []

        try:
            items = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []

        steps = []
        for item in items:
            if not isinstance(item, dict):
                continue
            steps.append(
                PlanStep(
                    description=item.get("description", ""),
                    executor=item.get("executor", "engine"),
                    prompt=item.get("prompt", ""),
                    depends_on=item.get("depends_on", []),
                )
            )
        return steps
