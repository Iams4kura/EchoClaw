"""SkillTool - Execute predefined skills.

Reference: src/tools/SkillTool/SkillTool.ts
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Optional, List, Tuple

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult

# frontmatter 中的 description 提取
_FM_DESC_RE = re.compile(r"^description:\s*(.+)$", re.MULTILINE)


class SkillTool(BaseTool):
    """Execute a predefined skill by name."""

    name = "Skill"
    description = (
        "Execute a skill (predefined prompt/workflow). "
        "Skills are loaded from .claude/skills/ directory. "
        "Available skills can be invoked with /<skill_name> in the chat."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The skill name to execute",
            },
            "args": {
                "type": "string",
                "description": "Optional arguments for the skill",
            },
        },
        "required": ["skill"],
    }
    permission_category = PermissionCategory.READ

    async def execute(
        self, params: dict, abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        skill_name = params["skill"]
        args = params.get("args", "")

        # Search for skill definition
        skill_content = self._find_skill(skill_name)

        if skill_content is None:
            available = ", ".join(
                name for name, _ in self.list_skills()
            )
            return ToolResult(
                content=(
                    f"Skill not found: {skill_name}\n"
                    f"Available skills: {available or 'none'}"
                ),
                is_error=True,
            )

        # Return skill content (the engine will inject it into context)
        result = skill_content
        if args:
            result += f"\n\nArguments: {args}"

        return ToolResult(content=result, is_error=False)

    def _find_skill(self, name: str) -> Optional[str]:
        """Find skill definition file."""
        for path in self._skill_search_paths(name):
            if path.exists():
                try:
                    return path.read_text(encoding="utf-8")
                except Exception:
                    continue
        return None

    @staticmethod
    def _skill_dirs() -> List[Path]:
        """返回所有 skill 搜索目录。"""
        cwd = Path(os.getcwd())
        return [
            cwd / ".claude" / "skills",
            cwd / "skills",
        ]

    @staticmethod
    def _skill_search_paths(name: str) -> List[Path]:
        """返回指定 skill 的搜索路径列表。"""
        cwd = Path(os.getcwd())
        return [
            cwd / ".claude" / "skills" / name / "SKILL.md",
            cwd / ".claude" / "skills" / f"{name}.md",
            cwd / "skills" / name / "SKILL.md",
            cwd / "skills" / f"{name}.md",
        ]

    @classmethod
    def list_skills(cls) -> List[Tuple[str, str]]:
        """扫描所有可用 skill，返回 [(name, description), ...]。

        供斜杠命令系统调用，动态发现已安装的 skill。
        """
        seen: set[str] = set()
        results: List[Tuple[str, str]] = []

        for skill_dir in cls._skill_dirs():
            if not skill_dir.is_dir():
                continue

            # 扫描 .md 文件（平铺模式）
            for f in sorted(skill_dir.iterdir()):
                if f.suffix == ".md" and f.is_file():
                    name = f.stem
                    if name in seen:
                        continue
                    seen.add(name)
                    desc = cls._extract_description(f)
                    results.append((name, desc))

                # 扫描子目录模式（name/SKILL.md）
                elif f.is_dir():
                    skill_file = f / "SKILL.md"
                    if skill_file.exists():
                        name = f.name
                        if name in seen:
                            continue
                        seen.add(name)
                        desc = cls._extract_description(skill_file)
                        results.append((name, desc))

        return results

    @staticmethod
    def _extract_description(path: Path) -> str:
        """从 skill 文件的 YAML frontmatter 中提取 description。"""
        try:
            text = path.read_text(encoding="utf-8")[:500]
            m = _FM_DESC_RE.search(text)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
        return ""
