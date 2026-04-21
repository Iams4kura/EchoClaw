"""Configuration loading and management."""

import os
import yaml
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path


def get_config_home() -> Path:
    """Get the config home directory (~/.config/mini_claude)."""
    xdg = os.getenv("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    return base / "mini_claude"


def get_data_home() -> Path:
    """Get the data home directory (~/.local/share/mini_claude)."""
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "share"
    return base / "mini_claude"


# Resolved paths
CONFIG_HOME = get_config_home()
DATA_HOME = get_data_home()


DEFAULT_CONFIG = {
    "llm": {
        "default_model": "anthropic/claude-3-5-sonnet-20241022",
        "api_key": None,
        "base_url": None,
        "temperature": 0.7,
        "max_tokens": 4096,
    },
    "permissions": {
        "mode": "ask",
        "rules": [
            {"category": "destructive", "mode": "ask"},
            {"category": "external", "mode": "ask"},
            {"category": "write", "mode": "ask"},
            {"category": "read", "mode": "auto_approve"},
        ]
    },
    "ui": {
        "theme": "default",
        "show_git_status": True,
        "stream_buffer_delay_ms": 50,
    },
    "context": {
        "claude_md_paths": ["CLAUDE.md", ".claude/CLAUDE.md"],
        "max_context_tokens": 200000,
        "auto_compact_threshold": 0.9,
    },
    "agent": {
        "max_concurrent": 5,
        "default_model": "anthropic/claude-3-haiku-20240307",
    },
    "persistence": {
        "auto_save": True,
        "sessions_dir": str(DATA_HOME / "sessions"),
        "max_sessions": 50,
    }
}


@dataclass
class Config:
    """Application configuration."""
    # LLM settings
    model: str = "anthropic/claude-3-5-sonnet-20241022"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096

    # Permissions
    permission_mode: str = "ask"
    permission_rules: List[Dict] = field(default_factory=list)

    # Paths
    sessions_dir: str = field(default_factory=lambda: str(DATA_HOME / "sessions"))
    config_dir: str = field(default_factory=lambda: str(CONFIG_HOME))

    @classmethod
    def from_dict(cls, cfg_data: dict) -> "Config":
        """Create config from dictionary."""
        llm = cfg_data.get("llm", {})
        perms = cfg_data.get("permissions", {})

        return cls(
            model=llm.get("default_model", DEFAULT_CONFIG["llm"]["default_model"]),
            api_key=llm.get("api_key"),
            base_url=llm.get("base_url"),
            temperature=llm.get("temperature", 0.7),
            max_tokens=llm.get("max_tokens", 4096),
            permission_mode=perms.get("mode", "ask"),
            permission_rules=perms.get("rules", []),
            sessions_dir=cfg_data.get("persistence", {}).get(
                "sessions_dir", str(DATA_HOME / "sessions")
            ),
        )


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from file with environment fallback.

    Search order:
    1. Explicit config_path argument
    2. ./config/settings.yaml (project-local)
    3. ~/.config/mini_claude/settings.yaml (user global)

    Then environment variables override everything.
    """
    data = _deep_copy_dict(DEFAULT_CONFIG)

    # Try to load from file
    if config_path is None:
        possible_paths = [
            Path("config/settings.yaml"),               # project-local
            CONFIG_HOME / "settings.yaml",               # user global
        ]
        for path in possible_paths:
            if path.exists():
                config_path = str(path)
                break

    if config_path and Path(config_path).exists():
        with open(config_path, 'r') as f:
            file_data = yaml.safe_load(f) or {}
            _deep_update(data, file_data)

    # Environment variable overrides (highest priority)
    if os.getenv("ANTHROPIC_API_KEY"):
        data["llm"]["api_key"] = os.getenv("ANTHROPIC_API_KEY")
    if os.getenv("OPENAI_API_KEY"):
        data["llm"]["api_key"] = os.getenv("OPENAI_API_KEY")
    if os.getenv("OPENAI_BASE_URL"):
        data["llm"]["base_url"] = os.getenv("OPENAI_BASE_URL")
    if os.getenv("OPENAI_MODEL"):
        data["llm"]["default_model"] = os.getenv("OPENAI_MODEL")
    if os.getenv("LITELLM_MODEL"):
        data["llm"]["default_model"] = os.getenv("LITELLM_MODEL")
    if os.getenv("MC_MODEL"):
        data["llm"]["default_model"] = os.getenv("MC_MODEL")

    # Web search: 百度 AI 搜索 API Key（YAML → 环境变量）
    # 如果 YAML 中配置了但环境变量没设，自动注入环境变量供 WebSearchTool 使用
    baidu_key = os.getenv("BAIDU_AI_SEARCH_API_KEY", "")
    if not baidu_key:
        yaml_key = data.get("web_search", {}).get("baidu_ai_search_api_key", "")
        if yaml_key:
            os.environ["BAIDU_AI_SEARCH_API_KEY"] = yaml_key

    return Config.from_dict(data)


def _deep_copy_dict(d: dict) -> dict:
    """Simple deep copy for nested dicts/lists."""
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _deep_copy_dict(v)
        elif isinstance(v, list):
            result[k] = list(v)
        else:
            result[k] = v
    return result


def _deep_update(base: dict, update: dict) -> None:
    """Deep merge update into base."""
    for key, value in update.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def ensure_config_dir() -> Path:
    """Ensure global config directory exists."""
    CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    return CONFIG_HOME


def ensure_data_dir() -> Path:
    """Ensure global data directory exists."""
    DATA_HOME.mkdir(parents=True, exist_ok=True)
    return DATA_HOME


def create_default_config() -> Path:
    """Create default configuration file if not exists. Returns config path."""
    ensure_config_dir()
    config_file = CONFIG_HOME / "settings.yaml"

    if not config_file.exists():
        with open(config_file, 'w') as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)

    return config_file
