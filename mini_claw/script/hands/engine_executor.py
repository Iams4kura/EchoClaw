"""EngineExecutor — 单用户的 mini_claude 引擎实例管理。

从 engine_session.EngineSession 重构而来：
- 去除对话跟踪（由 Brain 管理）
- 去除 skill 展开（由 Brain 决策层处理）
- 保留纯粹的引擎生命周期和执行接口
"""

import logging
import time
from typing import Any, Dict, Optional

from src.engine.headless import create_engine

from .models import ExecutionResult

logger = logging.getLogger(__name__)


class EngineExecutor:
    """单用户的 mini_claude 引擎执行器。

    Brain 委派编码/文件任务时，通过 EngineExecutor 调用 mini_claude 引擎。
    每次 execute() 调用都是独立的（引擎可保持多轮上下文，
    但 prompt 由 Brain 构造完整）。
    """

    def __init__(
        self,
        working_dir: str = ".",
        permission_mode: str = "auto",
        model: Optional[str] = None,
        system_prompt_extra: str = "",
        on_ask_user: Any = None,
    ) -> None:
        self._working_dir = working_dir
        self._permission_mode = permission_mode
        self._model = model
        self._system_prompt_extra = system_prompt_extra
        self._on_ask_user = on_ask_user
        self._engine: Any = None
        self._initialized = False

    async def initialize(self) -> None:
        """初始化 mini_claude 引擎实例。"""
        if self._initialized:
            return

        config = None
        if self._model:
            from src.config import load_config

            config = load_config()
            config.model = self._model

        self._engine = await create_engine(
            working_dir=self._working_dir,
            config=config,
            permission_mode=self._permission_mode,
            system_prompt_extra=self._system_prompt_extra,
            on_ask_user=self._on_ask_user,
        )
        self._initialized = True
        logger.info(
            "EngineExecutor 初始化完成: working_dir=%s",
            self._working_dir,
        )

    async def execute(self, prompt: str) -> ExecutionResult:
        """执行一次任务。

        Args:
            prompt: Brain 构造的完整 prompt，包含用户请求 + 记忆上下文。

        Returns:
            ExecutionResult 包含执行结果、耗时和错误信息。
        """
        if not self._initialized:
            await self.initialize()

        start_time = time.time()
        try:
            result = await self._engine.run_turn(prompt)
            duration_ms = (time.time() - start_time) * 1000
            return ExecutionResult(
                success=True,
                output=result or "",
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("EngineExecutor 执行失败: %s", e)
            return ExecutionResult(
                success=False,
                output="",
                duration_ms=duration_ms,
                error=str(e),
            )

    async def reset(self) -> None:
        """重置引擎状态（清空上下文，重新初始化）。"""
        self._engine = None
        self._initialized = False
        await self.initialize()

    async def teardown(self) -> None:
        """销毁引擎实例。"""
        self._engine = None
        self._initialized = False
        logger.info("EngineExecutor 已销毁")

    @property
    def is_ready(self) -> bool:
        return self._initialized and self._engine is not None

    @property
    def engine_config(self) -> Dict[str, Any]:
        """返回引擎配置概要（用于状态查询）。"""
        return {
            "working_dir": self._working_dir,
            "permission_mode": self._permission_mode,
            "model": self._model,
            "initialized": self._initialized,
        }
