import asyncio
import traceback
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from . import db, runner

scheduler = AsyncIOScheduler()
_alarm_callback = None


def set_alarm_callback(cb):
    global _alarm_callback
    _alarm_callback = cb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def _broadcast_alarm(task: dict) -> None:
    if _alarm_callback is None:
        return
    try:
        await _alarm_callback(task)
    except Exception:
        pass


async def execute_reminder(task_id: str) -> None:
    task = await asyncio.to_thread(db.get_task, task_id)
    if not task:
        return
    if task.get("board_column") == "done":
        return
    if not task.get("reminder_at"):
        return
    if task.get("reminder_acknowledged"):
        return
    try:
        reminder_dt = datetime.fromisoformat(task["reminder_at"]).astimezone(
            timezone.utc
        )
    except Exception:
        return
    # Allow a small tolerance for scheduler jitter
    if reminder_dt > datetime.now(timezone.utc) + timedelta(seconds=5):
        await ReminderService(scheduler).sync_task(task_id)
        return
    await _broadcast_alarm(task)


class ReminderService:
    def __init__(self, scheduler):
        self.scheduler = scheduler

    def _job_id(self, task_id: str) -> str:
        return f"shelldeck_reminder_{task_id}"

    def remove_task(self, task_id: str) -> None:
        try:
            self.scheduler.remove_job(self._job_id(task_id))
        except Exception:
            pass

    def sync_task(self, task_id: str) -> None:
        self.remove_task(task_id)
        task = db.get_task(task_id)
        if not task or task.get("board_column") == "done":
            return
        reminder_at = task.get("reminder_at")
        if not reminder_at:
            return
        if task.get("reminder_acknowledged"):
            return
        try:
            run_date = datetime.fromisoformat(reminder_at).astimezone(timezone.utc)
        except Exception:
            return
        self.scheduler.add_job(
            execute_reminder,
            trigger=DateTrigger(run_date=run_date, timezone=UTC),
            id=self._job_id(task_id),
            replace_existing=True,
            misfire_grace_time=86400,
            kwargs={"task_id": task_id},
        )

    async def sync_all(self) -> None:
        for job in list(self.scheduler.get_jobs()):
            if job.id.startswith("shelldeck_reminder_"):
                self.scheduler.remove_job(job.id)
        tasks = await asyncio.to_thread(db.list_tasks)
        for task in tasks:
            self.sync_task(task["id"])


reminder_service = ReminderService(scheduler)


def validate_cron(expression: str) -> CronTrigger:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("Cron expression must contain five fields.")
    minute, hour, day, month, weekday = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=weekday,
        timezone=UTC,
    )


def cron_trigger(cron: str, tz: str = "UTC") -> CronTrigger:
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError("Cron expression must contain five fields.")
    try:
        tzinfo = ZoneInfo(tz)
    except Exception:
        tzinfo = UTC
    minute, hour, day, month, weekday = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=weekday,
        timezone=tzinfo,
    )


async def execute_job(job_id: str):
    job = await asyncio.to_thread(db.get_job, job_id)
    if not job or not job.get("enabled"):
        return

    run_at = _now()
    await asyncio.to_thread(
        db.update_job,
        job_id,
        last_status="running",
        last_run_on=run_at,
    )

    try:
        result = await asyncio.to_thread(
            runner.run_command,
            job["project_id"],
            job["command"],
            None,
            job.get("timeout_seconds", 60),
        )
    except Exception:
        result = {
            "status": "failed",
            "exit_code": None,
            "output": "",
            "error": traceback.format_exc(),
            "duration_ms": 0,
        }

    status = result["status"]
    duration_ms = result["duration_ms"]
    exit_code = result["exit_code"]
    output = result["output"]
    error = result["error"]
    successful = status == "success"

    try:
        last_run_dt = datetime.fromisoformat(run_at)
        trigger = cron_trigger(job["cron"], job.get("timezone", "UTC"))
        next_run_dt = trigger.get_next_fire_time(last_run_dt, last_run_dt)
        next_run = _iso(next_run_dt)
    except Exception:
        next_run = _iso(_next_run_time(job_id))

    await asyncio.to_thread(
        db.update_job_after_run,
        job_id,
        status,
        run_at,
        duration_ms,
        error,
        next_run,
        successful,
    )
    await asyncio.to_thread(
        db.add_job_run,
        job_id,
        run_at,
        status,
        exit_code,
        output,
        error,
        duration_ms,
    )


class SchedulerService:
    def __init__(self):
        self.scheduler = scheduler

    def aps_job_id(self, db_job_id: str) -> str:
        return f"shelldeck_job_{db_job_id}"

    def remove(self, db_job_id: str) -> None:
        job_id = self.aps_job_id(db_job_id)
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def next_run(self, db_job_id: str) -> datetime | None:
        job = self.scheduler.get_job(self.aps_job_id(db_job_id))
        return job.next_run_time if job else None

    async def sync_job(self, job: dict) -> None:
        db_id = job["id"]
        self.remove(db_id)

        if not job.get("enabled"):
            await asyncio.to_thread(
                db.update_job,
                db_id,
                next_run_on=None,
            )
            return

        trigger = cron_trigger(job["cron"], job.get("timezone", "UTC"))

        self.scheduler.add_job(
            execute_job,
            trigger=trigger,
            id=self.aps_job_id(db_id),
            replace_existing=True,
            kwargs={"job_id": db_id},
            misfire_grace_time=60,
        )

        now = datetime.now(timezone.utc)
        next_run_dt = trigger.get_next_fire_time(None, now)
        next_run = _iso(next_run_dt)
        await asyncio.to_thread(
            db.update_job,
            db_id,
            next_run_on=next_run,
        )

    async def sync_all(self) -> None:
        jobs = await asyncio.to_thread(db.list_jobs)
        for job in jobs:
            await self.sync_job(job)

    def run_now(self, db_job_id: str) -> None:
        self.scheduler.add_job(
            execute_job,
            kwargs={"job_id": db_job_id},
        )


scheduler_service = SchedulerService()


def _next_run_time(db_job_id: str) -> datetime | None:
    return scheduler_service.next_run(db_job_id)


async def start() -> None:
    scheduler.start()
    await scheduler_service.sync_all()


def shutdown() -> None:
    scheduler.shutdown(wait=False)
