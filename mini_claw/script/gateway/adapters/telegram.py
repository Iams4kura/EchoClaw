"""Telegram Bot 适配器 — 通过轮询接收消息，调用引擎，回复结果。"""

import asyncio
import logging
from typing import Any, Optional, Set

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..models import BotResponse, UnifiedMessage

logger = logging.getLogger(__name__)

# Telegram 单条消息最大长度
MAX_MESSAGE_LENGTH = 4000
# 持续 typing 指示器间隔（秒）
TYPING_INTERVAL = 4.0


class TelegramAdapter:
    """Telegram Bot 适配器（轮询模式）。

    命令：
    - /start — 欢迎语
    - /reset — 清空当前会话
    - /status — 查看会话状态

    普通文本消息 → 调用引擎 → 回复结果。

    v0.2: handler 可以是 Brain CognitiveLoop 或旧版 SessionManager。
    """

    def __init__(
        self,
        bot_token: str,
        handler: Any,
        allowed_users: Optional[Set[int]] = None,
    ) -> None:
        self._token = bot_token
        self._handler = handler
        self._allowed_users = allowed_users
        self._app = ApplicationBuilder().token(bot_token).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("reset", self._on_reset))
        self._app.add_handler(CommandHandler("status", self._on_status))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )

    async def start(self) -> None:
        """启动 Bot 轮询。"""
        await self._app.initialize()
        await self._app.start()
        if self._app.updater:
            await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram adapter started (polling mode)")

    async def stop(self) -> None:
        """优雅关闭。"""
        if self._app.updater:
            await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        logger.info("Telegram adapter stopped")

    def _check_auth(self, user_id: int) -> bool:
        """检查用户是否有权限。"""
        if self._allowed_users is None:
            return True
        return user_id in self._allowed_users

    async def _on_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if update.effective_user and not self._check_auth(update.effective_user.id):
            return
        await self._reply(
            update,
            "Mini Claw ready.\n\n"
            "Send any message to start.\n"
            "/reset — clear conversation\n"
            "/status — session info",
        )

    async def _on_reset(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.effective_user:
            return
        if not self._check_auth(update.effective_user.id):
            return
        user_id = str(update.effective_user.id)
        # Brain: 通过 hands manager 重置
        if hasattr(self._handler, "_hands"):
            await self._handler._hands.reset_user(user_id)
            await self._reply(update, "Session reset.")
        elif hasattr(self._handler, "reset"):
            had = await self._handler.reset(user_id)
            await self._reply(update, "Session reset." if had else "No active session.")
        else:
            await self._reply(update, "No handler available.")

    async def _on_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.effective_user:
            return
        if not self._check_auth(update.effective_user.id):
            return
        # Brain: 发送 /status 命令给认知循环
        if hasattr(self._handler, "process"):
            msg = UnifiedMessage(
                platform="telegram",
                user_id=str(update.effective_user.id),
                chat_id=str(update.effective_chat.id) if update.effective_chat else "",
                content="/status",
            )
            response = await self._handler.process(msg)
            await self._reply(update, response.text)
        elif hasattr(self._handler, "get_session_info"):
            user_id = str(update.effective_user.id)
            info = self._handler.get_session_info(user_id)
            if info is None:
                await self._reply(update, "No active session.")
            else:
                await self._reply(
                    update,
                    f"Turn count: {info['turn_count']}\n"
                    f"Active sessions: {self._handler.active_count}",
                )
        else:
            await self._reply(update, "Status unavailable.")

    async def _keep_typing(self, chat: Any) -> None:
        """持续发送 typing 指示器，直到被取消。"""
        try:
            while True:
                await chat.send_action(ChatAction.TYPING)
                await asyncio.sleep(TYPING_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.effective_user or not update.message or not update.message.text:
            return
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        user_id = str(update.effective_user.id)
        text = update.message.text

        # 持续 typing 指示器（每 4 秒发一次，处理完成后取消）
        typing_task = None
        if update.effective_chat:
            typing_task = asyncio.create_task(self._keep_typing(update.effective_chat))

        try:
            # Brain CognitiveLoop — /btw 直接透传
            if hasattr(self._handler, "process"):
                msg = UnifiedMessage(
                    platform="telegram",
                    user_id=user_id,
                    chat_id=str(update.effective_chat.id) if update.effective_chat else "",
                    content=text,
                    message_id=str(update.message.message_id),
                )
                response = await self._handler.process(msg)
                result = response.text
            # 旧版 SessionManager
            elif hasattr(self._handler, "get_or_create"):
                session = await self._handler.get_or_create(user_id)
                result = await session.handle(text)
            else:
                result = "（无可用的消息处理器）"
        except Exception as e:
            logger.exception("Engine error: user_id=%s", user_id)
            result = f"Error: {e}"
        finally:
            if typing_task:
                typing_task.cancel()

        # 过滤 [interrupted] 响应（/btw 取消产生的）
        if result == "[interrupted]":
            return

        # 分段发送
        for chunk in _split_message(result):
            await self._reply(update, chunk)

    @staticmethod
    async def _reply(update: Update, text: str) -> None:
        """安全回复消息。"""
        if update.message:
            await update.message.reply_text(text)


def _split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """将长文本按 max_len 分段，优先在换行处断开。"""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # 尝试在换行处断开
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
