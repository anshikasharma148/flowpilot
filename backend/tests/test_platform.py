"""End-to-end tests (rule-based mode, temp SQLite DB)."""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["LLM_MODE"] = "rules"
# Never send real email or create real Jira issues from the test suite, whatever backend/.env contains.
os.environ["EMAIL_PROVIDER"] = "dry_run"
os.environ["JIRA_URL"] = ""
os.environ["OPS_TEAM_EMAIL"] = "ops-team@example.com"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.engine.expr import evaluate, resolve  # noqa: E402
from app.engine.validate import validate_definition  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def chat(client, msg, sid="t1"):
    r = client.post("/api/chat", json={"message": msg, "session_id": sid})
    assert r.status_code == 200, r.text
    return r.json()


# ----------------------------------------------------------------------------- engine units
def test_expressions():
    ctx = {"params": {"email_to": ["a@b.co"], "n": 2}, "steps": {"f": {"critical": [1, 2], "count": 3}}}
    assert evaluate("len(steps.f.critical) > 0 and params.email_to", ctx)
    assert evaluate("steps.f.count >= params.n", ctx) is True
    assert evaluate("steps.missing.count == 0", ctx) is False
    assert resolve("$steps.f.critical", ctx) == [1, 2]
    assert resolve("{{steps.f.count}} alerts", ctx) == "3 alerts"
    with pytest.raises(ValueError):
        evaluate("__import__('os')", ctx)


def test_definition_validation():
    errs = validate_definition({"steps": [
        {"id": "a", "plugin": "alerts", "action": "fetch", "inputs": {"x": "$steps.b.y"}},
        {"id": "b", "plugin": "nope", "action": "x"}]})
    assert any("does not run before" in e for e in errs)
    assert any("unknown plugin" in e for e in errs)


# ----------------------------------------------------------------------------- assignment prompts
def test_prompt1_dynamic_scheduled(client):
    res = chat(client, "Every day at 9 AM, check all unresolved down and high operational alerts, generate a "
                       "summary, create a ticket for each critical incident and email the report to the "
                       "operations team.")
    assert res["status"] == "scheduled", res
    agents = [t["agent"] for t in res["trace"]]
    assert "Workflow Generation Agent" in agents
    sched = client.get("/api/schedules").json()[0]
    assert sched["cron"] == "0 9 * * *" and sched["frequency"] == "daily"
    # run the schedule now to verify the generated workflow works end to end
    run = client.post(f"/api/schedules/{sched['id']}/run").json()
    assert run["status"] == "success", run
    steps = {s["step_id"]: s for s in run["steps"]}
    assert steps["tickets"]["status"] == "success"
    # 4 DOWN alerts; 2 already have tickets in the feed -> 2 created
    assert steps["tickets"]["output"]["created_count"] == 2
    assert steps["notify"]["output"]["status"] == "dry_run"
    # running again must not duplicate tickets
    run2 = client.post(f"/api/schedules/{sched['id']}/run").json()
    assert {s["step_id"]: s for s in run2["steps"]}["tickets"]["output"]["created_count"] == 0
    # asking for the same schedule again must not create a duplicate schedule
    again = chat(client, "Every day at 9 AM, check all unresolved down and high operational alerts, generate a "
                         "summary, create a ticket for each critical incident and email the report to the "
                         "operations team.", sid="t1b")
    assert again["schedule_id"] == sched["id"] and "already scheduled" in again["reply"]
    assert len(client.get("/api/schedules").json()) == 1


def test_prompt2_ticket_creation(client):
    res = chat(client, "Create a high-priority Jira ticket for all devices that have been down for more than "
                       "15 minutes.", sid="t2")
    assert res["status"] == "executed" and res["workflow_code"] == "WF002", res
    run = client.get(f"/api/runs/{res['run_id']}").json()
    assert run["params"]["priority"] == "High"
    assert run["params"]["intensities"] == ["DOWN"]
    assert run["params"]["min_active_minutes"] == 15


def test_prompt3_unknown_zone_then_followup(client):
    res = chat(client, "Generate the performance report for Mumbai for last 6 hours and email it to "
                       "test@gmail.com", sid="t3")
    assert res["status"] == "needs_input", res
    assert "Mumbai" in res["reply"] and "Mangaluru" in res["reply"]
    res2 = chat(client, "Mangaluru", sid="t3")
    assert res2["status"] == "executed", res2
    run = client.get(f"/api/runs/{res2['run_id']}").json()
    assert run["status"] == "success"
    assert run["params"]["zone"] == "Mangaluru" and run["params"]["hours"] == 6
    assert run["params"]["email_to"] == ["test@gmail.com"]
    emails = client.get("/api/emails").json()
    assert any(e["to"] == "test@gmail.com" for e in emails)


def test_conditional_email_skips_when_not_met(client):
    r = client.post("/api/workflows/WF003/run", json={"params": {"email_to": "x@y.com", "min_count": 99}}).json()
    statuses = [s["step_id"] for s in r["steps"]]
    assert statuses == ["fetch"]  # branch to END - no email sent
    assert r["status"] == "success"


def test_manual_run_validation_error(client):
    r = client.post("/api/workflows/WF003/run", json={"params": {}})
    assert r.status_code == 422


def test_custom_workflow_and_schedule(client):
    body = {"name": "Load alerts digest", "description": "custom", "params_schema": {},
            "definition": {"steps": [
                {"id": "fetch", "plugin": "alerts", "action": "fetch", "inputs": {"metrics": ["LOAD"]}},
                {"id": "report", "plugin": "report", "action": "alert_report",
                 "inputs": {"data": "$steps.fetch", "summary": "{{steps.fetch.count}} load alerts"}}],
                "output": {"report": "$steps.report.markdown"}}}
    w = client.post("/api/workflows", json=body).json()
    assert w["kind"] == "custom"
    s = client.post("/api/schedules", json={"workflow_code": w["code"], "frequency": "interval",
                                            "interval_minutes": 30}).json()
    assert s["next_run_at"] is not None
    assert client.patch(f"/api/schedules/{s['id']}", json={"enabled": False}).json()["next_run_at"] is None


def test_help(client):
    res = chat(client, "what workflows are available?", sid="t9")
    assert "WF004" in res["reply"]


# ----------------------------------------------------------------------------- monitoring & management via chat
def test_monitoring_questions(client):
    chat(client, "Summarise all down alerts", sid="m1")
    last = chat(client, "What's the status of my last run?", sid="m1")
    assert last["status"] == "answered" and "Run #" in last["reply"] and last["run_id"]
    assert any(t["agent"] == "Monitoring Agent" for t in last["trace"])
    one = chat(client, f"show run #{last['run_id']} details", sid="m1")
    assert f"Run #{last['run_id']}" in one["reply"]
    assert "schedule" in chat(client, "Show my schedules", sid="m1")["reply"].lower()
    assert "ticket" in chat(client, "How many tickets were created?", sid="m1")["reply"].lower()
    assert "active alert" in chat(client, "How many active alerts are there?", sid="m1")["reply"].lower()
    assert "overview" in chat(client, "Give me an overview", sid="m1")["reply"].lower()
    assert chat(client, "show failed runs", sid="m1")["status"] == "answered"


def test_manage_schedule_via_chat(client):
    s = chat(client, "Every Monday at 8 AM send the performance report for Shivamogga to noc@example.com", sid="m2")
    sid = s["schedule_id"]
    assert s["status"] == "scheduled"
    r = chat(client, f"pause schedule {sid}", sid="m2")
    assert "Paused" in r["reply"]
    assert not next(x for x in client.get("/api/schedules").json() if x["id"] == sid)["enabled"]
    assert "Resumed" in chat(client, f"resume schedule {sid}", sid="m2")["reply"]
    ran = chat(client, f"run schedule {sid} now", sid="m2")
    assert ran["run_id"] and "SUCCESS" in ran["reply"]
    assert "Deleted" in chat(client, f"delete schedule {sid}", sid="m2")["reply"]
    assert all(x["id"] != sid for x in client.get("/api/schedules").json())


def test_define_workflow_then_run_by_code(client):
    runs_before = len(client.get("/api/runs").json())
    d = chat(client, "Create a workflow that fetches LINK alerts and emails the report to noc@example.com", sid="m3")
    assert d["status"] == "defined", d
    code = d["workflow_code"]
    assert "not run" in d["reply"] and len(client.get("/api/runs").json()) == runs_before  # nothing executed
    wf = client.get(f"/api/workflows/{code}").json()
    assert wf["kind"] == "dynamic" and wf["params_schema"]["metrics"]["default"] == ["LINK"]
    r = chat(client, f"run {code}", sid="m3")
    assert r["status"] == "executed" and r["workflow_code"] == code
    run = client.get(f"/api/runs/{r['run_id']}").json()
    assert run["status"] == "success"
    fetch = next(s for s in run["steps"] if s["step_id"] == "fetch")
    assert fetch["output"]["count"] == 2  # two LINK alerts in the feed


def test_validation_rejects_bad_generated_designs():
    # the exact design an LLM once produced: an email step without inputs + a duplicate email step
    bad = {"steps": [
        {"id": "fetch", "plugin": "alerts", "action": "fetch", "inputs": {"metrics": ["$params.metrics"]}},
        {"id": "report", "plugin": "report", "action": "alert_report", "inputs": {"data": "$steps.fetch"}},
        {"id": "notify", "plugin": "email", "action": "send", "inputs": {}, "when": "params.email_to"},
        {"id": "notify2", "plugin": "email", "action": "send",
         "inputs": {"to": "$params.email_to", "body": "$steps.report.markdown"}}]}
    errs = validate_definition(bad)
    assert any("missing required input" in e for e in errs)
    assert any("duplicate email.send" in e for e in errs)
    # nested list reference is flattened at runtime
    assert resolve(["$params.m"], {"params": {"m": ["LINK", "LOAD"]}}) == ["LINK", "LOAD"]


# ----------------------------------------------------------------------------- real integrations (mocked transport)
import httpx  # noqa: E402

from app import config  # noqa: E402
from app.plugins import mailer, ticketing  # noqa: E402


def _jira_transport(calls, reject_fields=()):
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.content or b"{}")
        calls.append({"path": request.url.path, "fields": body.get("fields", {}),
                      "auth": request.headers.get("authorization", "")})
        bad = {f: f"Field '{f}' cannot be set." for f in reject_fields if f in body.get("fields", {})}
        if bad:
            return httpx.Response(400, json={"errorMessages": [], "errors": bad})
        return httpx.Response(201, json={"id": "10001", "key": f"OPS-{len(calls)}"})
    return httpx.MockTransport(handler)


def test_jira_issue_created_with_fallback_for_rejected_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "JIRA_URL", "https://example.atlassian.net")
    monkeypatch.setattr(config, "JIRA_EMAIL", "me@example.com")
    monkeypatch.setattr(config, "JIRA_API_TOKEN", "token")
    monkeypatch.setattr(ticketing, "_client", lambda: httpx.Client(
        base_url=config.JIRA_URL, auth=("me@example.com", "token"),
        transport=_jira_transport(calls, reject_fields=("priority",))))
    res = ticketing.create_issue("[DOWN] LINK on dev-1", "details", "High", ["flowpilot"])
    assert res["key"].startswith("OPS-") and res["url"].endswith("/browse/" + res["key"])
    assert res["dropped_fields"] == ["priority"]
    assert calls[0]["path"] == "/rest/api/2/issue" and calls[0]["auth"].startswith("Basic ")
    assert "priority" in calls[0]["fields"] and "priority" not in calls[1]["fields"]
    assert calls[1]["fields"]["description"].startswith("Priority: High")


def test_ticket_plugin_uses_jira_and_continues_after_a_failure(monkeypatch):
    monkeypatch.setattr(config, "JIRA_URL", "https://example.atlassian.net")
    monkeypatch.setattr(config, "JIRA_EMAIL", "me@example.com")
    monkeypatch.setattr(config, "JIRA_API_TOKEN", "token")
    n = {"i": 0}

    def fake_create(summary, description, priority, labels):
        n["i"] += 1
        if n["i"] == 1:
            raise ticketing.JiraError("Jira 500: boom")
        return {"key": f"OPS-{n['i']}", "url": f"https://example.atlassian.net/browse/OPS-{n['i']}",
                "dropped_fields": []}
    monkeypatch.setattr(ticketing, "create_issue", fake_create)
    from app.db import SessionLocal
    from app.plugins.base import PluginContext
    alerts = [{"uid": f"u-jira-{i}", "device": f"dev-{i}", "intensity": "DOWN", "metric": "LINK"} for i in range(3)]
    with SessionLocal() as db:
        out = ticketing.TicketingPlugin().create_for_alerts(PluginContext(db=db), alerts=alerts)
    assert out["created_count"] == 2 and len(out["failed"]) == 1
    assert all(t["system"] == "jira" and t["url"].startswith("https://") for t in out["created"])


def test_email_brevo_api_payload(monkeypatch):
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, json=json)
        return httpx.Response(201, json={"messageId": "<abc@smtp-relay.brevo.com>"},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "brevo")
    monkeypatch.setattr(config, "BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setattr(config, "EMAIL_FROM", "sender@example.com")
    monkeypatch.setattr(mailer.httpx, "post", fake_post)
    prov, msg_id = mailer.deliver(["a@b.com"], "Subject", "| a | b |\n|---|---|\n| 1 | 2 |")
    assert prov == "brevo" and msg_id.startswith("<abc")
    assert sent["url"] == mailer.BREVO_URL and sent["headers"]["api-key"] == "xkeysib-test"
    assert sent["json"]["to"] == [{"email": "a@b.com"}] and "<table" in sent["json"]["htmlContent"]


def test_email_smtp_send(monkeypatch):
    log = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            log["conn"] = (host, port)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self):
            log["tls"] = True
        def login(self, user, pw):
            log["login"] = user
        def sendmail(self, frm, to, msg):
            log.update(frm=frm, to=to, msg=msg)
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    monkeypatch.setattr(config, "SMTP_USER", "me@gmail.com")
    monkeypatch.setattr(config, "EMAIL_FROM", "me@gmail.com")
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    prov, _ = mailer.deliver(["x@y.com"], "Hello", "body")
    assert prov == "smtp" and log["conn"] == ("smtp.gmail.com", 587) and log["tls"] and log["login"] == "me@gmail.com"
    assert log["to"] == ["x@y.com"] and "Subject: Hello" in log["msg"]


def test_email_fails_loudly_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "auto")
    monkeypatch.setattr(config, "BREVO_API_KEY", "")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    r = client.post("/api/workflows/WF003/run", json={"params": {"email_to": "a@b.com", "min_count": 1}}).json()
    notify = next(s for s in r["steps"] if s["step_id"] == "notify")
    assert notify["status"] == "failed" and "No email provider is configured" in notify["error"]
    assert r["status"] == "failed"


# ----------------------------------------------------------------------------- reports & mandatory parameters
def test_performance_report_lists_every_down_link(client):
    r = client.post("/api/workflows/WF004/run", json={"params": {"zone": "Shivamogga"}}).json()
    report = r["output"]["report"]
    down_section = report.split("## Links down")[1].split("## Degraded")[0]
    import re as _re
    assert "(30)" in down_section.splitlines()[0]
    assert len([ln for ln in down_section.splitlines() if _re.match(r"^\| \d+ \|", ln)]) == 30


def test_zone_is_mandatory_for_performance_report(client):
    res = chat(client, "Generate the performance report for last 6 hours", sid="z1")
    assert res["status"] == "needs_input" and "zone" in res["reply"]
    assert chat(client, "Mangaluru", sid="z1")["status"] == "executed"


def test_email_recipient_required_when_email_is_requested(client):
    res = chat(client, "Generate the performance report for Mangaluru and email it", sid="e1")
    assert res["status"] == "needs_input" and "email_to" in res["reply"]
    done = chat(client, "noc@example.com", sid="e1")
    assert done["status"] == "executed"
    run = client.get(f"/api/runs/{done['run_id']}").json()
    assert run["params"]["email_to"] == ["noc@example.com"]
