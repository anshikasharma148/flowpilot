"""FastAPI application - REST API for chat, workflows, runs, schedules, plugins and integrations."""
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, llm, scheduler
from .agents.graph import handle_message
from .data_sources import available_zones, load_alerts
from .db import (ChatMessage, EmailLog, PluginConfig, Schedule, SessionLocal, StepRun, Ticket, Workflow,
                 WorkflowRun, init_db)
from .engine.executor import ParamValidationError, execute_workflow
from .engine.validate import validate_definition
from .plugins import all_plugins, mailer, ticketing
from .plugins.alerts import normalize
from .seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        seed(db)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Agentic Workflow Automation", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ----------------------------------------------------------------------------- serializers
def wf_out(w: Workflow) -> dict:
    definition = {k: v for k, v in (w.definition or {}).items() if not k.startswith("_")}
    return {"id": w.id, "code": w.code, "name": w.name, "description": w.description, "kind": w.kind,
            "definition": definition, "params_schema": w.params_schema, "is_active": w.is_active,
            "version": w.version, "created_at": w.created_at, "updated_at": w.updated_at}


def step_out(s: StepRun) -> dict:
    return {"id": s.id, "step_id": s.step_id, "plugin": s.plugin, "action": s.action, "status": s.status,
            "inputs": s.inputs, "output": s.output, "error": s.error, "started_at": s.started_at,
            "finished_at": s.finished_at}


def run_out(r: WorkflowRun, full: bool = False) -> dict:
    d = {"id": r.id, "workflow_code": r.workflow.code, "workflow_name": r.workflow.name, "trigger": r.trigger,
         "schedule_id": r.schedule_id, "params": r.params, "status": r.status, "error": r.error,
         "started_at": r.started_at, "finished_at": r.finished_at,
         "duration_ms": (r.finished_at - r.started_at).total_seconds() * 1000 if r.finished_at else None}
    if full:
        d.update(output=r.output, report=r.report, steps=[step_out(s) for s in r.steps])
    return d


def sched_out(s: Schedule) -> dict:
    nxt = scheduler.next_run(s.id)
    return {"id": s.id, "name": s.name, "workflow_code": s.workflow.code, "workflow_name": s.workflow.name,
            "frequency": s.frequency, "cron": s.cron, "run_at": s.run_at, "interval_minutes": s.interval_minutes,
            "description": scheduler.describe(s), "params": s.params, "enabled": s.enabled,
            "created_from": s.created_from, "last_run_at": s.last_run_at, "next_run_at": nxt,
            "created_at": s.created_at}


# ----------------------------------------------------------------------------- health / dashboard
def integrations_status() -> dict:
    return {"email": {**mailer.status(), "ops_team_email": config.OPS_TEAM_EMAIL or None},
            "jira": {"configured": ticketing.jira_enabled(), "url": config.JIRA_URL or None,
                     "project": config.JIRA_PROJECT_KEY, "issue_type": config.JIRA_ISSUE_TYPE}}


@app.get("/api/health")
def health():
    return {"status": "ok", "llm": llm.status(), "timezone": scheduler.TZ.key,
            "scheduler_running": scheduler.scheduler.running, "integrations": integrations_status()}


@app.get("/api/integrations")
def integrations():
    return integrations_status()


class TestEmailIn(BaseModel):
    to: str = Field(min_length=3, max_length=300)


@app.post("/api/integrations/email/test")
def test_email(body: TestEmailIn):
    """Send a real test email through the configured provider."""
    recipients = mailer._recipients(body.to)
    bad = [r for r in recipients if not mailer.EMAIL_RE.match(r)]
    if not recipients or bad:
        raise HTTPException(422, f"invalid email address(es): {', '.join(bad) or body.to}")
    try:
        prov, msg_id = mailer.deliver(recipients, "FlowPilot test email",
                                      "# FlowPilot test email\n\nIf you can read this, email delivery works.")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "provider": mailer.provider(), "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "provider": prov, "message_id": msg_id, "to": recipients}


@app.post("/api/integrations/jira/test")
def test_jira():
    """Check Jira credentials, project access and issue type (creates nothing)."""
    try:
        return ticketing.check_connection()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    by_status = dict(db.query(WorkflowRun.status, func.count()).group_by(WorkflowRun.status).all())
    by_trigger = dict(db.query(WorkflowRun.trigger, func.count()).group_by(WorkflowRun.trigger).all())
    return {
        "workflows": db.query(Workflow).filter_by(is_active=True).count(),
        "runs": sum(by_status.values()), "runs_by_status": by_status, "runs_by_trigger": by_trigger,
        "schedules_active": db.query(Schedule).filter_by(enabled=True).count(),
        "tickets": db.query(Ticket).count(), "emails": db.query(EmailLog).count(),
        "alerts": len(load_alerts()), "zones": available_zones(),
    }


# ----------------------------------------------------------------------------- chat
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(None, max_length=64)


@app.post("/api/chat")
def chat(body: ChatIn, db: Session = Depends(get_db)):
    sid = body.session_id or uuid.uuid4().hex
    result = handle_message(db, sid, body.message)
    return {"session_id": sid, **result}


@app.get("/api/chat/{session_id}")
def chat_history(session_id: str, db: Session = Depends(get_db)):
    msgs = db.query(ChatMessage).filter_by(session_id=session_id).order_by(ChatMessage.id).all()
    return [{"id": m.id, "role": m.role, "content": m.content, "meta": m.meta, "created_at": m.created_at}
            for m in msgs]


# ----------------------------------------------------------------------------- workflows
class WorkflowIn(BaseModel):
    name: str
    description: str = ""
    definition: dict
    params_schema: dict = Field(default_factory=dict)
    code: str | None = None


class RunIn(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


@app.get("/api/workflows")
def list_workflows(db: Session = Depends(get_db)):
    return [wf_out(w) for w in db.query(Workflow).filter_by(is_active=True).order_by(Workflow.code).all()]


def _get_wf(db: Session, code: str) -> Workflow:
    w = db.query(Workflow).filter_by(code=code.upper()).first()
    if not w:
        raise HTTPException(404, f"workflow {code} not found")
    return w


@app.get("/api/workflows/{code}")
def get_workflow(code: str, db: Session = Depends(get_db)):
    return wf_out(_get_wf(db, code))


@app.post("/api/workflows/validate")
def validate_wf(body: WorkflowIn):
    return {"errors": validate_definition(body.definition)}


@app.post("/api/workflows", status_code=201)
def create_workflow(body: WorkflowIn, db: Session = Depends(get_db)):
    errors = validate_definition(body.definition)
    if errors:
        raise HTTPException(422, {"errors": errors})
    if body.code:
        code = body.code.upper()
        if not re.fullmatch(r"[A-Z0-9_-]{2,20}", code):
            raise HTTPException(422, "code must be 2-20 chars A-Z, 0-9, _ or -")
        if db.query(Workflow).filter_by(code=code).first():
            raise HTTPException(409, f"workflow {code} already exists")
    else:
        nums = [int(w.code[2:]) for w in db.query(Workflow).all() if w.code[:2] == "WF" and w.code[2:].isdigit()]
        code = f"WF{max(nums + [4]) + 1:03d}"
    w = Workflow(code=code, name=body.name, description=body.description, kind="custom",
                 definition=body.definition, params_schema=body.params_schema)
    db.add(w)
    db.commit()
    return wf_out(w)


@app.put("/api/workflows/{code}")
def update_workflow(code: str, body: WorkflowIn, db: Session = Depends(get_db)):
    w = _get_wf(db, code)
    if w.kind == "predefined":
        raise HTTPException(403, "predefined workflows are read-only; create a custom copy instead")
    errors = validate_definition(body.definition)
    if errors:
        raise HTTPException(422, {"errors": errors})
    w.name, w.description, w.definition, w.params_schema = body.name, body.description, body.definition, body.params_schema
    w.version += 1
    db.commit()
    return wf_out(w)


@app.delete("/api/workflows/{code}", status_code=204)
def delete_workflow(code: str, db: Session = Depends(get_db)):
    w = _get_wf(db, code)
    if w.kind == "predefined":
        raise HTTPException(403, "predefined workflows cannot be deleted")
    w.is_active = False
    for s in db.query(Schedule).filter_by(workflow_id=w.id).all():
        s.enabled = False
        scheduler.unregister(s.id)
    db.commit()


@app.post("/api/workflows/{code}/run")
def run_workflow(code: str, body: RunIn, db: Session = Depends(get_db)):
    w = _get_wf(db, code)
    try:
        run = execute_workflow(db, w, body.params, trigger="manual")
    except ParamValidationError as e:
        raise HTTPException(422, {"missing": e.missing, "errors": e.errors}) from e
    return run_out(run, full=True)


# ----------------------------------------------------------------------------- runs
@app.get("/api/runs")
def list_runs(limit: int = 50, workflow: str | None = None, status: str | None = None,
              db: Session = Depends(get_db)):
    q = db.query(WorkflowRun).order_by(WorkflowRun.id.desc())
    if workflow:
        q = q.join(Workflow).filter(Workflow.code == workflow.upper())
    if status:
        q = q.filter(WorkflowRun.status == status)
    return [run_out(r) for r in q.limit(min(limit, 500)).all()]


@app.get("/api/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(WorkflowRun, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return run_out(r, full=True)


# ----------------------------------------------------------------------------- schedules
class ScheduleIn(BaseModel):
    workflow_code: str
    name: str | None = None
    frequency: str = "daily"   # once | daily | weekly | monthly | interval | cron
    time: str | None = "09:00"
    day_of_week: str | None = None
    day_of_month: int | None = None
    interval_minutes: int | None = None
    run_at: str | None = None
    cron: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class SchedulePatch(BaseModel):
    enabled: bool


@app.get("/api/schedules")
def list_schedules(db: Session = Depends(get_db)):
    return [sched_out(s) for s in db.query(Schedule).order_by(Schedule.id.desc()).all()]


@app.post("/api/schedules", status_code=201)
def create_schedule(body: ScheduleIn, db: Session = Depends(get_db)):
    from .engine.params import validate_params
    w = _get_wf(db, body.workflow_code)
    pr = validate_params(w.params_schema, body.params)
    if not pr.ok:
        raise HTTPException(422, {"missing": pr.missing, "errors": pr.errors})
    try:
        fields = scheduler.spec_to_fields(body.model_dump())
    except (ValueError, KeyError) as e:
        raise HTTPException(422, f"invalid schedule: {e}") from e
    s = Schedule(workflow_id=w.id, name=body.name or f"{w.name} ({body.frequency})", params=pr.values,
                 created_from="ui", **fields)
    db.add(s)
    db.commit()
    scheduler.register(s)
    return sched_out(s)


@app.patch("/api/schedules/{sid}")
def patch_schedule(sid: int, body: SchedulePatch, db: Session = Depends(get_db)):
    s = db.get(Schedule, sid)
    if not s:
        raise HTTPException(404, "schedule not found")
    s.enabled = body.enabled
    db.commit()
    scheduler.register(s) if s.enabled else scheduler.unregister(s.id)
    return sched_out(s)


@app.delete("/api/schedules/{sid}", status_code=204)
def delete_schedule(sid: int, db: Session = Depends(get_db)):
    s = db.get(Schedule, sid)
    if not s:
        raise HTTPException(404, "schedule not found")
    scheduler.unregister(sid)
    db.query(WorkflowRun).filter_by(schedule_id=sid).update({"schedule_id": None})
    db.delete(s)
    db.commit()


@app.post("/api/schedules/{sid}/run")
def run_schedule_now(sid: int, db: Session = Depends(get_db)):
    s = db.get(Schedule, sid)
    if not s:
        raise HTTPException(404, "schedule not found")
    run = execute_workflow(db, s.workflow, s.params, trigger="schedule", schedule_id=s.id)
    s.last_run_at = run.started_at
    db.commit()
    return run_out(run, full=True)


# ----------------------------------------------------------------------------- plugins & integrations
@app.get("/api/plugins")
def list_plugins(db: Session = Depends(get_db)):
    cfg = {p.name: p for p in db.query(PluginConfig).all()}
    return [p.describe() | {"enabled": cfg[p.name].enabled if p.name in cfg else True} for p in all_plugins()]


@app.patch("/api/plugins/{name}")
def toggle_plugin(name: str, body: SchedulePatch, db: Session = Depends(get_db)):
    p = db.get(PluginConfig, name)
    if not p:
        raise HTTPException(404, "plugin not found")
    p.enabled = body.enabled
    db.commit()
    return {"name": name, "enabled": p.enabled}


@app.get("/api/tickets")
def list_tickets(db: Session = Depends(get_db)):
    return [{"id": t.id, "key": t.key, "system": t.system, "title": t.title, "description": t.description,
             "priority": t.priority, "status": t.status, "device": t.device, "run_id": t.run_id, "url": t.url,
             "created_at": t.created_at} for t in db.query(Ticket).order_by(Ticket.id.desc()).all()]


@app.get("/api/emails")
def list_emails(db: Session = Depends(get_db)):
    return [{"id": e.id, "to": e.to, "subject": e.subject, "body": e.body, "status": e.status, "error": e.error,
             "provider": e.provider, "message_id": e.message_id,
             "run_id": e.run_id, "created_at": e.created_at}
            for e in db.query(EmailLog).order_by(EmailLog.id.desc()).all()]


@app.get("/api/data/alerts")
def data_alerts():
    return [normalize(a) for a in load_alerts()]


@app.get("/api/data/zones")
def data_zones():
    return available_zones()


# ----------------------------------------------------------------------------- web UI (single-service deployment)
# When the React build is present (STATIC_DIR, set in the Dockerfile), serve it from the same server,
# so one URL hosts both the UI and the API. Mounted last so every /api route takes precedence.
_static = os.getenv("STATIC_DIR", "")
if _static and os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="ui")
