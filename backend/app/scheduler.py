"""Scheduled execution (APScheduler). Schedules are persisted in SQL and re-registered on startup."""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import TIMEZONE
from .db import Schedule, SessionLocal, utcnow

log = logging.getLogger("scheduler")
TZ = ZoneInfo(TIMEZONE)
scheduler = BackgroundScheduler(timezone=TZ)

DOW = {"mon": "mon", "monday": "mon", "tue": "tue", "tuesday": "tue", "wed": "wed", "wednesday": "wed",
       "thu": "thu", "thursday": "thu", "fri": "fri", "friday": "fri", "sat": "sat", "saturday": "sat",
       "sun": "sun", "sunday": "sun", "weekday": "mon-fri", "weekdays": "mon-fri"}


def _hm(time_str: str | None) -> tuple[int, int]:
    if not time_str:
        return 9, 0
    h, _, m = time_str.partition(":")
    return int(h) % 24, int(m or 0) % 60


def spec_to_fields(spec: dict) -> dict:
    """Convert a schedule spec (from chat/UI) to Schedule columns."""
    freq = (spec.get("frequency") or "daily").lower()
    h, m = _hm(spec.get("time"))
    if freq == "daily":
        return {"frequency": "daily", "cron": f"{m} {h} * * *"}
    if freq == "weekly":
        dow = DOW.get(str(spec.get("day_of_week") or "mon").lower(), "mon")
        return {"frequency": "weekly", "cron": f"{m} {h} * * {dow}"}
    if freq == "monthly":
        dom = int(spec.get("day_of_month") or 1)
        return {"frequency": "monthly", "cron": f"{m} {h} {dom} * *"}
    if freq == "interval":
        return {"frequency": "interval", "interval_minutes": max(1, int(spec.get("interval_minutes") or 60))}
    if freq == "cron":
        CronTrigger.from_crontab(spec["cron"])  # validates
        return {"frequency": "cron", "cron": spec["cron"]}
    if freq == "once":
        run_at = spec.get("run_at")
        dt = datetime.fromisoformat(run_at) if run_at else None
        if dt is None:
            now = datetime.now(TZ)
            dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if dt <= now:
                dt = dt + timedelta(days=1)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return {"frequency": "once", "run_at": dt}
    raise ValueError(f"unsupported frequency '{freq}'")


def describe(s: Schedule) -> str:
    if s.frequency == "interval":
        return f"Every {s.interval_minutes} minutes"
    if s.frequency == "once":
        return f"Once at {s.run_at.astimezone(TZ).strftime('%Y-%m-%d %H:%M') if s.run_at else '?'}"
    parts = (s.cron or "").split()
    if len(parts) == 5:
        m, h, dom, _, dow = parts
        t = f"{int(h):02d}:{int(m):02d}" if h.isdigit() and m.isdigit() else f"{h}:{m}"
        if s.frequency == "daily":
            return f"Daily at {t}"
        if s.frequency == "weekly":
            return f"Weekly on {dow} at {t}"
        if s.frequency == "monthly":
            return f"Monthly on day {dom} at {t}"
    return f"Cron '{s.cron}'"


def _job_id(sid: int) -> str:
    return f"schedule-{sid}"


def register(s: Schedule) -> None:
    unregister(s.id)
    if not s.enabled:
        return
    if s.frequency == "interval":
        trigger = IntervalTrigger(minutes=s.interval_minutes, timezone=TZ)
    elif s.frequency == "once":
        run_at = s.run_at if s.run_at.tzinfo else s.run_at.replace(tzinfo=TZ)
        if run_at <= datetime.now(TZ):
            return
        trigger = DateTrigger(run_date=run_at, timezone=TZ)
    else:
        trigger = CronTrigger.from_crontab(s.cron, timezone=TZ)
    scheduler.add_job(run_schedule, trigger, id=_job_id(s.id), args=[s.id], replace_existing=True,
                      misfire_grace_time=3600, coalesce=True, max_instances=1)


def unregister(sid: int) -> None:
    if scheduler.get_job(_job_id(sid)):
        scheduler.remove_job(_job_id(sid))


def next_run(sid: int) -> datetime | None:
    job = scheduler.get_job(_job_id(sid))
    return job.next_run_time if job else None


def run_schedule(schedule_id: int):
    from .engine.executor import execute_workflow
    with SessionLocal() as db:
        s = db.get(Schedule, schedule_id)
        if s is None or not s.enabled:
            return None
        try:
            run = execute_workflow(db, s.workflow, s.params, trigger="schedule", schedule_id=s.id)
            log.info("schedule %s ran workflow %s -> run %s (%s)", s.id, s.workflow.code, run.id, run.status)
            return run.id
        except Exception:  # noqa: BLE001
            log.exception("scheduled run %s failed", schedule_id)
            return None
        finally:
            s.last_run_at = utcnow()
            if s.frequency == "once":
                s.enabled = False
            db.commit()


def start() -> None:
    if not scheduler.running:
        scheduler.start()
    with SessionLocal() as db:
        for s in db.query(Schedule).filter_by(enabled=True).all():
            try:
                register(s)
            except Exception:  # noqa: BLE001
                log.exception("could not register schedule %s", s.id)


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
