"""Predefined workflows (WF001-WF004) and default plugin rows."""
from sqlalchemy.orm import Session

from .db import PluginConfig, Workflow
from .plugins import all_plugins

SEVERITIES = ["DOWN", "HIGH", "MEDIUM", "LOW", "STOPPED"]

PREDEFINED: list[dict] = [
    {
        "code": "WF001",
        "name": "Daily Alert Summary",
        "description": "Collect unresolved alerts, summarise them with AI and optionally email the report.",
        "params_schema": {
            "intensities": {"type": "list", "default": ["DOWN", "HIGH"], "enum": SEVERITIES,
                            "description": "Severities to include"},
            "email_to": {"type": "email_list", "description": "Recipients of the summary (optional)"},
        },
        "definition": {
            "steps": [
                {"id": "fetch", "plugin": "alerts", "action": "fetch",
                 "inputs": {"intensities": "$params.intensities"}},
                {"id": "summarize", "plugin": "ai", "action": "summarize_alerts",
                 "inputs": {"data": "$steps.fetch"}},
                {"id": "report", "plugin": "report", "action": "alert_report",
                 "inputs": {"title": "Daily Alert Summary", "summary": "$steps.summarize.summary",
                            "data": "$steps.fetch"}},
                {"id": "notify", "plugin": "email", "action": "send", "when": "params.email_to",
                 "inputs": {"to": "$params.email_to", "subject": "Daily Alert Summary - {{steps.fetch.count}} alerts",
                            "body": "$steps.report.markdown"}},
            ],
            "output": {"report": "$steps.report.markdown", "alerts": "$steps.fetch.count",
                       "critical": "$steps.fetch.critical_count"},
        },
    },
    {
        "code": "WF002",
        "name": "Ticket Creation",
        "description": "Create a ticket for each alert matching the criteria (skips alerts already ticketed).",
        "params_schema": {
            "intensities": {"type": "list", "default": ["DOWN"], "enum": SEVERITIES,
                            "description": "Severities to ticket"},
            "min_active_minutes": {"type": "integer", "default": 0, "min": 0,
                                   "description": "Only alerts active longer than N minutes"},
            "metrics": {"type": "list", "description": "Metric types, e.g. LINK, INTERFACE (optional)"},
            "priority": {"type": "string", "default": "High", "enum": ["Highest", "High", "Medium", "Low"],
                         "description": "Ticket priority"},
            "skip_existing": {"type": "boolean", "default": True,
                              "description": "Skip alerts that already have a ticket"},
        },
        "definition": {
            "steps": [
                {"id": "fetch", "plugin": "alerts", "action": "fetch",
                 "inputs": {"intensities": "$params.intensities", "metrics": "$params.metrics",
                            "min_active_minutes": "$params.min_active_minutes"},
                 "branches": [{"when": "steps.fetch.count == 0", "goto": "report"}]},
                {"id": "tickets", "plugin": "ticketing", "action": "create_for_alerts",
                 "inputs": {"alerts": "$steps.fetch.alerts", "priority": "$params.priority",
                            "skip_existing": "$params.skip_existing"}},
                {"id": "report", "plugin": "report", "action": "ticket_report",
                 "inputs": {"data": "$steps.fetch", "tickets": "$steps.tickets",
                            "criteria": "severity in {{params.intensities}}, active > {{params.min_active_minutes}} min, "
                                        "priority {{params.priority}}"}},
            ],
            "output": {"report": "$steps.report.markdown", "created": "$steps.tickets.created_count"},
        },
    },
    {
        "code": "WF003",
        "name": "Email Notification",
        "description": "Email an alert report only when a condition is met (e.g. at least N DOWN alerts).",
        "params_schema": {
            "email_to": {"type": "email_list", "required": True, "description": "Recipients"},
            "intensities": {"type": "list", "default": ["DOWN"], "enum": SEVERITIES,
                            "description": "Severities that trigger the email"},
            "min_count": {"type": "integer", "default": 1, "min": 1,
                          "description": "Send only if at least this many alerts match"},
            "subject": {"type": "string", "default": "Alert notification",
                        "description": "Email subject"},
        },
        "definition": {
            "steps": [
                {"id": "fetch", "plugin": "alerts", "action": "fetch",
                 "inputs": {"intensities": "$params.intensities"},
                 "branches": [{"when": "steps.fetch.count < params.min_count", "goto": "END"}]},
                {"id": "report", "plugin": "report", "action": "alert_report",
                 "inputs": {"title": "$params.subject", "summary": "{{steps.fetch.count}} alert(s) matched "
                            "the notification rule.", "data": "$steps.fetch"}},
                {"id": "notify", "plugin": "email", "action": "send",
                 "inputs": {"to": "$params.email_to", "subject": "$params.subject",
                            "body": "$steps.report.markdown"}},
            ],
            "output": {"matched": "$steps.fetch.count", "email": "$steps.notify.status"},
        },
    },
    {
        "code": "WF004",
        "name": "Performance Report",
        "description": "Collect link performance data for a zone, summarise it and optionally email the report.",
        "params_schema": {
            "zone": {"type": "string", "required": True, "validator": "zone",
                     "description": "Zone / region (e.g. Mangaluru, Shivamogga) or 'all'"},
            "hours": {"type": "number", "default": 6, "min": 1, "max": 720, "description": "Look-back window (hours)"},
            "email_to": {"type": "email_list", "description": "Recipients (optional)"},
        },
        "definition": {
            "steps": [
                {"id": "fetch", "plugin": "performance", "action": "fetch",
                 "inputs": {"zone": "$params.zone", "hours": "$params.hours"}},
                {"id": "summarize", "plugin": "ai", "action": "summarize_performance",
                 "inputs": {"data": "$steps.fetch"}},
                {"id": "report", "plugin": "report", "action": "performance_report",
                 "inputs": {"summary": "$steps.summarize.summary", "data": "$steps.fetch"}},
                {"id": "notify", "plugin": "email", "action": "send", "when": "params.email_to",
                 "inputs": {"to": "$params.email_to", "subject": "$steps.report.title",
                            "body": "$steps.report.markdown"}},
            ],
            "output": {"report": "$steps.report.markdown", "summary": "$steps.fetch.summary"},
        },
    },
]


def seed(db: Session) -> None:
    for wf in PREDEFINED:
        existing = db.query(Workflow).filter_by(code=wf["code"]).first()
        if existing is None:
            db.add(Workflow(kind="predefined", **wf))
        elif existing.kind == "predefined":
            # keep predefined definitions in sync with code
            existing.name, existing.description = wf["name"], wf["description"]
            existing.definition, existing.params_schema = wf["definition"], wf["params_schema"]
    for p in all_plugins():
        if db.get(PluginConfig, p.name) is None:
            db.add(PluginConfig(name=p.name, enabled=True, config={}))
    db.commit()
