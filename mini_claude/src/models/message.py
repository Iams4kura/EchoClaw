"""Message types compatible with Anthropic SDK."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional, Union, Any, List


@dataclass(frozen=True)
class TextBlock:
    """Text content block."""
    type: Literal["text"] = "text"
    text: str = ""


@dataclass(frozen=True)
class ToolUseBlock:
    """Tool invocation from assistant."""
    type: Literal["tool_use"] = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ThinkingBlock:
    """Extended thinking content block from Claude."""
    type: Literal["thinking"] = "thinking"
    thinking: str = ""


@dataclass(frozen=True)
class ToolResultBlock:
    """Tool execution result."""
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = ""
    content: Union[str, List[Any]] = ""
    is_error: bool = False


MessageContent = Union[TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock]


@dataclass
class Message:
    """A conversation message."""
    role: Literal["user", "assistant", "system"]
    content: Union[List[MessageContent], str]
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Optional[dict] = None
    # Compression tracking
    original_length: Optional[int] = None
    is_summarized: bool = False

    def __post_init__(self):
        """Normalize string content to list."""
        if isinstance(self.content, str):
            self.content = [TextBlock(text=self.content)]

    def to_api_format(self) -> dict:
        """Convert to Anthropic API format."""
        content_list = []
        for block in self.content if isinstance(self.content, list) else [self.content]:
            if isinstance(block, TextBlock):
                content_list.append({"type": "text", "text": block.text})
            elif isinstance(block, ThinkingBlock):
                content_list.append({"type": "thinking", "thinking": block.thinking})
            elif isinstance(block, ToolUseBlock):
                content_list.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })
            elif isinstance(block, ToolResultBlock):
                content_list.append({
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error
                })
        return {
            "role": self.role,
            "content": content_list
        }

    @classmethod
    def from_api_response(cls, role: str, content: List[dict]) -> "Message":
        """Parse API response into Message."""
        blocks = []
        for item in content:
            item_type = item.get("type")
            if item_type == "text":
                blocks.append(TextBlock(text=item.get("text", "")))
            elif item_type == "thinking":
                blocks.append(ThinkingBlock(thinking=item.get("thinking", "")))
            elif item_type == "tool_use":
                blocks.append(ToolUseBlock(
                    id=item.get("id", ""),
                    name=item.get("name", ""),
                    input=item.get("input", {})
                ))
            elif item_type == "tool_result":
                # Tool results from API — store as text with tool_use_id context
                tool_use_id = item.get("tool_use_id", "")
                result_content = item.get("content", "")
                if isinstance(result_content, list):
                    result_content = "\n".join(
                        b.get("text", "") for b in result_content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                blocks.append(TextBlock(
                    text=f"[tool_result:{tool_use_id}] {result_content}"
                ))
        return cls(role=role, content=blocks)

    def get_text(self) -> str:
        """Extract all text content from message."""
        texts = []
        for block in self.content if isinstance(self.content, list) else []:
            if isinstance(block, TextBlock):
                texts.append(block.text)
        return "".join(texts)

    def get_tool_uses(self) -> List[ToolUseBlock]:
        """Extract all tool_use blocks."""
        return [
            block for block in self.content if isinstance(self.content, list)
            if isinstance(block, ToolUseBlock)
        ]
