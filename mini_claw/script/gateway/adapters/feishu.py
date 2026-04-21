"""飞书机器人适配器 — 通过 Webhook 接收事件回调。

飞书机器人使用事件订阅模式：
1. 配置事件回调 URL（需 HTTPS）
2. 飞书推送消息事件到回调 URL
3. 适配器处理后回复

依赖：httpx（发送消息）、fastapi（接收回调）
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Response

from ..base_adapter import BaseAdapter
from ..models import BotResponse, UnifiedMessage

logger = logging.getLogger(__name__)

# 飞书消息最大长度
MAX_MESSAGE_LENGTH = 4000


class FeishuAdapter(BaseAdapter):
    """飞书机器人适配器。

    使用事件订阅模式接收消息，通过 API 发送回复。
    需要配置：app_id, app_secret, verification_token/encrypt_key。
    """

    def __init__(
        self,
        handler: Any = None,
        app_id: str = "",
        app_secret: str = "",
        verification_token: str = "",
        encrypt_key: str = "",
    ) -> None:
        super().__init__(handler)
        self._app_id = app_id
        self._app_secret = app_secret
        self._verification_token = verification_token
        self._encrypt_key = encrypt_key
        self._tenant_access_token: Optional[str] = None
        self._token_expires_at: float = 0
        # 事件去重
        self._processed_events: Dict[str, float] = {}

    @property
    def platform(self) -> str:
        return "feishu"

    def register_routes(self, app: FastAPI, path: str = "/feishu/event") -> None:
        """在 FastAPI 应用上注册飞书回调路由。"""

        @app.post(path)
        async def feishu_event(request: Request) -> Response:
            body = await request.json()

            # URL 验证（飞书初次配置回调时发送）
            if "challenge" in body:
                return Response(
                    content=json.dumps({"challenge": body["challenge"]}),
                    media_type="application/json",
                )

            # 处理事件
            await self._handle_event(body)
            return Response(content="{}", media_type="application/json")

    async def start(self) -> None:
        """启动适配器（飞书使用被动回调，无需主动连接）。"""
        logger.info("Feishu adapter ready (event subscription mode)")

    async def stop(self) -> None:
        """关闭适配器。"""
        logger.info("Feishu adapter stopped")

    async def send(self, chat_id: str, response: BotResponse) -> None:
        """通过飞书 API 发送消息。"""
        try:
            import httpx
        except ImportError:
            logger.error("httpx not installed, cannot send feishu message")
            return

        token = await self._get_tenant_token()
        if not token:
            logger.error("Failed to get feishu tenant access token")
            return

        # 分段发送
        for chunk in _split_message(response.text):
            payload = {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": chunk}),
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.warning("Feishu send failed: %s", resp.text)

    async def _handle_event(self, body: Dict[str, Any]) -> None:
        """处理飞书事件回调。"""
        # 验证 token
        if self._verification_token:
            token = body.get("token", "")
            if token != self._verification_token:
                logger.warning("Invalid verification token")
                return

        header = body.get("header", {})
        event = body.get("event", {})

        # 事件去重
        event_id = header.get("event_id", "")
        if event_id in self._processed_events:
            return
        self._processed_events[event_id] = time.time()
        # 定期清理（保留最近 1000 条）
        if len(self._processed_events) > 1000:
            sorted_events = sorted(self._processed_events.items(), key=lambda x: x[1])
            self._processed_events = dict(sorted_events[-500:])

        event_type = header.get("event_type", "")

        if event_type == "im.message.receive_v1":
            await self._handle_message(event)

    async def _handle_message(self, event: Dict[str, Any]) -> None:
        """处理收到的消息事件。"""
        message = event.get("message", {})
        sender = event.get("sender", {})

        msg_type = message.get("message_type", "")
        if msg_type != "text":
            return  # P3 只处理纯文本

        # 解析消息内容
        content_str = message.get("content", "{}")
        try:
            content = json.loads(content_str)
            text = content.get("text", "")
        except json.JSONDecodeError:
            text = content_str

        if not text:
            return

        chat_id = message.get("chat_id", "")
        user_id = sender.get("sender_id", {}).get("user_id", "unknown")

        msg = UnifiedMessage(
            platform="feishu",
            user_id=user_id,
            chat_id=chat_id,
            content=text,
            message_id=message.get("message_id", ""),
        )

        result = await self._dispatch(msg)
        await self.send(chat_id, BotResponse(text=result))

    async def _get_tenant_token(self) -> Optional[str]:
        """获取飞书 tenant_access_token（2小时有效）。"""
        if self._tenant_access_token and time.time() < self._token_expires_at:
            return self._tenant_access_token

        if not self._app_id or not self._app_secret:
            return None

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self._app_id, "app_secret": self._app_secret},
                    timeout=10.0,
                )
                data = resp.json()
                if data.get("code") == 0:
                    self._tenant_access_token = data["tenant_access_token"]
                    self._token_expires_at = time.time() + data.get("expire", 7200) - 300
                    return self._tenant_access_token
        except Exception as e:
            logger.error("Failed to get tenant token: %s", e)

        return None


def _split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list:
    """分段发送长消息。"""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
