"""P3 测试 — BaseAdapter + 中间件 + 飞书/企微适配器。"""

import asyncio
import json
import time
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from script.gateway.base_adapter import BaseAdapter
from script.gateway.models import BotResponse, UnifiedMessage
from script.gateway.middleware.auth import AuthManager, UserRole
from script.gateway.middleware.rate_limit import RateLimiter, TokenBucket
from script.gateway.middleware.logging_mw import MessageLogger
from script.gateway.adapters.feishu import FeishuAdapter, _split_message as feishu_split
from script.gateway.adapters.wecom import WecomAdapter, _split_message as wecom_split


# ─── BaseAdapter ─────────────────────────────────────────────

class DummyAdapter(BaseAdapter):
    """用于测试的具体适配器实现。"""

    def __init__(self, session_manager):
        super().__init__(session_manager)
        self.sent: list = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, chat_id: str, response: BotResponse) -> None:
        self.sent.append((chat_id, response.text))


class TestBaseAdapter:
    def test_platform_name(self):
        mgr = MagicMock()
        adapter = DummyAdapter(mgr)
        assert adapter.platform == "dummy"

    @pytest.mark.asyncio
    async def test_dispatch_calls_session_manager(self):
        mgr = MagicMock()
        session = AsyncMock()
        session.handle = AsyncMock(return_value="hello")
        mgr.get_or_create = AsyncMock(return_value=session)

        adapter = DummyAdapter(mgr)
        msg = UnifiedMessage(platform="test", user_id="u1", chat_id="c1", content="hi")
        result = await adapter._dispatch(msg)
        assert result == "hello"
        mgr.get_or_create.assert_called_once_with("u1")

    @pytest.mark.asyncio
    async def test_dispatch_with_callback(self):
        mgr = MagicMock()
        adapter = DummyAdapter(mgr)

        callback = AsyncMock(return_value=BotResponse(text="from callback"))
        adapter.on_message(callback)

        msg = UnifiedMessage(platform="test", user_id="u1", chat_id="c1", content="hi")
        result = await adapter._dispatch(msg)
        assert result == "from callback"
        callback.assert_called_once_with(msg)


# ─── AuthManager ─────────────────────────────────────────────

class TestAuthManager:
    def test_allow_all_by_default(self):
        auth = AuthManager()
        assert auth.is_allowed("anyone") is True

    def test_whitelist(self):
        auth = AuthManager(allowed_users={"alice", "bob"})
        assert auth.is_allowed("alice") is True
        assert auth.is_allowed("eve") is False

    def test_admin_always_allowed(self):
        auth = AuthManager(allowed_users={"alice"}, admin_users={"admin1"})
        assert auth.is_allowed("admin1") is True
        assert auth.get_role("admin1") == UserRole.ADMIN

    def test_role_override(self):
        auth = AuthManager()
        auth.set_role("u1", UserRole.READONLY)
        assert auth.get_role("u1") == UserRole.READONLY

    def test_permissions(self):
        auth = AuthManager(admin_users={"admin1"})
        assert auth.check_permission("admin1", "admin") is True
        assert auth.check_permission("user1", "chat") is True
        assert auth.check_permission("user1", "admin") is False

    def test_readonly_cannot_chat(self):
        auth = AuthManager()
        auth.set_role("ro", UserRole.READONLY)
        assert auth.check_permission("ro", "chat") is False
        assert auth.check_permission("ro", "view_status") is True

    @pytest.mark.asyncio
    async def test_authorize_pass(self):
        auth = AuthManager()
        msg = UnifiedMessage(platform="test", user_id="u1", chat_id="c1", content="hi")
        assert await auth.authorize(msg) is None

    @pytest.mark.asyncio
    async def test_authorize_reject_not_allowed(self):
        auth = AuthManager(allowed_users={"alice"})
        msg = UnifiedMessage(platform="test", user_id="eve", chat_id="c1", content="hi")
        result = await auth.authorize(msg)
        assert result is not None
        assert "Not authorized" in result

    @pytest.mark.asyncio
    async def test_authorize_reject_readonly(self):
        auth = AuthManager()
        auth.set_role("ro", UserRole.READONLY)
        msg = UnifiedMessage(platform="test", user_id="ro", chat_id="c1", content="hi")
        result = await auth.authorize(msg)
        assert "Read-only" in result


# ─── TokenBucket + RateLimiter ───────────────────────────────

class TestTokenBucket:
    def test_basic_consume(self):
        bucket = TokenBucket(capacity=3, refill_rate=1.0)
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is False  # 桶空

    def test_refill(self):
        bucket = TokenBucket(capacity=2, refill_rate=100.0)  # 快速补充
        bucket.consume()
        bucket.consume()
        assert bucket.consume() is False
        # 模拟时间流逝
        bucket._last_refill -= 1.0
        assert bucket.consume() is True

    def test_tokens_property(self):
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        assert bucket.tokens == 5.0


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_pass(self):
        limiter = RateLimiter(capacity=5, refill_rate=1.0)
        msg = UnifiedMessage(platform="test", user_id="u1", chat_id="c1", content="hi")
        assert await limiter.check(msg) is None

    @pytest.mark.asyncio
    async def test_rate_limited(self):
        limiter = RateLimiter(capacity=2, refill_rate=0.001)  # 极慢补充
        msg = UnifiedMessage(platform="test", user_id="u1", chat_id="c1", content="hi")
        assert await limiter.check(msg) is None
        assert await limiter.check(msg) is None
        result = await limiter.check(msg)
        assert result is not None
        assert "slow down" in result.lower()

    def test_cleanup(self):
        limiter = RateLimiter()
        # 添加一个旧桶
        bucket = TokenBucket(5, 0.33)
        bucket._last_refill = time.time() - 7200
        limiter._buckets["old_user"] = bucket
        removed = limiter.cleanup(max_idle=3600)
        assert removed == 1
        assert "old_user" not in limiter._buckets


# ─── MessageLogger ───────────────────────────────────────────

class TestMessageLogger:
    @pytest.mark.asyncio
    async def test_log_incoming(self):
        ml = MessageLogger()  # 无文件输出
        msg = UnifiedMessage(platform="test", user_id="u1", chat_id="c1", content="hello")
        await ml.log_incoming(msg)
        assert ml.total_messages == 1

    @pytest.mark.asyncio
    async def test_log_outgoing(self):
        ml = MessageLogger()
        await ml.log_outgoing("test", "u1", 100, 50.0)
        # 不报错即可

    @pytest.mark.asyncio
    async def test_log_to_file(self, tmp_path):
        ml = MessageLogger(log_dir=str(tmp_path))
        msg = UnifiedMessage(platform="test", user_id="u1", chat_id="c1", content="hello")
        await ml.log_incoming(msg)
        # 检查文件
        files = list(tmp_path.glob("messages_*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text()
        record = json.loads(content.strip())
        assert record["direction"] == "incoming"
        assert record["platform"] == "test"


# ─── FeishuAdapter ───────────────────────────────────────────

class TestFeishuAdapter:
    def _make_adapter(self):
        mgr = MagicMock()
        session = AsyncMock()
        session.handle = AsyncMock(return_value="pong")
        mgr.get_or_create = AsyncMock(return_value=session)
        return FeishuAdapter(
            session_manager=mgr,
            app_id="test_id",
            app_secret="test_secret",
            verification_token="test_token",
        ), mgr

    def test_platform(self):
        adapter, _ = self._make_adapter()
        assert adapter.platform == "feishu"

    @pytest.mark.asyncio
    async def test_handle_challenge(self):
        """飞书 URL 验证回调。"""
        adapter, _ = self._make_adapter()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        adapter.register_routes(app)

        client = TestClient(app)
        resp = client.post("/feishu/event", json={"challenge": "abc123"})
        assert resp.status_code == 200
        assert resp.json()["challenge"] == "abc123"

    @pytest.mark.asyncio
    async def test_handle_message_event(self):
        """处理文本消息事件。"""
        adapter, mgr = self._make_adapter()

        event_body = {
            "token": "test_token",
            "header": {
                "event_id": "evt_001",
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "message": {
                    "message_type": "text",
                    "content": json.dumps({"text": "hello"}),
                    "chat_id": "chat_001",
                    "message_id": "msg_001",
                },
                "sender": {
                    "sender_id": {"user_id": "user_001"},
                },
            },
        }

        # 直接调用内部方法测试（避免需要 httpx mock）
        await adapter._handle_event(event_body)
        mgr.get_or_create.assert_called_once_with("user_001")

    @pytest.mark.asyncio
    async def test_event_dedup(self):
        """同一事件 ID 不重复处理。"""
        adapter, mgr = self._make_adapter()
        event_body = {
            "token": "test_token",
            "header": {"event_id": "evt_dup", "event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "message_type": "text",
                    "content": json.dumps({"text": "hello"}),
                    "chat_id": "c1",
                    "message_id": "m1",
                },
                "sender": {"sender_id": {"user_id": "u1"}},
            },
        }
        await adapter._handle_event(event_body)
        await adapter._handle_event(event_body)  # 重复
        assert mgr.get_or_create.call_count == 1

    @pytest.mark.asyncio
    async def test_ignore_non_text(self):
        """忽略非文本消息。"""
        adapter, mgr = self._make_adapter()
        event_body = {
            "token": "test_token",
            "header": {"event_id": "evt_img", "event_type": "im.message.receive_v1"},
            "event": {
                "message": {"message_type": "image", "chat_id": "c1"},
                "sender": {"sender_id": {"user_id": "u1"}},
            },
        }
        await adapter._handle_event(event_body)
        mgr.get_or_create.assert_not_called()

    def test_split_message(self):
        short = "hello"
        assert feishu_split(short) == ["hello"]
        long_text = "a" * 5000
        chunks = feishu_split(long_text)
        assert len(chunks) == 2
        assert "".join(chunks) == long_text


# ─── WecomAdapter ────────────────────────────────────────────

class TestWecomAdapter:
    def _make_adapter(self):
        mgr = MagicMock()
        session = AsyncMock()
        session.handle = AsyncMock(return_value="pong")
        mgr.get_or_create = AsyncMock(return_value=session)
        return WecomAdapter(
            session_manager=mgr,
            corp_id="test_corp",
            corp_secret="test_secret",
            webhook_url="https://example.com/webhook",
        ), mgr

    def test_platform(self):
        adapter, _ = self._make_adapter()
        assert adapter.platform == "wecom"

    @pytest.mark.asyncio
    async def test_handle_text_callback(self):
        """处理企微文本消息回调。"""
        adapter, mgr = self._make_adapter()

        xml_str = """<xml>
            <MsgType>text</MsgType>
            <Content>hello</Content>
            <FromUserName>user001</FromUserName>
            <ToUserName>bot001</ToUserName>
        </xml>"""

        # mock send 以避免实际 HTTP 调用
        adapter.send = AsyncMock()
        await adapter._handle_callback(xml_str)
        mgr.get_or_create.assert_called_once_with("user001")

    @pytest.mark.asyncio
    async def test_ignore_non_text(self):
        """忽略非文本消息。"""
        adapter, mgr = self._make_adapter()
        xml_str = """<xml>
            <MsgType>image</MsgType>
            <FromUserName>user001</FromUserName>
        </xml>"""
        await adapter._handle_callback(xml_str)
        mgr.get_or_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_xml(self):
        """无效 XML 不崩溃。"""
        adapter, mgr = self._make_adapter()
        await adapter._handle_callback("not xml at all")
        mgr.get_or_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_route(self):
        """企微 URL 验证（GET 请求）。"""
        adapter, _ = self._make_adapter()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        adapter.register_routes(app)

        client = TestClient(app)
        resp = client.get("/wecom/callback", params={"echostr": "verify123"})
        assert resp.status_code == 200
        assert resp.text == "verify123"

    def test_split_message(self):
        short = "hello"
        assert wecom_split(short) == ["hello"]
        long_text = "a" * 3000
        chunks = wecom_split(long_text)
        assert len(chunks) == 2  # 2048 限制
        assert "".join(chunks) == long_text
