"""Permission classification and rule enforcement.

Reference: src/hooks/useCanUseTool.tsx, src/types/permissions.ts
"""

import fnmatch
import logging
import os
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from ..tools.base import BaseTool, PermissionCategory

logger = logging.getLogger(__name__)


class PermissionDecision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PermissionRule:
    """A fine-grained permission rule.

    Matching priority: higher priority number wins.
    All non-empty criteria must match for the rule to apply.
    """
    tool: str = "*"                # Tool name or "*" for any
    category: str = "*"            # read/write/destructive/external or "*"
    mode: str = "ask"              # auto_approve / ask / auto_deny
    path_patterns: List[str] = field(default_factory=list)
    command_patterns: List[str] = field(default_factory=list)
    source: str = "default"        # default / user / project / local
    priority: int = 0              # Higher wins


# Default rules (lowest priority)
_DEFAULT_RULES = [
    PermissionRule(category="read", mode="auto_approve", source="default", priority=0),
    PermissionRule(category="write", mode="ask", source="default", priority=0),
    PermissionRule(category="destructive", mode="ask", source="default", priority=0),
    PermissionRule(category="external", mode="ask", source="default", priority=0),
]


class PermissionManager:
    """Manages tool execution permissions with multi-level rules.

    Modes:
    - "ask" / "default": Prompt user for non-read actions
    - "auto" / "bypass": Auto-approve everything
    - "restricted": Deny destructive/external by default
    - "plan": Only allow read operations
    """

    def __init__(
        self,
        mode: str = "ask",
        rules: Optional[List[Dict]] = None,
        working_dir: Optional[str] = None,
    ):
        self.mode = mode
        self.rules: List[PermissionRule] = list(_DEFAULT_RULES)
        self._session_overrides: Dict[str, PermissionDecision] = {}

        # Load user-provided rules
        if rules:
            for r in rules:
                self.rules.append(PermissionRule(
                    tool=r.get("tool", "*"),
                    category=r.get("category", "*"),
                    mode=r.get("mode", "ask"),
                    path_patterns=r.get("path_patterns", r.get("patterns", [])),
                    command_patterns=r.get("command_patterns", []),
                    source=r.get("source", "config"),
                    priority=r.get("priority", 10),
                ))

        # Load user-level rules from ~/.config/mini_claude/permissions.yaml
        self._load_user_rules()

        # Sort by priority descending
        self.rules.sort(key=lambda r: r.priority, reverse=True)

    def check(self, tool: BaseTool, params: dict) -> PermissionDecision:
        """Check if tool execution is allowed."""
        # Session overrides have highest priority
        override_key = tool.name.lower()
        if override_key in self._session_overrides:
            return self._session_overrides[override_key]

        # Mode-level short circuits
        if self.mode in ("auto", "bypass"):
            return PermissionDecision.ALLOW
        if self.mode == "plan":
            if tool.permission_category == PermissionCategory.READ:
                return PermissionDecision.ALLOW
            return PermissionDecision.DENY
        if self.mode == "restricted" and tool.permission_category in (
            PermissionCategory.DESTRUCTIVE, PermissionCategory.EXTERNAL
        ):
            return PermissionDecision.DENY

        # Rule-based check (highest priority first)
        for rule in self.rules:
            if self._rule_matches(rule, tool, params):
                return self._mode_to_decision(rule.mode)

        # Fallback
        if tool.permission_category == PermissionCategory.READ:
            return PermissionDecision.ALLOW
        return PermissionDecision.ASK

    def set_session_override(self, tool_name: str, decision: PermissionDecision) -> None:
        """Set a session-level override (e.g., 'always allow Bash')."""
        self._session_overrides[tool_name.lower()] = decision

    def _rule_matches(self, rule: PermissionRule, tool: BaseTool, params: dict) -> bool:
        """Check if a rule matches the given tool and params."""
        # Tool name match
        if rule.tool != "*" and rule.tool.lower() != tool.name.lower():
            return False

        # Category match
        if rule.category != "*" and rule.category != tool.permission_category:
            return False

        # Path pattern match (if rule has path patterns)
        if rule.path_patterns:
            file_path = params.get("file_path", "") or params.get("path", "")
            if not file_path or not any(
                fnmatch.fnmatch(file_path, p) for p in rule.path_patterns
            ):
                return False

        # Command pattern match (if rule has command patterns)
        if rule.command_patterns:
            command = params.get("command", "")
            if not command or not any(
                fnmatch.fnmatch(command, p) for p in rule.command_patterns
            ):
                return False

        return True

    def _load_user_rules(self) -> None:
        """Load permission rules from ~/.config/mini_claude/permissions.yaml."""
        config_dir = os.path.expanduser("~/.config/mini_claude")
        perms_file = os.path.join(config_dir, "permissions.yaml")
        if not os.path.exists(perms_file):
            return

        try:
            import yaml
            with open(perms_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            for r in data.get("rules", []):
                self.rules.append(PermissionRule(
                    tool=r.get("tool", "*"),
                    category=r.get("category", "*"),
                    mode=r.get("mode", "ask"),
                    path_patterns=r.get("path_patterns", []),
                    command_patterns=r.get("command_patterns", []),
                    source="user",
                    priority=r.get("priority", 20),
                ))
            logger.info("Loaded %d user permission rules from %s",
                        len(data.get("rules", [])), perms_file)
        except Exception as e:
            logger.warning("Failed to load permission rules: %s", e)

    @staticmethod
    def load_rules_from_file(path: str) -> List[PermissionRule]:
        """Load permission rules from a YAML file."""
        rules = []
        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for r in data.get("rules", []):
                rules.append(PermissionRule(
                    tool=r.get("tool", "*"),
                    category=r.get("category", "*"),
                    mode=r.get("mode", "ask"),
                    path_patterns=r.get("path_patterns", []),
                    command_patterns=r.get("command_patterns", []),
                    source=r.get("source", "file"),
                    priority=r.get("priority", 10),
                ))
        except Exception as e:
            logger.warning("Failed to load rules from %s: %s", path, e)
        return rules

    @staticmethod
    def _mode_to_decision(mode: str) -> PermissionDecision:
        mapping = {
            "auto_approve": PermissionDecision.ALLOW,
            "allow": PermissionDecision.ALLOW,
            "ask": PermissionDecision.ASK,
            "auto_deny": PermissionDecision.DENY,
            "deny": PermissionDecision.DENY,
        }
        return mapping.get(mode, PermissionDecision.ASK)
