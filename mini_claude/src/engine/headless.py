"""无头模式入口 — 供 mini_claw 等外部程序调用 mini_claude 引擎。

不依赖终端 UI，纯编程接口。
"""

import os
import logging
from typing import Optional

from ..config import Config, load_config
from ..models.state import AppState
from ..services.llm import LLMClient
from ..services.context import ContextAssembler
from ..services.permissions import PermissionManager
from ..services.compaction import ContextCompactor
from ..tools.registry import ToolRegistry
from ..main import register_all_tools, register_agent_tool
from .query import QueryEngine

logger = logging.getLogger(__name__)


async def create_engine(
    working_dir: Optional[str] = None,
    config: Optional[Config] = None,
    permission_mode: str = "auto",
    system_prompt_extra: str = "",
    on_ask_user=None,
) -> QueryEngine:
    """创建一个无头 QueryEngine 实例，供编程调用。

    Args:
        working_dir: 工作目录，默认 cwd。
        config: LLM 配置，默认从 settings.yaml / 环境变量加载。
        permission_mode: 权限模式，默认 "auto"（全部自动批准）。
        system_prompt_extra: 追加到 system prompt 末尾的额外内容。

    Returns:
        可直接调用 engine.run_turn(text) 的 QueryEngine 实例。
    """
    # 1. 配置
    if config is None:
        config = load_config()

    # 2. 状态
    wd = working_dir or os.getcwd()
    state = AppState(working_dir=wd)

    # 3. 工具
    registry = ToolRegistry()
    register_all_tools(registry)

    # 4. LLM
    llm = LLMClient(config)

    # 5. Agent 工具（需要 llm + state）
    register_agent_tool(registry, llm, state)

    # 6. System prompt
    context_asm = ContextAssembler(wd)
    state.system_prompt = await context_asm.build_system_prompt()
    if system_prompt_extra:
        state.system_prompt += f"\n\n{system_prompt_extra}"

    # 7. 权限 — 无头模式默认全部自动批准
    permissions = PermissionManager(mode=permission_mode)

    # 8. 上下文压缩
    compactor = ContextCompactor()

    # 9. 组装引擎（不接 UI callback）
    engine = QueryEngine(
        llm=llm,
        tools=registry,
        state=state,
        permissions=permissions,
        compactor=compactor,
    )

    # 注入 AskUser 回调（供 Web 前端等非终端环境使用）
    if on_ask_user is not None:
        ask_tool = registry.get("AskUser")
        if ask_tool is not None:
            ask_tool.set_callback(on_ask_user)

    logger.info("Headless engine created: working_dir=%s, model=%s", wd, config.model)
    return engine


async def run_headless(
    input_text: str,
    working_dir: Optional[str] = None,
    config: Optional[Config] = None,
    permission_mode: str = "auto",
    system_prompt_extra: str = "",
    on_ask_user=None,
) -> str:
    """一次性执行：创建引擎 → 执行一轮对话 → 返回结果。

    适合无需保持会话的场景。如需多轮对话，用 create_engine() 保持引擎实例。
    """
    engine = await create_engine(
        working_dir=working_dir,
        config=config,
        permission_mode=permission_mode,
        system_prompt_extra=system_prompt_extra,
        on_ask_user=on_ask_user,
    )
    return await engine.run_turn(input_text)
