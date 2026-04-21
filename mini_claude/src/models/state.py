"""Application state management."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Literal, Any
from datetime import datetime
import os
import asyncio
from .message import Message


@dataclass
class TaskInfo:
    """Background task information."""
    task_id: str
    type: Literal["bash", "agent"]
    status: Literal["pending", "running", "completed", "failed", "killed"]
    description: str
    start_time: float
    output_file: Optional[str] = None
    output_offset: int = 0
    end_time: Optional[float] = None
    exit_code: Optional[int] = None

    @property
    def duration_ms(self) -> Optional[int]:
        """Calculate task duration."""
        if self.end_time and self.start_time:
            return int((self.end_time - self.start_time) * 1000)
        return None


@dataclass
class AgentInfo:
    """Active agent information."""
    agent_id: str
    name: str
    color: str
    model: str
    status: Literal["idle", "running", "completed", "failed", "killed"]
    parent_tool_use_id: Optional[str] = None
    messages: List[Message] = field(default_factory=list)
    assigned_files: List[str] = field(default_factory=list)
    can_communicate_with: List[str] = field(default_factory=list)
    start_time: float = field(default_factory=lambda: datetime.now().timestamp())
    end_time: Optional[float] = None


@dataclass
class TokenUsage:
    """Detailed token usage tracking with input/output split."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, usage_dict: dict) -> None:
        """Accumulate usage from an API response usage dict."""
        self.input_tokens += usage_dict.get("input_tokens", 0)
        self.output_tokens += usage_dict.get("output_tokens", 0)
        self.cache_read_tokens += usage_dict.get("cache_read_input_tokens", 0)
        self.cache_write_tokens += usage_dict.get("cache_creation_input_tokens", 0)


@dataclass
class AppState:
    """Global application state."""
    # Session
    session_id: str = field(default_factory=lambda: f"mc_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    working_dir: str = field(default_factory=os.getcwd)
    created_at: datetime = field(default_factory=datetime.now)

    # Conversation
    messages: List[Message] = field(default_factory=list)
    system_prompt: str = ""

    # Context tracking
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    compact_boundary_index: int = 0  # Messages before this are compacted

    @property
    def total_tokens(self) -> int:
        """Backward-compatible total token count."""
        return self.token_usage.total

    @total_tokens.setter
    def total_tokens(self, value: int) -> None:
        """Backward-compatible setter — distributes to input_tokens."""
        self.token_usage.input_tokens = value

    # Active operations
    active_tasks: Dict[str, TaskInfo] = field(default_factory=dict)
    active_agents: Dict[str, AgentInfo] = field(default_factory=dict)
    agent_mailbox: Dict[str, asyncio.Queue] = field(default_factory=dict)

    # UI state
    is_streaming: bool = False
    current_tool_use: Optional[str] = None
    pending_tool_uses: List[str] = field(default_factory=list)
    stream_buffer: str = ""

    # Cancellation
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)

    def is_aborted(self) -> bool:
        """Check if session is aborted."""
        return self.abort_event.is_set()

    def abort(self) -> None:
        """Signal session abort."""
        self.abort_event.set()

    def get_active_task_count(self) -> int:
        """Count non-terminal tasks."""
        return sum(
            1 for t in self.active_tasks.values()
            if t.status in ["pending", "running"]
        )

    def get_active_agent_count(self) -> int:
        """Count running agents."""
        return sum(
            1 for a in self.active_agents.values()
            if a.status == "running"
        )

    def to_dict(self) -> dict:
        """Serialize state for persistence."""
        return {
            "session_id": self.session_id,
            "working_dir": self.working_dir,
            "created_at": self.created_at.isoformat(),
            "messages": [msg.to_api_format() for msg in self.messages],
            "active_agents": {
                aid: {
                    "agent_id": a.agent_id,
                    "name": a.name,
                    "color": a.color,
                    "status": a.status,
                } for aid, a in self.active_agents.items()
            },
            "token_usage": {
                "input_tokens": self.token_usage.input_tokens,
                "output_tokens": self.token_usage.output_tokens,
                "cache_read_tokens": self.token_usage.cache_read_tokens,
                "cache_write_tokens": self.token_usage.cache_write_tokens,
            },
            "total_tokens": self.total_tokens,  # backward compat
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppState":
        """Deserialize state from persistence."""
        from .message import TextBlock, ToolUseBlock, ToolResultBlock

        # Restore token usage (supports both old and new format)
        tu_data = data.get("token_usage")
        if tu_data and isinstance(tu_data, dict):
            token_usage = TokenUsage(
                input_tokens=tu_data.get("input_tokens", 0),
                output_tokens=tu_data.get("output_tokens", 0),
                cache_read_tokens=tu_data.get("cache_read_tokens", 0),
                cache_write_tokens=tu_data.get("cache_write_tokens", 0),
            )
        else:
            # Backward compat: old format stored total_tokens as int
            old_total = data.get("total_tokens", 0)
            token_usage = TokenUsage(input_tokens=old_total)

        state = cls(
            session_id=data.get("session_id", ""),
            working_dir=data.get("working_dir", os.getcwd()),
            token_usage=token_usage,
        )

        # Restore messages
        for msg_data in data.get("messages", []):
            role = msg_data.get("role", "user")
            blocks = []
            for block_data in msg_data.get("content", []):
                btype = block_data.get("type")
                if btype == "text":
                    blocks.append(TextBlock(text=block_data.get("text", "")))
                elif btype == "tool_use":
                    blocks.append(ToolUseBlock(
                        id=block_data.get("id", ""),
                        name=block_data.get("name", ""),
                        input=block_data.get("input", {}),
                    ))
                elif btype == "tool_result":
                    blocks.append(ToolResultBlock(
                        tool_use_id=block_data.get("tool_use_id", ""),
                        content=block_data.get("content", ""),
                        is_error=block_data.get("is_error", False),
                    ))
            if blocks:
                state.messages.append(Message(role=role, content=blocks))

        return state
