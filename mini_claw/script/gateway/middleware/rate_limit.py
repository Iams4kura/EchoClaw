"""限流中间件 — 令牌桶算法，按用户限制请求频率。"""

import logging
import time
from typing import Dict, Optional

from ..models import UnifiedMessage

logger = logging.getLogger(__name__)


class TokenBucket:
    """令牌桶限流器。"""

    def __init__(self, capacity: float, refill_rate: float) -> None:
        """
        Args:
            capacity: 桶容量（最大突发请求数）
            refill_rate: 每秒补充的令牌数
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = capacity
        self._last_refill = time.time()

    def consume(self, tokens: float = 1.0) -> bool:
        """尝试消耗令牌。返回 True 表示允许，False 表示限流。"""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    def _refill(self) -> None:
        now = time.time()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now


class RateLimiter:
    """按用户限流。

    默认：每用户每分钟 20 条消息（桶容量 5，每秒补充 0.33）。
    """

    def __init__(
        self,
        capacity: float = 5.0,
        refill_rate: float = 0.33,
        disabled: bool = False,
    ) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._disabled = disabled
        self._buckets: Dict[str, TokenBucket] = {}

    def _get_bucket(self, user_id: str) -> TokenBucket:
        if user_id not in self._buckets:
            self._buckets[user_id] = TokenBucket(self._capacity, self._refill_rate)
        return self._buckets[user_id]

    async def check(self, msg: UnifiedMessage) -> Optional[str]:
        """限流检查。返回 None 表示通过，否则返回拒绝理由。"""
        if self._disabled:
            return None
        bucket = self._get_bucket(msg.user_id)
        if not bucket.consume():
            logger.warning("Rate limited: user=%s platform=%s", msg.user_id, msg.platform)
            return "Too many requests. Please slow down."
        return None

    def cleanup(self, max_idle: float = 3600.0) -> int:
        """清理长时间未使用的桶。"""
        now = time.time()
        to_remove = [
            uid for uid, bucket in self._buckets.items()
            if now - bucket._last_refill > max_idle
        ]
        for uid in to_remove:
            del self._buckets[uid]
        return len(to_remove)
