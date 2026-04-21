"""BaseAdapter — IM 适配器抽象基类。"""

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Optional

from .models import BotResponse, UnifiedMessage


class BaseAdapter(ABC):
    """所有 IM 适配器的基类。

    子类需实现 start/stop/send，并在收到消息时调用 self._dispatch(msg)。

    v0.2: 不再直接依赖 SessionManager，改为接受通用消息处理回调。
    兼容旧 API：仍可传入 SessionManager（鸭子类型）。
    """

    def __init__(
        self,
        handler: Any = None,
    ) -> None:
        # handler 可以是 Brain CognitiveLoop（有 .process 方法）
        # 或旧的 SessionManager（有 .get_or_create 方法）
        self._handler = handler
        self._on_message: Optional[Callable[[UnifiedMessage], Awaitable[BotResponse]]] = None

    @abstractmethod
    async def start(self) -> None:
        """启动适配器（连接/轮询/webhook 监听）。"""

    @abstractmethod
    async def stop(self) -> None:
        """优雅关闭。"""

    @abstractmethod
    async def send(self, chat_id: str, response: BotResponse) -> None:
        """发送消息到 IM 平台。"""

    @property
    def platform(self) -> str:
        """平台标识符。"""
        return self.__class__.__name__.replace("Adapter", "").lower()

    def on_message(self, callback: Callable[[UnifiedMessage], Awaitable[BotResponse]]) -> None:
        """注册外部消息回调（可用于中间件链）。"""
        self._on_message = callback

    async def _dispatch(self, msg: UnifiedMessage) -> str:
        """处理收到的消息。

        优先级：
        1. on_message 回调（中间件链）
        2. handler.process()（Brain CognitiveLoop）
        3. handler.get_or_create()（旧 SessionManager 兼容）
        """
        if self._on_message:
            response = await self._on_message(msg)
            return response.text

        if self._handler is not None:
            # Brain CognitiveLoop 有 .process(msg) -> BotResponse
            if hasattr(self._handler, "process"):
                response = await self._handler.process(msg)
                return response.text

            # 旧版 SessionManager 兼容
            if hasattr(self._handler, "get_or_create"):
                session = await self._handler.get_or_create(msg.user_id)
                return await session.handle(msg.content)

        return "（无可用的消息处理器）"
