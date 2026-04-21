"""Brain 数据模型 — 意图、思考上下文、决策结果。"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class IntentType(str, Enum):
    """意图分类。"""

    CHITCHAT = "chitchat"        # 闲聊、问候、感谢
    STATUS = "status"            # 查询状态（任务、系统）
    CODING = "coding"            # 编码任务（需委派给 mini_claude）
    FILE_OPS = "file_ops"        # 文件操作（需委派给 mini_claude）
    KNOWLEDGE = "knowledge"      # 知识问答（Brain 可直接回答或需搜索）
    COMMAND = "command"          # 系统命令（重置、配置、管理）
    COMPLEX = "complex"          # 需要多步规划的复杂任务
    MEMORY = "memory"            # 记忆操作（记住/忘记/回忆）


@dataclass
class Intent:
    """解析后的用户意图。"""

    type: IntentType
    confidence: float                 # 0.0 ~ 1.0
    summary: str                      # 一句话概括用户意图
    requires_engine: bool             # 是否需要 mini_claude 引擎
    plan_steps: List[str] = field(default_factory=list)
    memory_keywords: List[str] = field(default_factory=list)
    emotional_tone: str = ""          # 用户消息中的情绪基调（如"难过""焦虑"）


@dataclass
class ThinkingContext:
    """Brain 思考时的上下文包。

    汇集 Soul 人格、情绪、对话历史、记忆、系统状态，
    供认知循环各步骤使用。
    """

    user_message: str
    user_id: str
    chat_id: str
    platform: str
    soul_fragment: str                # Soul 人格 system prompt 片段
    mood_context: str                 # 当前精力/心情描述
    relevant_memories: List[Any] = field(default_factory=list)  # MemoryEntry
    recent_conversation: List[Dict[str, str]] = field(default_factory=list)
    system_state: Dict[str, Any] = field(default_factory=dict)
    agents_rules: str = ""                # AGENTS.md 中的工作流规则
    diary_context: str = ""               # 最近日记内容（启动时加载）


@dataclass
class PlanStep:
    """多步计划中的一步。"""

    description: str
    executor: str                     # "brain" | "engine"
    prompt: str                       # 实际执行用的 prompt
    depends_on: List[int] = field(default_factory=list)
    result: Optional[str] = None
    completed: bool = False


@dataclass
class BrainDecision:
    """Brain 的决策结果。

    action 决定执行路径：
    - "reply": 直接回复（response_text 有值）
    - "delegate": 委派给 mini_claude（engine_prompt 有值）
    - "plan": 多步计划（plan 有值）
    - "memory_op": 记忆操作（memory_ops 有值）
    - "workspace_op": workspace 文件操作（workspace_ops 有值）
    """

    action: str
    response_text: Optional[str] = None
    engine_prompt: Optional[str] = None
    plan: Optional[List[PlanStep]] = None
    memory_ops: Optional[List[Dict[str, Any]]] = None
    workspace_ops: Optional[List[Dict[str, Any]]] = None
