"""消息日志中间件 — 记录所有进出消息。"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from ..models import UnifiedMessage

logger = logging.getLogger(__name__)


class MessageLogger:
    """消息日志记录器。

    记录格式：JSONL（每行一条日志）。
    """

    def __init__(self, log_dir: Optional[str] = None) -> None:
        self._log_dir = Path(log_dir) if log_dir else None
        self._count = 0

    async def log_incoming(self, msg: UnifiedMessage) -> None:
        """记录收到的消息。"""
        self._count += 1
        record = {
            "direction": "incoming",
            "platform": msg.platform,
            "user_id": msg.user_id,
            "chat_id": msg.chat_id,
            "content_length": len(msg.content),
            "timestamp": msg.timestamp,
            "message_id": msg.message_id,
        }
        logger.info(
            "MSG IN [%s] user=%s len=%d",
            msg.platform, msg.user_id, len(msg.content),
        )
        self._write(record)

    async def log_outgoing(
        self,
        platform: str,
        user_id: str,
        response_length: int,
        duration_ms: float,
    ) -> None:
        """记录发出的响应。"""
        record = {
            "direction": "outgoing",
            "platform": platform,
            "user_id": user_id,
            "response_length": response_length,
            "duration_ms": round(duration_ms, 1),
            "timestamp": time.time(),
        }
        logger.info(
            "MSG OUT [%s] user=%s len=%d %.0fms",
            platform, user_id, response_length, duration_ms,
        )
        self._write(record)

    @property
    def total_messages(self) -> int:
        return self._count

    def _write(self, record: dict) -> None:
        """写入 JSONL 日志文件（如果配置了目录）。"""
        if self._log_dir is None:
            return

        self._log_dir.mkdir(parents=True, exist_ok=True)
        # 按日期分文件
        date_str = time.strftime("%Y-%m-%d")
        log_file = self._log_dir / f"messages_{date_str}.jsonl"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
