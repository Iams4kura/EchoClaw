"""用户鉴权中间件 — 多级权限控制。"""

import logging
from enum import Enum
from typing import Any, Dict, Optional, Set

from ..models import UnifiedMessage

logger = logging.getLogger(__name__)


class UserRole(str, Enum):
    ADMIN = "admin"       # 管理员 — 全部权限
    USER = "user"         # 普通用户 — 对话 + 基本工具
    READONLY = "readonly" # 只读 — 只能查看状态，不能对话


class AuthManager:
    """用户鉴权管理。

    权限规则：
    1. 如果 allowed_users 为空 → 允许所有人（默认 USER 角色）
    2. 如果 allowed_users 非空 → 只有列表中的用户可访问
    3. admin_users 中的用户拥有 ADMIN 角色
    """

    def __init__(
        self,
        allowed_users: Optional[Set[str]] = None,
        admin_users: Optional[Set[str]] = None,
        default_role: UserRole = UserRole.USER,
        personal_mode: bool = False,
        owner_id: str = "",
    ) -> None:
        self._allowed = allowed_users  # None = 允许所有人
        self._admins = admin_users or set()
        self._default_role = default_role
        self._personal_mode = personal_mode
        self._owner_id = owner_id
        # 用户角色覆盖
        self._role_overrides: Dict[str, UserRole] = {}

    def is_allowed(self, user_id: str) -> bool:
        """检查用户是否有访问权限。"""
        if self._personal_mode:
            return not self._owner_id or user_id == self._owner_id
        if self._allowed is None:
            return True
        return user_id in self._allowed or user_id in self._admins

    def get_role(self, user_id: str) -> UserRole:
        """获取用户角色。"""
        if self._personal_mode and self.is_allowed(user_id):
            return UserRole.ADMIN
        if user_id in self._role_overrides:
            return self._role_overrides[user_id]
        if user_id in self._admins:
            return UserRole.ADMIN
        return self._default_role

    def set_role(self, user_id: str, role: UserRole) -> None:
        """设置用户角色。"""
        self._role_overrides[user_id] = role

    def check_permission(self, user_id: str, action: str) -> bool:
        """检查用户是否有执行特定操作的权限。

        Args:
            user_id: 用户 ID
            action: 操作类型 ("chat", "reset", "admin", "view_status")
        """
        if not self.is_allowed(user_id):
            return False

        role = self.get_role(user_id)

        if role == UserRole.ADMIN:
            return True
        if role == UserRole.USER:
            return action in ("chat", "reset", "view_status")
        if role == UserRole.READONLY:
            return action in ("view_status",)

        return False

    async def authorize(self, msg: UnifiedMessage) -> Optional[str]:
        """鉴权中间件入口。返回 None 表示通过，否则返回拒绝理由。"""
        if not self.is_allowed(msg.user_id):
            if self._personal_mode:
                logger.warning(
                    "Personal mode: rejected user=%s platform=%s",
                    msg.user_id, msg.platform,
                )
                return "This is a personal assistant. Access denied."
            logger.warning(
                "Unauthorized access: user=%s platform=%s",
                msg.user_id, msg.platform,
            )
            return "Not authorized."

        role = self.get_role(msg.user_id)
        if role == UserRole.READONLY:
            return "Read-only access. You cannot send messages."

        return None
