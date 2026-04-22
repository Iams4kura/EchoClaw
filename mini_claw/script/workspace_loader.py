"""WorkspaceLoader — 统一加载 workspace/ 下所有 Markdown 文件。

核心理念：文件即记忆。每次启动读取 workspace/ 下所有 md 文件，
保持人格和认知的连续性。
"""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .soul.models import (
    GreetingTemplates,
    MoodState,
    MoodTone,
    PersonalityTraits,
    SoulConfig,
)

logger = logging.getLogger(__name__)


class WorkspaceLoader:
    """workspace/ 目录的统一加载器。

    读取：
    - SOUL.md + IDENTITY.md → SoulConfig
    - AGENTS.md → 系统规则原文
    - HEARTBEAT.md → Routine 任务列表
    - USER.md → 用户信息
    - TOOLS.md → 工具配置
    - MEMORY.md → 记忆索引
    - BOOTSTRAP.md → 首次引导标记
    - .openclaw/mood.json → 情绪状态持久化
    """

    def __init__(self, workspace_dir: str) -> None:
        self._root = Path(workspace_dir)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def memory_dir(self) -> str:
        """记忆文件目录路径。"""
        return str(self._root / "memory")

    @property
    def memory_index_path(self) -> str:
        """MEMORY.md 的路径（workspace 根目录）。"""
        return str(self._root / "MEMORY.md")

    # ── 文件读取 ─────────────────────────────────────────────

    def _read(self, filename: str) -> str:
        """读取 workspace 下的文件，不存在返回空字符串。"""
        path = self._root / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _write(self, filename: str, content: str) -> None:
        """写入 workspace 下的文件。"""
        path = self._root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # ── Soul 加载 ────────────────────────────────────────────

    def load_soul(self) -> SoulConfig:
        """解析 SOUL.md + IDENTITY.md → SoulConfig。"""
        soul_md = self._read("SOUL.md")
        identity_md = self._read("IDENTITY.md")
        return self._parse_soul(soul_md, identity_md)

    def _parse_soul(self, soul_md: str, identity_md: str) -> SoulConfig:
        """从 Markdown 内容解析 SoulConfig。

        设计理念：SOUL.md 全文直接注入 Brain 作为人格指令，
        不再拆解为结构化字段。IDENTITY.md 提取身份基本信息和情绪基调。
        """
        identity_sections = _parse_md_sections(identity_md)

        # 从 IDENTITY.md 读取基本信息
        identity_fields = _parse_key_value_list(
            identity_sections.get("基本信息", "")
        )
        name = identity_fields.get("名字", "小爪")
        role = identity_fields.get("角色", "数字员工")
        style = identity_fields.get("风格", "")

        # 表达习惯
        expression_habits = _parse_list(
            identity_sections.get("表达习惯", "")
        )

        # 情绪基调（Markdown 表格格式）
        mood_tones = _parse_md_table(
            identity_sections.get("情绪基调", "")
        )

        personality = PersonalityTraits(
            name=name,
            role=role,
            style=style,
            expression_habits=expression_habits,
        )

        # 从情绪基调表格构建 GreetingTemplates
        # 注意：表格中的内容可能是"基调描述"（如"坦诚、立刻给方案"）而非消息模板。
        # 只有包含 { 占位符的才作为模板使用，否则用默认模板。
        tone_map = {t.scene: t.tone for t in mood_tones}

        def _tone_or_default(scene: str, default: str) -> str:
            val = tone_map.get(scene)
            if not val:
                return default
            # 包含占位符 → 是真正的模板
            if "{" in val:
                return val
            # 纯基调描述 → 使用默认模板（基调会通过 Soul system prompt 注入）
            return default

        greetings = GreetingTemplates(
            morning=_tone_or_default("早上", GreetingTemplates.morning),
            afternoon=_tone_or_default("下午", GreetingTemplates.afternoon),
            evening=_tone_or_default("晚上", GreetingTemplates.evening),
            error=_tone_or_default("出错", GreetingTemplates.error),
            task_done=_tone_or_default("完成", GreetingTemplates.task_done),
            thinking=_tone_or_default("思考中", GreetingTemplates.thinking),
        )

        return SoulConfig(
            personality=personality,
            greetings=greetings,
            mood_tones=mood_tones,
            soul_raw=soul_md.strip(),
        )

    # ── 其他文件加载 ─────────────────────────────────────────

    def load_agents(self) -> str:
        """读取 AGENTS.md 原文，作为 Brain 系统规则。"""
        return self._read("AGENTS.md")

    def load_user(self) -> Dict[str, str]:
        """读取 USER.md 的结构化字段。"""
        content = self._read("USER.md")
        if not content.strip() or "首次交互后" in content:
            return {}
        sections = _parse_md_sections(content)
        return _parse_key_value_list(sections.get("基本信息", content))

    def load_tools(self) -> Dict[str, Dict[str, str]]:
        """读取 TOOLS.md 的配置。"""
        content = self._read("TOOLS.md")
        sections = _parse_md_sections(content)
        result: Dict[str, Dict[str, str]] = {}
        for name, body in sections.items():
            result[name] = _parse_key_value_list(body)
        return result

    def load_heartbeat(self) -> List["HeartbeatTask"]:
        """解析 HEARTBEAT.md → HeartbeatTask 列表。

        格式：
        ## 任务名
        自由文本描述（做什么 + 条件 + 方法，全部自然语言）
        """
        from .routine.models import HeartbeatTask

        content = self._read("HEARTBEAT.md")
        sections = _parse_md_sections(content)
        tasks: List[HeartbeatTask] = []

        for title, body in sections.items():
            prompt = body.strip()
            if not prompt:
                continue

            tasks.append(HeartbeatTask(
                name=title.replace(" ", "_").lower(),
                description=title,
                prompt=prompt,
            ))

        return tasks

    # ── Bootstrap ────────────────────────────────────────────

    def is_first_boot(self) -> bool:
        """BOOTSTRAP.md 是否存在（首次启动标记）。"""
        return (self._root / "BOOTSTRAP.md").exists()

    def load_bootstrap(self) -> str:
        """读取 BOOTSTRAP.md 内容。不存在返回空字符串。"""
        return self._read("BOOTSTRAP.md")

    def complete_bootstrap(self) -> None:
        """删除 BOOTSTRAP.md，标记引导完成。"""
        path = self._root / "BOOTSTRAP.md"
        if path.exists():
            path.unlink()
            logger.info("首次引导完成，已删除 BOOTSTRAP.md")

    # ── 情绪持久化 ───────────────────────────────────────────

    def save_mood(self, mood: MoodState) -> None:
        """将情绪状态写入 .openclaw/mood.json。"""
        data = {
            "energy": mood.energy,
            "mood": mood.mood,
            "consecutive_errors": mood.consecutive_errors,
            "tasks_completed_today": mood.tasks_completed_today,
            "last_interaction": mood.last_interaction,
            "last_reset_date": mood.last_reset_date,
        }
        self._write(".openclaw/mood.json", json.dumps(data, indent=2))

    def load_mood(self) -> MoodState:
        """从 .openclaw/mood.json 恢复情绪状态。"""
        content = self._read(".openclaw/mood.json")
        if not content:
            return MoodState()
        try:
            data = json.loads(content)
            return MoodState(
                energy=data.get("energy", 1.0),
                mood=data.get("mood", "neutral"),
                consecutive_errors=data.get("consecutive_errors", 0),
                tasks_completed_today=data.get("tasks_completed_today", 0),
                last_interaction=data.get("last_interaction", time.time()),
                last_reset_date=data.get("last_reset_date", ""),
            )
        except (json.JSONDecodeError, KeyError):
            return MoodState()

    # ── USER.md 更新 ─────────────────────────────────────────

    def update_user(self, info: Dict[str, str]) -> None:
        """更新 USER.md 中的用户信息。"""
        lines = ["# USER.md — 用户信息", "", "## 基本信息", ""]
        for k, v in info.items():
            lines.append(f"- **{k}**: {v}")
        self._write("USER.md", "\n".join(lines) + "\n")

    # ── 文件操作 API（供 Brain workspace_op 调用） ──────────────

    # 可直接编辑的白名单文件（workspace 根目录下）
    EDITABLE_FILES = {
        "SOUL.md", "IDENTITY.md", "USER.md", "TOOLS.md",
        "AGENTS.md", "MEMORY.md", "HEARTBEAT.md",
    }

    def read_file(self, filename: str) -> str:
        """读取 workspace 下的文件。

        支持 "SOUL.md"、"memory/xxx.md" 等相对路径。
        安全校验：禁止 ".." 和绝对路径。
        """
        self._validate_path(filename)
        return self._read(filename)

    def write_file(self, filename: str, content: str) -> None:
        """写入 workspace 下的文件（覆盖）。

        仅允许白名单文件或 memory/ 子目录下的文件。
        """
        self._validate_path(filename)
        self._validate_writable(filename)
        self._write(filename, content)
        logger.info("Workspace write: %s (%d chars)", filename, len(content))

    def append_file(self, filename: str, content: str) -> None:
        """追加内容到 workspace 下的文件末尾。"""
        self._validate_path(filename)
        self._validate_writable(filename)
        existing = self._read(filename)
        if existing and not existing.endswith("\n"):
            existing += "\n"
        self._write(filename, existing + content)
        logger.info("Workspace append: %s (+%d chars)", filename, len(content))

    def update_section(
        self, filename: str, section: str, content: str, append: bool = False,
    ) -> None:
        """更新指定文件中某个 ## section 的内容。

        append=False（默认）：替换整个 section 内容。
        append=True：在 section 末尾追加内容（保留已有内容）。
        如果 section 不存在则追加到文件末尾。
        """
        self._validate_path(filename)
        self._validate_writable(filename)
        text = self._read(filename)
        header = f"## {section}"

        if header in text:
            idx = text.index(header)
            after_header = idx + len(header)
            # 找下一个 ## 标题
            next_section = text.find("\n## ", after_header)
            if append:
                # 追加模式：在 section 末尾（下一个 ## 之前）插入新内容
                if next_section == -1:
                    existing_body = text[after_header:].rstrip()
                    new_text = text[:after_header] + existing_body + "\n" + content.strip() + "\n"
                else:
                    existing_body = text[after_header:next_section].rstrip()
                    new_text = (
                        text[:after_header] + existing_body + "\n" + content.strip() + "\n"
                        + text[next_section:]
                    )
            else:
                if next_section == -1:
                    new_text = text[:after_header] + "\n\n" + content.strip() + "\n"
                else:
                    new_text = (
                        text[:after_header] + "\n\n" + content.strip() + "\n"
                        + text[next_section:]
                    )
        else:
            # section 不存在 → 追加到文件末尾
            new_text = text.rstrip() + f"\n\n{header}\n\n{content.strip()}\n"

        self._write(filename, new_text)
        logger.info("Workspace update_section: %s ## %s", filename, section)

    def _validate_path(self, filename: str) -> None:
        """校验路径安全：禁止 .. 和绝对路径。"""
        if ".." in filename or filename.startswith("/"):
            raise ValueError(f"不安全的路径: {filename}")

    def _validate_writable(self, filename: str) -> None:
        """校验文件是否可写：白名单文件 或 memory/ 子目录。"""
        if filename in self.EDITABLE_FILES:
            return
        if filename.startswith("memory/"):
            return
        if filename.startswith(".openclaw/"):
            return
        raise ValueError(
            f"不可编辑的文件: {filename}。"
            f"允许: {self.EDITABLE_FILES} 或 memory/ 子目录"
        )

    # ── 日记系统（memory/YYYY-MM-DD.md） ────────────────────────

    def _diary_filename(self, dt: Optional[str] = None) -> str:
        """日记文件的 workspace 相对路径。"""
        d = dt or date.today().isoformat()
        return f"memory/{d}.md"

    def read_diary(self, dt: str = "") -> str:
        """读取指定日期的日记。不指定则读今天。"""
        return self._read(self._diary_filename(dt or None))

    def append_diary(self, content: str, dt: str = "") -> None:
        """追加日记条目到 memory/YYYY-MM-DD.md。

        如果文件不存在会自动创建（带标题头）。
        """
        filename = self._diary_filename(dt or None)
        existing = self._read(filename)
        if not existing:
            d = dt or date.today().isoformat()
            existing = f"# {d} 日记\n\n"
        now = datetime.now().strftime("%H:%M")
        entry = f"- [{now}] {content}\n"
        self._write(filename, existing + entry)

    def list_recent_diaries(self, days: int = 2) -> str:
        """读取最近 N 天的日记内容，拼接返回。"""
        parts: List[str] = []
        today = date.today()
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            content = self._read(self._diary_filename(d))
            if content:
                parts.append(content.strip())
        return "\n\n---\n\n".join(parts)

    # ── 会话日志（memory/sessions/YYYY-MM-DD.jsonl） ─────────────

    def append_session_log(self, record: dict) -> None:
        """追加一条完整的会话记录到 memory/sessions/{YYYY-MM-DD}.jsonl。

        每行一个 JSON 对象，包含用户消息、意图分类、决策和模型回复。
        """
        import json

        filename = f"memory/sessions/{date.today().isoformat()}.jsonl"
        existing = self._read(filename)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        self._write(filename, existing + line)

    # ── Learnings 系统（memory/learnings/） ──────────────────────

    def _learnings_path(self, category: str) -> str:
        """learnings 文件路径。"""
        filemap = {
            "learnings": "memory/learnings/LEARNINGS.md",
            "errors": "memory/learnings/ERRORS.md",
            "features": "memory/learnings/FEATURE_REQUESTS.md",
        }
        return filemap.get(category, f"memory/learnings/{category}.md")

    def append_learning(self, entry_id: str, content: str) -> None:
        """追加一条经验教训到 LEARNINGS.md。"""
        self._append_learnings_entry("learnings", entry_id, content)

    def append_error(self, entry_id: str, content: str) -> None:
        """追加一条错误记录到 ERRORS.md。"""
        self._append_learnings_entry("errors", entry_id, content)

    def append_feature_request(self, entry_id: str, content: str) -> None:
        """追加一条功能请求到 FEATURE_REQUESTS.md。"""
        self._append_learnings_entry("features", entry_id, content)

    def _append_learnings_entry(
        self, category: str, entry_id: str, content: str
    ) -> None:
        """向 learnings 文件追加一条带 ID 的条目。"""
        filename = self._learnings_path(category)
        existing = self._read(filename)
        if not existing:
            titles = {
                "learnings": "# 经验教训",
                "errors": "# 错误记录",
                "features": "# 功能请求",
            }
            existing = titles.get(category, f"# {category}") + "\n\n"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"### [{entry_id}] {now}\n\n{content}\n\n"
        self._write(filename, existing + entry)
        logger.info("Learnings appended: %s -> %s", entry_id, filename)

    # ── 能力枚举 ────────────────────────────────────────────────

    def get_skills(self) -> List[Dict[str, str]]:
        """返回 Brain 当前可用能力列表（供 BOOTSTRAP 展示）。"""
        return [
            {"name": "对话", "desc": "闲聊、问答、知识查询"},
            {"name": "编码", "desc": "写代码、改 Bug、Review、重构"},
            {"name": "文件操作", "desc": "读写 workspace 文件、搜索、编辑"},
            {"name": "记忆", "desc": "记住/回忆/忘记信息，管理 MEMORY.md"},
            {"name": "规划", "desc": "复杂任务分解与多步执行"},
            {"name": "日记", "desc": "每日自动记录工作日志"},
            {"name": "自我改进", "desc": "从错误中学习、更新工作规则"},
            {"name": "定时任务", "desc": "HEARTBEAT.md 驱动的定期巡检"},
        ]


# ── Markdown 解析工具函数 ────────────────────────────────────


def _parse_md_sections(text: str) -> Dict[str, str]:
    """将 Markdown 按 ## 标题分割为 {标题: 内容} 字典。

    只识别二级标题（##），忽略一级和三级。
    """
    sections: Dict[str, str] = {}
    current_title: Optional[str] = None
    current_lines: List[str] = []

    for line in text.split("\n"):
        if line.startswith("## "):
            if current_title is not None:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = line[3:].strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)

    if current_title is not None:
        sections[current_title] = "\n".join(current_lines).strip()

    return sections


def _parse_list(text: str) -> List[str]:
    """从 Markdown 列表中提取项目。

    识别 `- item` 和 `* item` 格式（不含加粗键值对）。
    """
    items: List[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith(("- ", "* ")):
            item = line[2:].strip()
            # 跳过键值对格式（**key**: value）
            if item.startswith("**") and "**:" in item:
                continue
            items.append(item)
    return items


def _parse_key_value_list(text: str) -> Dict[str, str]:
    """从 Markdown 列表中提取键值对。

    识别格式（同时兼容中英文冒号 : 和 ：）：
    - **key**: value      / - **key：** value
    - key: value          / - key：value
    """
    result: Dict[str, str] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith(("- ", "* ")):
            continue
        item = line[2:].strip()

        # **key**: value 或 **key：** value 格式
        match = re.match(r"\*\*(.+?)\*\*[:\uff1a]\s*(.+)", item)
        if not match:
            # **key：** 中冒号在 bold 内部：**key：**\s*value
            match = re.match(r"\*\*(.+?)[:\uff1a]\*\*\s*(.+)", item)
        if match:
            result[match.group(1).strip()] = match.group(2).strip()
            continue

        # key: value 或 key：value 格式（不含加粗）
        if not item.startswith("**"):
            for sep in ["：", ":"]:
                if sep in item:
                    k, v = item.split(sep, 1)
                    result[k.strip()] = v.strip()
                    break

    return result


def _parse_md_table(text: str) -> List["MoodTone"]:
    """从 Markdown 表格中提取行数据。

    识别格式：
    | 场景 | 基调 |
    |------|------|
    | 早上 | 轻快、有精神 |
    """
    from .soul.models import MoodTone

    rows: List[MoodTone] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 2:
            continue
        # 跳过表头和分隔行
        if cells[0] in ("场景", "") or set(cells[0]) <= {"-", " "}:
            continue
        if all(set(c) <= {"-", " ", ":"} for c in cells):
            continue
        rows.append(MoodTone(scene=cells[0], tone=cells[1]))
    return rows
