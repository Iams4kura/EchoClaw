"""统一消息协议 — 所有 IM 平台的消息归一化为内部数据结构。"""

import time
from dataclasses import dataclass, field


@dataclass
class UnifiedMessage:
    """平台无关的输入消息。"""

    platform: str       # "telegram" | "webhook"
    user_id: str        # 平台无关的用户标识
    chat_id: str        # 会话 ID
    content: str        # 纯文本内容
    timestamp: float = field(default_factory=time.time)
    message_id: str = ""                          # 平台消息 ID（用于回复）
    metadata: dict = field(default_factory=dict)  # 平台特有字段透传


@dataclass
class BotResponse:
    """平台无关的输出响应。"""

    text: str
    reply_to: str | None = None  # 回复目标消息 ID
