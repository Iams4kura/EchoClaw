"""企业微信机器人适配器 — 通过回调 URL 接收消息。

企微机器人使用回调模式：
1. 配置消息接收 URL
2. 企微推送消息到回调 URL（XML 格式）
3. 适配器解析、处理、回复

依赖：httpx（发送消息）、fastapi（接收回调）
"""

import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Response

from ..base_adapter import BaseAdapter
from ..models import BotResponse, UnifiedMessage

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2048  # 企微单条消息限制


class WecomAdapter(BaseAdapter):
    """企业微信机器人适配器。

    使用回调 URL 接收消息，通过 Webhook 或 API 回复。
    配置项：corp_id, corp_secret, token, encoding_aes_key, agent_id。
    """

    def __init__(
        self,
        handler: Any = None,
        corp_id: str = "",
        corp_secret: str = "",
        agent_id: str = "",
        callback_token: str = "",
        encoding_aes_key: str = "",
        webhook_url: str = "",
    ) -> None:
        super().__init__(handler)
        self._corp_id = corp_id
        self._corp_secret = corp_secret
        self._agent_id = agent_id
        self._callback_token = callback_token
        self._encoding_aes_key = encoding_aes_key
        self._webhook_url = webhook_url  # 简单模式：群机器人 Webhook
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    @property
    def platform(self) -> str:
        return "wecom"

    def register_routes(self, app: FastAPI, path: str = "/wecom/callback") -> None:
        """在 FastAPI 应用上注册企微回调路由。"""

        @app.get(path)
        async def wecom_verify(
            msg_signature: str = "",
            timestamp: str = "",
            nonce: str = "",
            echostr: str = "",
        ) -> Response:
            """企微 URL 验证（GET 请求）。"""
            # 简化实现：直接返回 echostr（完整实现需要解密验证）
            return Response(content=echostr, media_type="text/plain")

        @app.post(path)
        async def wecom_callback(request: Request) -> Response:
            """接收消息回调（POST 请求）。"""
            body = await request.body()
            await self._handle_callback(body.decode("utf-8"))
            return Response(content="success", media_type="text/plain")

    async def start(self) -> None:
        """启动适配器。"""
        logger.info("Wecom adapter ready (callback mode)")

    async def stop(self) -> None:
        """关闭适配器。"""
        logger.info("Wecom adapter stopped")

    async def send(self, chat_id: str, response: BotResponse) -> None:
        """发送消息。

        优先使用 Webhook URL（群机器人），否则使用应用消息 API。
        """
        for chunk in _split_message(response.text):
            if self._webhook_url:
                await self._send_webhook(chunk)
            else:
                await self._send_app_message(chat_id, chunk)

    async def _send_webhook(self, text: str) -> None:
        """通过 Webhook URL 发送（群机器人模式）。"""
        try:
            import httpx
            payload = {
                "msgtype": "text",
                "text": {"content": text},
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._webhook_url, json=payload, timeout=10.0
                )
                if resp.status_code != 200:
                    logger.warning("Wecom webhook send failed: %s", resp.text)
        except Exception as e:
            logger.error("Wecom webhook error: %s", e)

    async def _send_app_message(self, user_id: str, text: str) -> None:
        """通过应用消息 API 发送。"""
        token = await self._get_access_token()
        if not token:
            logger.error("Failed to get wecom access token")
            return

        try:
            import httpx
            payload = {
                "touser": user_id,
                "msgtype": "text",
                "agentid": int(self._agent_id) if self._agent_id else 0,
                "text": {"content": text},
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://qyapi.weixin.qq.com/cgi-bin/message/send",
                    params={"access_token": token},
                    json=payload,
                    timeout=10.0,
                )
                data = resp.json()
                if data.get("errcode", 0) != 0:
                    logger.warning("Wecom send failed: %s", data)
        except Exception as e:
            logger.error("Wecom send error: %s", e)

    async def _handle_callback(self, xml_str: str) -> None:
        """处理企微 XML 回调。"""
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            logger.warning("Invalid XML in wecom callback")
            return

        msg_type = root.findtext("MsgType", "")
        if msg_type != "text":
            return

        content = root.findtext("Content", "")
        from_user = root.findtext("FromUserName", "unknown")
        to_user = root.findtext("ToUserName", "")

        if not content:
            return

        msg = UnifiedMessage(
            platform="wecom",
            user_id=from_user,
            chat_id=from_user,
            content=content,
        )

        result = await self._dispatch(msg)
        await self.send(from_user, BotResponse(text=result))

    async def _get_access_token(self) -> Optional[str]:
        """获取企微 access_token（2小时有效）。"""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        if not self._corp_id or not self._corp_secret:
            return None

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                    params={"corpid": self._corp_id, "corpsecret": self._corp_secret},
                    timeout=10.0,
                )
                data = resp.json()
                if data.get("errcode", 0) == 0:
                    self._access_token = data["access_token"]
                    self._token_expires_at = time.time() + data.get("expires_in", 7200) - 300
                    return self._access_token
        except Exception as e:
            logger.error("Failed to get wecom token: %s", e)

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
