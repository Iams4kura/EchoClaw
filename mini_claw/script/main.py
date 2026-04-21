"""Mini Claw 主入口 — WorkspaceLoader 驱动 Soul → Brain → Hands 认知架构。"""

import asyncio
import logging
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

    async def routine_trigger(msg: UnifiedMessage) -> None:
        """Routine 触发回调：送入 Brain 认知循环，结果推送到前端。"""
        response = await cognitive.process(msg)
        logger.info("Routine 结果 [%s]: %s", msg.chat_id, response.text[:200])
        # 系统内部任务（如健康检查）结果不推送给用户，仅记录日志
        # 除非结果中包含异常/错误关键词
        is_system = msg.chat_id.startswith("routine_sys_")
        if is_system:
            alert_keywords = ["异常", "错误", "失败", "警告", "error", "fail", "warn"]
            if not any(kw in response.text for kw in alert_keywords):
                return
        # 推送到 webhook 前端
        target = msg.user_id if msg.user_id != "system" else "default"
        webhook.push_notification(target, response.text, source=msg.chat_id)

    routine_scheduler._on_trigger = routine_trigger
    if config.routine.enabled:
        routine_scheduler.load_builtin()

        # 心跳任务：模型自主判断是否执行
        heartbeat_tasks = workspace.load_heartbeat()
        if heartbeat_tasks:
            routine_scheduler.load_heartbeat_tasks(heartbeat_tasks)

            # 设置心跳判断回调（用 Brain LLM 直接判断，不走完整认知循环）
            async def heartbeat_judge(content: str) -> str:
                return await brain_llm.think("你是一个任务调度助手。根据当前时间和每个任务的条件，判断哪些任务需要执行。", content)

            routine_scheduler._on_heartbeat_judge = heartbeat_judge

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

        duration_ms = (time.time() - t0) * 1000
        await msg_logger.log_outgoing(msg.platform, msg.user_id, len(response.text), duration_ms)
        return response

    # ── Gateway 适配器 ───────────────────────────────────────

    # Webhook（始终启动：/health + /message + 聊天页面）
    webhook = WebhookAdapter(cognitive)

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
            if config.is_personal:
                greeting_prompt = (
                    "你刚刚醒来了。作为主人的数字分身，主动打个招呼。"
                    "根据当前时间段用你的风格说一句简短的问候，"
                    "可以提一下你还记得最近在忙什么。自然、亲切，像老朋友一样。"
                )
            else:
                greeting_prompt = (
                    "你刚刚启动上线了，请主动和用户打个招呼，"
                    "根据当前时间段（早上/下午/晚上）用你的风格说一句简短的问候。"
                    "不需要太长，自然一点。"
                )
            msg = UnifiedMessage(
                platform="routine",
                user_id="system",
                chat_id="startup_greeting",
                content=greeting_prompt,
            )
            response = await cognitive.process(msg)
            webhook.push_notification("default", response.text, source="startup")
            logger.info("启动问候: %s", response.text[:100])
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
