"""ID generation utilities."""

import secrets
import string
from datetime import datetime


# Generate URL-safe tokens
ALPHANUMERIC = string.ascii_lowercase + string.digits


def generate_id(length: int = 8) -> str:
    """Generate a random alphanumeric ID."""
    return ''.join(secrets.choice(ALPHANUMERIC) for _ in range(length))


def generate_task_id(task_type: str) -> str:
    """Generate task ID with prefix."""
    prefixes = {
        "bash": "b",
        "agent": "a",
        "workflow": "w",
        "dream": "d",
    }
    prefix = prefixes.get(task_type, "x")
    return f"{prefix}{generate_id(8)}"


def generate_agent_id() -> str:
    """Generate agent ID."""
    return f"a{generate_id(8)}"


def generate_session_id() -> str:
    """Generate session ID with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = generate_id(4)
    return f"mc_{timestamp}_{random_suffix}"


def generate_tool_use_id() -> str:
    """Generate tool_use ID for LLM interactions."""
    return f"tu_{generate_id(16)}"
