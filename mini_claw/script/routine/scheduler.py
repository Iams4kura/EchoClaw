"""RoutineScheduler — asyncio 驱动的自驱日程调度器。

每分钟检查一次，匹配到期任务后构造 UnifiedMessage 送入 Brain 认知循环。

系统任务（RoutineJob）：精确 cron 调度，调度器判断 _should_run。
心跳任务（HeartbeatTask）：每小时唤醒，由模型自主判断是否执行。
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..gateway.models import UnifiedMessage
from .builtin import get_builtin_routines
from .models import HeartbeatTask, RoutineFrequency, RoutineJob

logger = logging.getLogger(__name__)


class RoutineScheduler:
    """自驱日程调度器。

    工作方式：
    1. 启动后每 60 秒检查一次
    2. 匹配到期的 RoutineJob
    3. 构造 UnifiedMessage(platform="routine") 发给 Brain 处理
    4. 记录上次执行时间，避免重复触发
    """

    def __init__(
        self,
        on_trigger: Optional[Callable[[UnifiedMessage], Awaitable[Any]]] = None,
        workspace_root: Optional[str] = None,
    ) -> None:
        self._jobs: List[RoutineJob] = []
        self._on_trigger = on_trigger
        self._last_run: Dict[str, float] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False

        # 心跳系统
        self._heartbeat_tasks: List[HeartbeatTask] = []
        self._heartbeat_interval: int = 3600  # 1 小时
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._heartbeat_log: Dict[str, str] = {}  # name → 最近一次执行时间
        self._on_heartbeat_judge: Optional[Callable[[str], Awaitable[str]]] = None
        self._workspace_root = Path(workspace_root) if workspace_root else None

    def load_builtin(self) -> None:
        """加载内置任务。"""
        builtins = get_builtin_routines()
        self._jobs.extend(builtins)
        logger.info("加载 %d 个内置 Routine 任务", len(builtins))

    def load_heartbeat_tasks(self, tasks: List[HeartbeatTask]) -> None:
        """加载心跳任务（由模型自主判断是否执行）。"""
        self._heartbeat_tasks = tasks
        # 从持久化文件恢复执行记录
        self._load_heartbeat_state()
        for t in self._heartbeat_tasks:
            t.last_executed = self._heartbeat_log.get(t.name)
        logger.info("加载 %d 个心跳任务", len(tasks))

    def load_from_config(self, config_data: List[Dict[str, Any]]) -> None:
        """从配置数据加载自定义任务。"""
        for item in config_data:
            try:
                job = RoutineJob(
                    name=item["name"],
                    description=item.get("description", ""),
                    prompt=item["prompt"],
                    frequency=RoutineFrequency(item.get("frequency", "daily")),
                    cron_expr=item.get("cron_expr", ""),
                    hour=item.get("hour", 9),
                    minute=item.get("minute", 0),
                    weekday=item.get("weekday", 0),
                    interval_minutes=item.get("interval_minutes", 30),
                    enabled=item.get("enabled", True),
                    target_user=item.get("target_user"),
                    target_platform=item.get("target_platform"),
                    tags=item.get("tags", []),
                    executor=item.get("executor", "brain"),
                )
                if job.enabled:
                    self._jobs.append(job)
            except (KeyError, ValueError) as e:
                logger.warning("无效的 Routine 配置: %s", e)

    async def start(self) -> None:
        """启动调度循环。

        首次启动时将所有任务的 last_run 初始化为当前时间，
        避免 HOURLY 等周期性任务在启动后立刻触发。
        DAILY/WEEKLY 任务按时间点匹配，不受此影响。
        """
        if self._running:
            return
        self._running = True
        now = time.time()
        for job in self._jobs:
            if job.name not in self._last_run:
                self._last_run[job.name] = now
        self._task = asyncio.create_task(self._loop())

        # 心跳循环
        if self._heartbeat_tasks:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.info("心跳循环已启动，%d 个任务，间隔 %ds",
                        len(self._heartbeat_tasks), self._heartbeat_interval)

        logger.info("RoutineScheduler 已启动，共 %d 个系统任务", len(self._jobs))

    async def stop(self) -> None:
        """停止调度循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info("RoutineScheduler 已停止")

    @property
    def job_count(self) -> int:
        return len(self._jobs)

    def list_jobs(self) -> List[Dict[str, Any]]:
        """返回所有任务的概要信息（系统任务 + 心跳任务）。"""
        result = [
            {
                "name": j.name,
                "type": "system",
                "description": j.description,
                "frequency": j.frequency.value,
                "enabled": j.enabled,
                "last_run": self._last_run.get(j.name),
            }
            for j in self._jobs
        ]
        for t in self._heartbeat_tasks:
            result.append({
                "name": t.name,
                "type": "heartbeat",
                "description": t.description,
                "condition": t.condition,
                "last_executed": self._heartbeat_log.get(t.name, "从未执行"),
            })
        return result

    async def _loop(self) -> None:
        """主调度循环。"""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("Routine 调度异常: %s", e)
            await asyncio.sleep(60)

    async def _tick(self) -> None:
        """每分钟执行一次的检查。

        到期的任务并发触发（create_task），避免一个慢任务阻塞后续检查。
        """
        now = datetime.now()
        for job in self._jobs:
            if not job.enabled:
                continue
            if self._should_run(job, now):
                # 立即标记已执行，避免下一轮重复触发
                self._last_run[job.name] = time.time()
                asyncio.create_task(self._trigger(job))

    def _should_run(self, job: RoutineJob, now: datetime) -> bool:
        """判断任务是否应该执行。"""
        last = self._last_run.get(job.name, 0)

        match job.frequency:
            case RoutineFrequency.ONCE:
                return last == 0

            case RoutineFrequency.HOURLY:
                return (time.time() - last) >= job.interval_minutes * 60

            case RoutineFrequency.DAILY:
                if now.hour != job.hour:
                    return False
                # 在目标分钟的 ±5 分钟窗口内触发（容忍 sleep 漂移）
                if abs(now.minute - job.minute) > 5:
                    return False
                # 基于日期去重：今天还没跑过就触发
                last_dt = datetime.fromtimestamp(last) if last > 0 else datetime.min
                return last_dt.date() < now.date()

            case RoutineFrequency.WEEKLY:
                if now.weekday() != job.weekday:
                    return False
                if now.hour != job.hour:
                    return False
                if abs(now.minute - job.minute) > 5:
                    return False
                # 基于日期去重
                last_dt = datetime.fromtimestamp(last) if last > 0 else datetime.min
                return last_dt.date() < now.date()

            case RoutineFrequency.CRON:
                return self._match_cron(job.cron_expr, now) and (time.time() - last) > 55

        return False

    @staticmethod
    def _match_cron(expr: str, now: datetime) -> bool:
        """简单 cron 匹配：minute hour day month weekday。"""
        parts = expr.split()
        if len(parts) != 5:
            return False

        fields = [now.minute, now.hour, now.day, now.month, now.weekday()]
        for field_val, pattern in zip(fields, parts):
            if pattern == "*":
                continue
            if "/" in pattern:
                # */N 格式
                base, step = pattern.split("/", 1)
                try:
                    step_int = int(step)
                    if field_val % step_int != 0:
                        return False
                except ValueError:
                    return False
            else:
                try:
                    if field_val != int(pattern):
                        return False
                except ValueError:
                    return False

        return True

    async def _trigger(self, job: RoutineJob) -> None:
        """触发一个 Routine 任务。"""
        logger.info("触发 Routine: %s (%s)", job.name, job.description)

        if not self._on_trigger:
            logger.warning("无 on_trigger 回调，跳过 Routine: %s", job.name)
            return

        msg = UnifiedMessage(
            platform="routine",
            user_id=job.target_user or "system",
            chat_id=f"routine_{job.name}",
            content=job.prompt,
        )

        try:
            await self._on_trigger(msg)
        except Exception as e:
            logger.error("Routine 执行失败 %s: %s", job.name, e)

    # ── 心跳系统 ─────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """心跳循环：每 _heartbeat_interval 秒唤醒一次，让模型判断任务。"""
        while self._running:
            await asyncio.sleep(self._heartbeat_interval)
            if self._heartbeat_tasks and self._on_heartbeat_judge:
                try:
                    await self._heartbeat_check()
                except Exception as e:
                    logger.error("心跳检查异常: %s", e)

    async def _heartbeat_check(self) -> None:
        """核心：把所有心跳任务打包发给模型，由模型逐个判断是否执行。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        task_list = []
        for t in self._heartbeat_tasks:
            last = self._heartbeat_log.get(t.name, "从未执行")
            task_list.append(
                f"- 任务: {t.description}\n  条件: {t.condition}\n  上次执行: {last}"
            )

        content = (
            f"[心跳检查] 当前时间: {now}\n\n"
            "以下是你的心跳任务列表，请逐个判断现在是否需要执行：\n\n"
            f"{chr(10).join(task_list)}\n\n"
            "对每个任务，回复 JSON 数组，格式：\n"
            '[{"task": "任务名（与上面的任务字段一致）", "execute": true/false, "reason": "判断理由"}]\n\n'
            "只输出 JSON 数组，不要输出其他内容。"
        )

        logger.info("心跳检查开始，%d 个任务", len(self._heartbeat_tasks))

        response = await self._on_heartbeat_judge(content)
        decisions = self._parse_heartbeat_decisions(response)

        executed_count = 0
        for d in decisions:
            if d.get("execute"):
                task = next(
                    (t for t in self._heartbeat_tasks if t.description == d.get("task")),
                    None,
                )
                if task:
                    self._heartbeat_log[task.name] = now
                    task.last_executed = now
                    self._save_heartbeat_state()
                    asyncio.create_task(self._trigger_heartbeat(task))
                    executed_count += 1
                    logger.info("心跳决策: 执行 [%s] — %s", d["task"], d.get("reason", ""))
                else:
                    logger.warning("心跳决策: 任务 [%s] 未找到", d.get("task"))
            else:
                logger.info("心跳决策: 跳过 [%s] — %s", d.get("task", "?"), d.get("reason", ""))

        logger.info("心跳检查完成，执行 %d/%d 个任务", executed_count, len(self._heartbeat_tasks))

    async def _trigger_heartbeat(self, task: HeartbeatTask) -> None:
        """执行单个心跳任务。"""
        logger.info("心跳触发: %s", task.description)

        if not self._on_trigger:
            logger.warning("无 on_trigger 回调，跳过心跳任务: %s", task.name)
            return

        msg = UnifiedMessage(
            platform="routine",
            user_id="system",
            chat_id=f"heartbeat_{task.name}",
            content=task.prompt,
        )

        try:
            await self._on_trigger(msg)
        except Exception as e:
            logger.error("心跳任务执行失败 %s: %s", task.name, e)

    # ── 心跳状态持久化 ───────────────────────────────────────

    @property
    def _heartbeat_state_path(self) -> Optional[Path]:
        if self._workspace_root:
            return self._workspace_root / ".heartbeat_state.json"
        return None

    def _load_heartbeat_state(self) -> None:
        """从 .heartbeat_state.json 恢复心跳执行记录。"""
        path = self._heartbeat_state_path
        if not path or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._heartbeat_log = data
            logger.info("恢复心跳状态: %d 条记录", len(data))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("加载心跳状态失败: %s", e)

    def _save_heartbeat_state(self) -> None:
        """保存心跳执行记录到 .heartbeat_state.json。"""
        path = self._heartbeat_state_path
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._heartbeat_log, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("保存心跳状态失败: %s", e)

    @staticmethod
    def _parse_heartbeat_decisions(text: str) -> List[Dict[str, Any]]:
        """从模型输出解析心跳决策 JSON 数组。"""
        text = text.strip()

        # 提取 ```json ... ``` 中的内容
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                cleaned = part.strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
                if cleaned.startswith("["):
                    try:
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        continue

        # 直接尝试解析
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("无法解析心跳决策 JSON: %s", text[:200])
        return []
