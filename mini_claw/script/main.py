"""Mini Claw 主入口 — WorkspaceLoader 驱动 Soul → Brain → Hands 认知架构。"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import uvicorn

from .config import load_config
from .brain.cognitive import CognitiveLoop
from .brain.conversation import ConversationStore
from .brain.llm_client import BrainConfig, BrainLLMClient
from .gateway.adapters.webhook import WebhookAdapter
from .gateway.adapters.telegram import TelegramAdapter
from .gateway.adapters.feishu import FeishuAdapter
from .gateway.adapters.wecom import WecomAdapter
from .gateway.middleware.auth import AuthManager
from .gateway.middleware.rate_limit import RateLimiter
from .gateway.middleware.logging_mw import MessageLogger
from .gateway.models import BotResponse, UnifiedMessage
from .hands.manager import HandsManager
from .memory.extractor import MemoryExtractor
from .memory.loader import MemoryLoader
from .memory.store import MemoryStore
from .recovery.self_healer import SelfHealer
from .routine.scheduler import RoutineScheduler
from .soul.manager import SoulManager
from .workspace_loader import WorkspaceLoader

logger = logging.getLogger(__name__)


async def async_main(config_path: Optional[str] = None) -> None:
    """异步主函数 — v0.2 WorkspaceLoader 驱动启动。"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 加载技术配置（端口、API key、model 等）
    config = load_config(config_path)
    logger.info("Mini Claw v0.2 starting...")

    # ── Workspace（文件即记忆） ──────────────────────────────
    workspace = WorkspaceLoader(config.workspace_dir)
    logger.info("Workspace: %s", workspace.root)

    # ── Soul（人格：从 workspace 加载） ──────────────────────
    soul = SoulManager()
    soul.load_from_workspace(workspace)
    logger.info("Soul loaded: %s", soul.name)

    # 首次启动检测
    if workspace.is_first_boot():
        logger.info("首次启动！BOOTSTRAP.md 存在，将进入引导流程")

    # ── Memory（记忆：指向 workspace/memory/） ───────────────
    memory_store = MemoryStore(
        root=workspace.memory_dir,
        index_path=workspace.memory_index_path,
    )
    memory_loader = MemoryLoader(store=memory_store)
    memory_extractor = MemoryExtractor()

    # ── Hands（执行层：mini_claude 引擎池） ──────────────────
    hands = HandsManager(
        working_dir=str(workspace.root),
        permission_mode=config.engine.permission_mode,
        model=config.engine.model,
        personal_mode=config.is_personal,
    )

    # ── Brain（认知循环） ────────────────────────────────────
    brain_config = BrainConfig(
        model=config.brain.model,
        api_key=config.brain.api_key,
        base_url=config.brain.base_url,
        temperature=config.brain.temperature,
        max_tokens=config.brain.max_tokens,
        classify_temperature=config.brain.classify_temperature,
        classify_max_tokens=config.brain.classify_max_tokens,
    )
    brain_llm = BrainLLMClient(brain_config)

    conversation = ConversationStore(personal_mode=config.is_personal)

    # 从 workspace 加载 AGENTS.md 规则 + BOOTSTRAP.md 引导 + 日记上下文
    agents_rules = workspace.load_agents()
    bootstrap_prompt = workspace.load_bootstrap() if workspace.is_first_boot() else ""
    diary_context = workspace.list_recent_diaries(days=2)

    # ── Routine（自驱日程：从 HEARTBEAT.md 加载） ────────────

    routine_scheduler = RoutineScheduler(workspace_root=str(workspace.root))

    def _get_system_state() -> Dict[str, Any]:
        """汇总系统状态：引擎池 + 定时任务。"""
        state = hands.get_status()
        state["routine_jobs"] = routine_scheduler.list_jobs()
        return state

    # ── Recovery（自愈服务） ──────────────────────────────────
    self_healer = SelfHealer(
        llm=brain_llm,
        hands=hands,
        soul=soul,
        workspace=workspace,
    )

    cognitive = CognitiveLoop(
        llm=brain_llm,
        soul=soul,
        hands=hands,
        memory_store=memory_store,
        memory_loader=memory_loader,
        memory_extractor=memory_extractor,
        conversation=conversation,
        state_provider=_get_system_state,
        workspace=workspace,
        agents_rules=agents_rules,
        bootstrap_prompt=bootstrap_prompt,
        diary_context=diary_context,
        personal_mode=config.is_personal,
        self_healer=self_healer,
    )

    logger.info("Brain cognitive loop ready")

    async def routine_trigger(msg: UnifiedMessage) -> bool:
        """Routine 触发回调：送入 Brain 认知循环，结果推送到前端。

        返回 True 表示任务真正执行了，False 表示跳过。
        """
        response = await cognitive.process(msg)

        # 系统内部任务：用模型判断是否有真实异常
        is_system = msg.chat_id.startswith("routine_sys_")
        if is_system:
            try:
                judgment = await brain_llm.classify(
                    "你是一个系统监控判断器。判断以下系统检查报告是否存在需要人工介入的真实异常。"
                    "正常状态、信息性报告、'暂无法获取'等非致命提示都不算异常。"
                    "只有服务宕机、错误率飙升、磁盘满、引擎崩溃等严重问题才算异常。",
                    f"系统检查报告：\n{response.text}\n\n"
                    '返回 JSON: {"has_anomaly": true/false, "reason": "判断理由"}',
                )
                has_anomaly = judgment.get("has_anomaly", False)
                logger.info("系统检查判断: anomaly=%s, reason=%s",
                            has_anomaly, judgment.get("reason", ""))
                if not has_anomaly:
                    return True
                # 有异常：先尝试自愈
                logger.warning("系统检查发现异常，尝试自愈: %s", judgment.get("reason"))
                heal_result = await self_healer.heal_simple(
                    error=Exception(judgment.get("reason", "系统异常")),
                    user_id="system",
                    context_desc=f"系统健康检查发现异常: {response.text[:500]}",
                )
                if heal_result and heal_result.fix_ok:
                    logger.info("系统异常已自愈: %s", judgment.get("reason"))
                    return True
                # 自愈失败才通知用户
                logger.warning("系统异常自愈失败，通知用户")
            except Exception as e:
                logger.error("系统检查判断失败: %s，默认不通知", e)
                return True

        # 心跳任务：解析模型输出的结构化标记
        is_heartbeat = msg.chat_id.startswith("heartbeat_")
        if is_heartbeat:
            executed, meta_updates, clean_text = _parse_heartbeat_result(response.text)
            if not executed:
                logger.info("心跳结果无需推送 [%s]: %s", msg.chat_id, clean_text[:100])
                return False

            # 推送给用户（去掉标记行）
            logger.info("Routine 推送 [%s]: %s", msg.chat_id, clean_text[:200])
            target = msg.user_id if msg.user_id != "system" else "default"
            webhook.push_notification(target, clean_text, source=msg.chat_id)
            routine_scheduler.record_interaction()

            if meta_updates:
                task_name = msg.chat_id[len("heartbeat_"):]
                routine_scheduler.update_task_meta(task_name, meta_updates)
                logger.info("更新心跳任务 meta [%s]: %s", task_name, meta_updates)

            return True

        # 其他 routine 任务：直接推送
        logger.info("Routine 推送 [%s]: %s", msg.chat_id, response.text[:200])
        target = msg.user_id if msg.user_id != "system" else "default"
        webhook.push_notification(target, response.text, source=msg.chat_id)
        routine_scheduler.record_interaction()
        return True

    routine_scheduler._on_trigger = routine_trigger
    if config.routine.enabled:
        routine_scheduler.load_builtin()

        # 心跳任务：模型自主执行
        heartbeat_tasks = workspace.load_heartbeat()
        if heartbeat_tasks:
            routine_scheduler.load_heartbeat_tasks(heartbeat_tasks)

        await routine_scheduler.start()
        logger.info("Routine scheduler started (%d system jobs, %d heartbeat tasks)",
                     routine_scheduler.job_count, len(heartbeat_tasks) if heartbeat_tasks else 0)
    else:
        logger.info("Routine scheduler disabled")

    # ── 中间件 ───────────────────────────────────────────────
    mw = config.middleware
    auth_mgr = AuthManager(
        allowed_users=set(mw.allowed_users) if mw.allowed_users else None,
        admin_users=set(mw.admin_users) if mw.admin_users else set(),
        personal_mode=config.is_personal,
        owner_id=mw.owner_id,
    )
    rate_limiter = RateLimiter(
        capacity=mw.rate_limit_capacity,
        refill_rate=mw.rate_limit_refill,
        disabled=config.is_personal,
    )
    msg_logger = MessageLogger(log_dir=mw.message_log_dir or "logs")

    async def middleware_chain(msg: UnifiedMessage) -> BotResponse:
        """中间件链：日志 → 鉴权 → 限流 → Brain 认知循环。"""
        await msg_logger.log_incoming(msg)
        t0 = time.time()

        # 鉴权
        reject = await auth_mgr.authorize(msg)
        if reject:
            return BotResponse(text=reject)

        # 限流
        reject = await rate_limiter.check(msg)
        if reject:
            return BotResponse(text=reject)

        # Brain 认知循环处理
        response = await cognitive.process(msg)
        routine_scheduler.record_interaction()

        duration_ms = (time.time() - t0) * 1000
        await msg_logger.log_outgoing(msg.platform, msg.user_id, len(response.text), duration_ms)
        return response

    # ── Gateway 适配器 ───────────────────────────────────────

    # Webhook（始终启动：/health + /message + 聊天页面）
    webhook = WebhookAdapter(cognitive)
    webhook._routine_scheduler = routine_scheduler

    # 访问日志中间件
    from starlette.middleware.base import BaseHTTPMiddleware

    _QUIET_PATHS = ("/status", "/health", "/notifications", "/pending_question")

    class TimedAccessLogMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Any, call_next: Any) -> Any:
            start = time.time()
            response = await call_next(request)
            duration = (time.time() - start) * 1000
            if not request.url.path.startswith(_QUIET_PATHS):
                logger.info(
                    '%s %s %s - %.0fms',
                    request.method,
                    request.url.path,
                    response.status_code,
                    duration,
                )
            return response

    webhook.app.add_middleware(TimedAccessLogMiddleware)

    adapters_to_stop: List[Any] = []

    # Telegram（有 token 才启动）
    if config.telegram.bot_token:
        allowed = set(config.telegram.allowed_users) if config.telegram.allowed_users else None
        telegram = TelegramAdapter(
            bot_token=config.telegram.bot_token,
            handler=cognitive,
            allowed_users=allowed,
        )
        await telegram.start()
        adapters_to_stop.append(telegram)
        logger.info("Telegram adapter started")
    else:
        logger.info("No TELEGRAM_BOT_TOKEN, Telegram disabled")

    # 飞书（有 app_id 才启动）
    if config.feishu.app_id:
        feishu = FeishuAdapter(
            handler=cognitive,
            app_id=config.feishu.app_id,
            app_secret=config.feishu.app_secret,
            verification_token=config.feishu.verification_token,
            encrypt_key=config.feishu.encrypt_key,
        )
        feishu.on_message(middleware_chain)
        feishu.register_routes(webhook.app)
        await feishu.start()
        adapters_to_stop.append(feishu)
        logger.info("Feishu adapter started")
    else:
        logger.info("No FEISHU_APP_ID, Feishu disabled")

    # 企微（有 corp_id 或 webhook_url 才启动）
    if config.wecom.corp_id or config.wecom.webhook_url:
        wecom = WecomAdapter(
            handler=cognitive,
            corp_id=config.wecom.corp_id,
            corp_secret=config.wecom.corp_secret,
            agent_id=config.wecom.agent_id,
            callback_token=config.wecom.callback_token,
            encoding_aes_key=config.wecom.encoding_aes_key,
            webhook_url=config.wecom.webhook_url,
        )
        wecom.on_message(middleware_chain)
        wecom.register_routes(webhook.app)
        await wecom.start()
        adapters_to_stop.append(wecom)
        logger.info("Wecom adapter started")
    else:
        logger.info("No WECOM config, Wecom disabled")

    # ── 注册主动推送回调 ─────────────────────────────────────
    async def push_message(platform: str, chat_id: str, text: str) -> None:
        """将消息推送到对应平台。"""
        for adapter in adapters_to_stop:
            if hasattr(adapter, "platform") and adapter.platform == platform:
                if hasattr(adapter, "push"):
                    await adapter.push(chat_id, text)
                    return
        logger.warning("推送失败: 未找到平台 %s 的适配器", platform)

    cognitive._on_push = push_message

    # ── 启动 HTTP 服务器 ─────────────────────────────────────
    server_config = uvicorn.Config(
        webhook.app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(server_config)

    mode_label = "PERSONAL" if config.is_personal else "MULTI-USER"
    logger.info(
        "Mini Claw ready at http://%s:%d (%s, %s mode)",
        config.server.host,
        config.server.port,
        soul.name,
        mode_label,
    )

    # ── 启动问候：让 claw 主动打招呼 ────────────────────────
    async def _send_startup_greeting() -> None:
        try:
            soul_ctx = soul.get_system_prompt_fragment()

            if cognitive._bootstrap_prompt and not cognitive._bootstrapped:
                # 首次启动：只给人设，不给历史
                user_prompt = (
                    "[内部状态：首次启动，没有历史记忆。以下是行为指导，不是让你对用户说的话。]\n"
                    "按照 BOOTSTRAP.md 的引导向主人打个招呼并开始认识对方。\n"
                    "不要提及任何'上次''之前''最近在忙'的内容。\n"
                    "不要把系统提示中的状态描述直接说给用户。"
                )
                soul_ctx += "\n\n--- 首次启动引导 ---\n" + workspace.load_bootstrap()
            else:
                # 日常启动：给人设 + 最近日记，让问候有内容
                from datetime import datetime as _dt

                now_str = _dt.now().strftime("%Y-%m-%d %H:%M")
                diary = workspace.list_recent_diaries(days=1)
                if diary and diary.strip():
                    diary_hint = f"\n\n近期日记：\n{diary[:500]}"
                    user_prompt = (
                        f"当前时间：{now_str}。你刚刚醒来了，主动打个招呼。"
                        "可以结合下面的日记内容寻找一个话题。"
                        "注意日记条目带有日期标注，请根据日期准确描述时间。"
                        "只引用日记中实际存在的内容，不要编造。"
                        f"{diary_hint}"
                    )
                else:
                    user_prompt = (
                        "你刚刚醒来了，主动打个招呼。"
                        "你目前没有日记记录，不要编造之前的工作内容或项目。"
                        "不知道说什么就自由发挥，想到什么说什么。"
                    )

            text = await brain_llm.think(soul_ctx, user_prompt)
            webhook.push_notification("default", text, source="startup")
            logger.info("启动问候: %s", text[:500])
        except Exception as e:
            logger.warning("启动问候失败: %s", e)

    asyncio.create_task(_send_startup_greeting())

    try:
        await server.serve()
    finally:
        await routine_scheduler.stop()
        await hands.shutdown()
        for adapter in adapters_to_stop:
            await adapter.stop()
        logger.info("Mini Claw stopped")


def _parse_heartbeat_result(text: str) -> tuple:
    """解析心跳任务回复中的结构化标记。

    返回 (executed: bool, meta: dict, clean_text: str)。
    """
    import re
    match = re.search(r"<!--heartbeat_result:\s*(\{.*?\})\s*-->", text, re.DOTALL)
    if match:
        clean_text = text[:match.start()].rstrip()
        try:
            data = json.loads(match.group(1))
            return data.get("executed", True), data.get("meta") or {}, clean_text
        except (json.JSONDecodeError, AttributeError):
            pass
    # 无标记时默认视为已执行（兼容）
    return True, {}, text


def cli() -> None:
    """CLI 入口点（pyproject.toml [project.scripts]）。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="mini_claw",
        description="Mini Claw — Codeclaw digital worker platform",
    )
    parser.add_argument("--config", "-c", help="Path to config YAML file")
    args = parser.parse_args()

    try:
        asyncio.run(async_main(config_path=args.config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
