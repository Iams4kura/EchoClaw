"""配置加载 — 从 YAML 文件 + 环境变量读取 mini_claw 配置。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class TelegramConfig:
    bot_token: str = ""
    allowed_users: List[int] = field(default_factory=list)


@dataclass
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    encrypt_key: str = ""


@dataclass
class WecomConfig:
    corp_id: str = ""
    corp_secret: str = ""
    agent_id: str = ""
    callback_token: str = ""
    encoding_aes_key: str = ""
    webhook_url: str = ""


@dataclass
class MiddlewareConfig:
    # 运行模式
    mode: str = "personal"           # "personal" (单人分身) | "multi" (多用户平台)
    owner_id: str = ""               # personal 模式的 owner ID，空=接受所有人
    # 鉴权
    allowed_users: List[str] = field(default_factory=list)
    admin_users: List[str] = field(default_factory=list)
    # 限流
    rate_limit_capacity: float = 5.0
    rate_limit_refill: float = 0.33
    # 日志
    message_log_dir: Optional[str] = None


@dataclass
class EngineConfig:
    working_dir: str = "."
    permission_mode: str = "auto"
    model: Optional[str] = None


@dataclass
class BrainLLMConfig:
    """Brain 独立 LLM 配置。为空时复用引擎配置。"""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.3
    max_tokens: int = 1024
    classify_temperature: float = 0.1
    classify_max_tokens: int = 256


@dataclass
class RoutineConfig:
    """Routine 调度配置。"""
    enabled: bool = True


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class ClawConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    wecom: WecomConfig = field(default_factory=WecomConfig)
    middleware: MiddlewareConfig = field(default_factory=MiddlewareConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    brain: BrainLLMConfig = field(default_factory=BrainLLMConfig)
    routine: RoutineConfig = field(default_factory=RoutineConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    workspace_dir: str = "workspace"

    @property
    def is_personal(self) -> bool:
        """是否运行在个人分身模式。"""
        return self.middleware.mode == "personal"

    def engine_as_dict(self) -> Dict[str, Any]:
        """供 HandsManager 使用的引擎配置字典。"""
        return {
            "working_dir": self.engine.working_dir,
            "permission_mode": self.engine.permission_mode,
            "model": self.engine.model,
        }


def load_config(config_path: Optional[str] = None) -> ClawConfig:
    """加载配置。优先级：环境变量 > YAML 文件 > 默认值。"""
    data: Dict[str, Any] = {}

    # 尝试从 YAML 文件加载
    if config_path is None:
        candidates = [
            Path("config/settings.yaml"),
            Path(__file__).parent.parent / "config" / "settings.yaml",
        ]
        for p in candidates:
            if p.exists():
                config_path = str(p)
                break

    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

    # 构建配置
    tg_data = data.get("telegram", {})
    feishu_data = data.get("feishu", {})
    wecom_data = data.get("wecom", {})
    mw_data = data.get("middleware", {})
    engine_data = data.get("engine", {})
    llm_data = data.get("llm", {})
    brain_data = data.get("brain", {})
    routine_data = data.get("routine", {})
    server_data = data.get("server", {})

    config = ClawConfig(
        telegram=TelegramConfig(
            bot_token=tg_data.get("bot_token", ""),
            allowed_users=tg_data.get("allowed_users", []),
        ),
        feishu=FeishuConfig(
            app_id=feishu_data.get("app_id", ""),
            app_secret=feishu_data.get("app_secret", ""),
            verification_token=feishu_data.get("verification_token", ""),
            encrypt_key=feishu_data.get("encrypt_key", ""),
        ),
        wecom=WecomConfig(
            corp_id=wecom_data.get("corp_id", ""),
            corp_secret=wecom_data.get("corp_secret", ""),
            agent_id=wecom_data.get("agent_id", ""),
            callback_token=wecom_data.get("callback_token", ""),
            encoding_aes_key=wecom_data.get("encoding_aes_key", ""),
            webhook_url=wecom_data.get("webhook_url", ""),
        ),
        middleware=MiddlewareConfig(
            mode=mw_data.get("mode", "personal"),
            owner_id=mw_data.get("owner_id", ""),
            allowed_users=mw_data.get("allowed_users", []),
            admin_users=mw_data.get("admin_users", []),
            rate_limit_capacity=mw_data.get("rate_limit_capacity", 5.0),
            rate_limit_refill=mw_data.get("rate_limit_refill", 0.33),
            message_log_dir=mw_data.get("message_log_dir"),
        ),
        engine=EngineConfig(
            working_dir=engine_data.get("working_dir", "."),
            permission_mode=engine_data.get("permission_mode", "auto"),
            model=engine_data.get("model"),
        ),
        brain=BrainLLMConfig(
            model=brain_data.get("model", "") or llm_data.get("default_model", ""),
            api_key=brain_data.get("api_key", "") or llm_data.get("api_key", ""),
            base_url=brain_data.get("base_url", "") or llm_data.get("base_url", ""),
            temperature=brain_data.get("temperature", llm_data.get("temperature", 0.3)),
            max_tokens=brain_data.get("max_tokens", llm_data.get("max_tokens", 1024)),
            classify_temperature=brain_data.get("classify_temperature", 0.1),
            classify_max_tokens=brain_data.get("classify_max_tokens", 256),
        ),
        routine=RoutineConfig(
            enabled=routine_data.get("enabled", True),
        ),
        server=ServerConfig(
            host=server_data.get("host", "0.0.0.0"),
            port=server_data.get("port", 8080),
        ),
        workspace_dir=data.get("workspace_dir", "workspace"),
    )

    # 环境变量覆盖（最高优先级）
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        config.telegram.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if os.getenv("FEISHU_APP_ID"):
        config.feishu.app_id = os.getenv("FEISHU_APP_ID", "")
    if os.getenv("FEISHU_APP_SECRET"):
        config.feishu.app_secret = os.getenv("FEISHU_APP_SECRET", "")
    if os.getenv("WECOM_CORP_ID"):
        config.wecom.corp_id = os.getenv("WECOM_CORP_ID", "")
    if os.getenv("WECOM_CORP_SECRET"):
        config.wecom.corp_secret = os.getenv("WECOM_CORP_SECRET", "")
    if os.getenv("WECOM_WEBHOOK_URL"):
        config.wecom.webhook_url = os.getenv("WECOM_WEBHOOK_URL", "")
    if os.getenv("MINI_CLAW_WORKING_DIR"):
        config.engine.working_dir = os.getenv("MINI_CLAW_WORKING_DIR", ".")
    if os.getenv("MINI_CLAW_PORT"):
        config.server.port = int(os.getenv("MINI_CLAW_PORT", "8080"))
    if os.getenv("MINI_CLAW_WORKSPACE_DIR"):
        config.workspace_dir = os.getenv("MINI_CLAW_WORKSPACE_DIR", "workspace")
    if os.getenv("MINI_CLAW_MODE"):
        config.middleware.mode = os.getenv("MINI_CLAW_MODE", "personal")
    if os.getenv("MINI_CLAW_OWNER_ID"):
        config.middleware.owner_id = os.getenv("MINI_CLAW_OWNER_ID", "")

    return config
