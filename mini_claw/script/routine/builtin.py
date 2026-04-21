"""内置 Routine 任务定义。"""

from .models import RoutineFrequency, RoutineJob

# ── 内置任务列表 ─────────────────────────────────────────────

BUILTIN_ROUTINES = [
    RoutineJob(
        name="sys_health_check",
        description="系统健康检查",
        prompt="请检查当前系统状态，包括活跃引擎数、内存使用情况。如果一切正常只需简短确认，异常时详细报告。",
        frequency=RoutineFrequency.HOURLY,
        interval_minutes=30,
        executor="brain",
        tags=["system", "health"],
    ),
    RoutineJob(
        name="sys_daily_summary",
        description="每日工作总结",
        prompt="请生成今日工作简报：今日完成的任务摘要、有价值的对话要点、需要记住的信息。用简洁的列表格式。",
        frequency=RoutineFrequency.DAILY,
        hour=21,
        minute=0,
        executor="brain",
        tags=["summary", "daily"],
    ),
    RoutineJob(
        name="sys_memory_review",
        description="记忆回顾与整理",
        prompt="请回顾今天的交互记录，提取值得长期记住的信息（用户偏好、项目决策、技术方案）。已有记忆不需重复。",
        frequency=RoutineFrequency.DAILY,
        hour=18,
        minute=0,
        executor="brain",
        tags=["memory", "daily"],
    ),
]


def get_builtin_routines() -> list[RoutineJob]:
    """返回所有内置任务（已启用的）。"""
    return [r for r in BUILTIN_ROUTINES if r.enabled]
