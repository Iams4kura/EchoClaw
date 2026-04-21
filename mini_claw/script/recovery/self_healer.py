"""SelfHealer — 独立的错误自愈服务。

任何组件遇到运行时错误都可以调用，不依赖认知循环。

流程：分析 → mclaude 修复 → 热重载 → (可选)验证 → 记录到 ERRORS.md
"""

import importlib
import logging
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional

from ..brain.llm_client import BrainLLMClient
from ..gateway.models import BotResponse, UnifiedMessage
from ..hands.manager import HandsManager
from ..soul.manager import SoulManager
from ..workspace_loader import WorkspaceLoader
from .models import SelfHealResult

logger = logging.getLogger(__name__)


class SelfHealer:
    """独立自愈服务，可被任何组件调用。"""

    def __init__(
        self,
        llm: BrainLLMClient,
        hands: HandsManager,
        soul: SoulManager,
        workspace: Optional[WorkspaceLoader] = None,
    ) -> None:
        self._llm = llm
        self._hands = hands
        self._soul = soul
        self._workspace = workspace

    # ── 公开入口 1：完整自愈（用于认知循环等需要验证+返回响应的场景）──

    async def heal(
        self,
        error: Exception,
        msg: UnifiedMessage,
        state: Any,
        verify_fn: Optional[Callable[..., Awaitable[BotResponse]]] = None,
    ) -> BotResponse:
        """完整自愈：分析 → 修复 → 热重载 → 验证 → 记录 → 返回 BotResponse。

        Args:
            error: 捕获到的异常
            msg: 触发错误的消息
            state: 用户处理状态（需要有 reset_for_new_message 方法）
            verify_fn: 验证回调，签名 (msg, state) -> BotResponse。不传则跳过验证。
        """
        result = await self._do_heal(
            error=error,
            user_id=msg.user_id,
            trigger_desc=msg.content[:300],
            verify_fn=verify_fn,
            verify_args=(msg, state),
            state=state,
        )

        # 验证通过 → 直接返回重试结果
        if result.verified and hasattr(result, "_retry_response"):
            return result._retry_response

        # 未通过 → 构建错误响应给用户
        user_notice = self._soul.get_error_message(str(error))
        situation = (
            f"\n\n---\n"
            f"错误详情：`{type(error).__name__}: {error}`\n"
            f"分析：{result.analysis[:200] if result.analysis else '未知'}\n"
        )
        if result.fix_ok and result.needs_restart:
            situation += "状态：已修复代码，但改动需要重启才能生效。请重启服务后重试。\n"
        elif result.fix_ok and not result.verified:
            situation += "状态：已尝试自动修复但验证未通过，可能需要手动介入。\n"
        elif not result.fix_ok:
            situation += "状态：自动修复未成功，需要手动排查。\n"

        return BotResponse(text=user_notice + situation, reply_to=msg.message_id)

    # ── 公开入口 2：简化自愈（用于 webhook / routine 等不需要验证的场景）──

    async def heal_simple(
        self,
        error: Exception,
        user_id: str,
        context_desc: str,
    ) -> SelfHealResult:
        """简化自愈：分析 → 修复 → 热重载 → 记录。无验证、无 BotResponse。

        Args:
            error: 捕获到的异常
            user_id: 触发用户（用于调用 mclaude）
            context_desc: 触发上下文描述（如 "webhook /api/routines 请求处理"）
        """
        return await self._do_heal(
            error=error,
            user_id=user_id,
            trigger_desc=context_desc,
        )

    # ── 内部核心流程 ─────────────────────────────────────────────

    async def _do_heal(
        self,
        error: Exception,
        user_id: str,
        trigger_desc: str,
        verify_fn: Optional[Callable] = None,
        verify_args: tuple = (),
        state: Any = None,
    ) -> SelfHealResult:
        """自愈核心：分析 → 修复 → 热重载 → (可选)验证 → 记录。"""
        eid = uuid.uuid4().hex[:8]
        tb = traceback.format_exception(type(error), error, error.__traceback__)
        tb_text = "".join(tb[-6:])

        logger.info("进入自愈流程 [%s]: %s: %s", eid, type(error).__name__, error)

        result = SelfHealResult(error_id=eid)

        # ── Step 1: Brain LLM 分析错误 ──
        try:
            result.analysis = await self._llm.think(
                "你是一个 Python 后端错误分析专家。分析以下运行时错误，给出：\n"
                "1. 根本原因（1-2句话）\n"
                "2. 具体修复方案（说明改哪个文件的哪段代码，怎么改）\n"
                "简洁、精准，不要废话。",
                f"错误类型: {type(error).__name__}\n"
                f"错误信息: {error}\n"
                f"触发: {trigger_desc}\n"
                f"调用栈:\n{tb_text}",
            )
            logger.info("自愈分析 [%s]: %s", eid, result.analysis[:300])
        except Exception as ae:
            result.analysis = f"分析失败: {ae}"
            logger.warning("自愈分析失败 [%s]: %s", eid, ae)

        # ── Step 2: 调用 Hands (mclaude) 执行修复 ──
        fix_result = None
        try:
            fix_prompt = (
                f"系统运行时发生了错误，请修复它。\n\n"
                f"## 错误信息\n"
                f"```\n{type(error).__name__}: {error}\n```\n\n"
                f"## 调用栈\n"
                f"```\n{tb_text}```\n\n"
                f"## 分析\n{result.analysis}\n\n"
                f"## 要求\n"
                f"- 根据分析定位并修复代码中的 bug\n"
                f"- 只改必要的代码，不要做额外重构\n"
                f"- 修复后简要说明你改了什么"
            )
            fix_result = await self._hands.execute(user_id, fix_prompt)
            result.fix_ok = fix_result.success
            result.fix_output = (fix_result.output or "")[:500]
            logger.info(
                "自愈修复 [%s]: success=%s, output=%s",
                eid, result.fix_ok, result.fix_output[:200],
            )
        except Exception as fe:
            logger.warning("自愈修复调用失败 [%s]: %s", eid, fe)

        # ── Step 3: 热重载被修改的模块 ──
        if result.fix_ok:
            changed = self.extract_modules_from_traceback(error)
            for mod_name in changed:
                try:
                    mod = sys.modules.get(mod_name)
                    if mod is None:
                        continue
                    importlib.reload(mod)
                    result.reloaded_modules.append(mod_name)
                    logger.info("自愈热重载 [%s]: %s", eid, mod_name)
                except Exception as re_err:
                    logger.warning(
                        "自愈热重载失败 [%s]: %s → %s", eid, mod_name, re_err,
                    )
                    result.needs_restart = True

        # ── Step 4: 验证（仅当传入 verify_fn 时）──
        if result.fix_ok and verify_fn is not None:
            try:
                if state is not None and hasattr(state, "reset_for_new_message"):
                    state.reset_for_new_message(verify_args[0].content if verify_args else "")
                retry_response = await verify_fn(*verify_args)
                result.verified = True
                result._retry_response = retry_response  # type: ignore[attr-defined]
                logger.info("自愈验证成功 [%s]: 重试通过", eid)
            except Exception as ve:
                logger.warning("自愈验证失败 [%s]: %s", eid, ve)
                if not result.reloaded_modules or result.needs_restart:
                    result.needs_restart = True

        # ── Step 5: 记录到 ERRORS.md ──
        self._record_error(eid, error, trigger_desc, tb_text, result)

        return result

    def _record_error(
        self,
        eid: str,
        error: Exception,
        trigger_desc: str,
        tb_text: str,
        result: SelfHealResult,
    ) -> None:
        """结构化写入 ERRORS.md。"""
        if not self._workspace:
            return
        try:
            reload_info = ""
            if result.reloaded_modules:
                reload_info = f"热重载模块: {', '.join(result.reloaded_modules)}\n"
            if result.needs_restart and not result.verified:
                reload_info += "注意: 部分修改需要重启才能生效\n"

            if result.verified:
                status_label = "已修复并验证通过"
            elif result.fix_ok and result.needs_restart:
                status_label = "已修复，需重启生效"
            elif result.fix_ok:
                status_label = "修复已执行但验证未通过"
            else:
                status_label = "修复失败"

            record = (
                f"**错误**: `{type(error).__name__}: {error}`\n\n"
                f"**触发**: {trigger_desc[:200]}\n\n"
                f"**调用栈**:\n```\n{tb_text}```\n\n"
                f"**原因分析**: {result.analysis}\n\n"
                f"**修复操作**: {result.fix_output or '未执行'}\n\n"
                f"{reload_info}"
                f"**结果**: {status_label}\n"
            )
            self._workspace.append_error(eid, record)
        except Exception:
            pass

    # ── 工具方法 ─────────────────────────────────────────────────

    @staticmethod
    def extract_modules_from_traceback(error: Exception) -> List[str]:
        """从异常 traceback 中提取属于 mini_claw 包的模块名。

        只返回 mini_claw 下的模块，外部库不管。
        """
        modules: List[str] = []
        seen: set = set()
        tb = error.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            filename = frame.f_code.co_filename
            if "mini_claw" in filename and filename.endswith(".py"):
                for name, mod in sys.modules.items():
                    if (
                        name not in seen
                        and hasattr(mod, "__file__")
                        and mod.__file__
                        and Path(mod.__file__).resolve()
                        == Path(filename).resolve()
                    ):
                        modules.append(name)
                        seen.add(name)
                        break
            tb = tb.tb_next
        return modules
