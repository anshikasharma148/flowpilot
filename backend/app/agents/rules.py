"""Deterministic natural-language understanding - used when no LLM is configured
(or as a fallback when an LLM call fails). Covers the assignment's sample prompts
and common variations."""
import re

from .. import config
from ..data_sources import available_zones

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
ZONE_STOP = {"last", "all", "each", "every", "the", "past", "more", "today", "tomorrow", "devices", "me",
             "alerts", "zone", "region", "a", "an"}


# --------------------------------------------------------------------------- schedule
def _time(t: str) -> str | None:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", t)
    if m:
        h, mi = int(m.group(1)) % 12, int(m.group(2) or 0)
        if m.group(3).startswith("p"):
            h += 12
        return f"{h:02d}:{mi:02d}"
    m = re.search(r"\bat\s+(\d{1,2}):(\d{2})\b", t)
    if m:
        return f"{int(m.group(1)) % 24:02d}:{int(m.group(2)):02d}"
    if "noon" in t:
        return "12:00"
    if "midnight" in t:
        return "00:00"
    return None


def parse_schedule(text: str) -> dict | None:
    t = text.lower()
    m = re.search(r"every\s+(\d+)\s*(minute|min|hour|hr)s?\b", t)
    if m:
        n = int(m.group(1)) * (60 if m.group(2).startswith("h") else 1)
        return {"frequency": "interval", "interval_minutes": n}
    if re.search(r"\bevery\s+hour\b|\bhourly\b", t):
        return {"frequency": "interval", "interval_minutes": 60}
    time = _time(t)
    if re.search(r"\b(every\s*day|daily|each\s+day|every\s+(morning|evening|night))\b", t):
        if not time:
            time = "18:00" if re.search(r"evening|night", t) else "09:00"
        return {"frequency": "daily", "time": time}
    for d in WEEKDAYS:
        if re.search(rf"\b(every|each|on)\s+{d}s?\b", t):
            return {"frequency": "weekly", "day_of_week": d[:3], "time": time or "09:00"}
    if re.search(r"\b(weekly|every\s+week)\b", t):
        return {"frequency": "weekly", "day_of_week": "mon", "time": time or "09:00"}
    if re.search(r"\b(monthly|every\s+month)\b", t):
        m = re.search(r"\b(\d{1,2})(st|nd|rd|th)\b", t)
        return {"frequency": "monthly", "day_of_month": int(m.group(1)) if m else 1, "time": time or "09:00"}
    if time and re.search(r"\b(today|tonight|tomorrow|schedule|later)\b", t):
        return {"frequency": "once", "time": time}
    return None


# --------------------------------------------------------------------------- params
def parse_params(text: str) -> dict:
    t = text.lower()
    p: dict = {}

    emails = EMAIL_RE.findall(text)
    if re.search(r"\b(ops|operations|noc)\s+team\b", t) and config.OPS_TEAM_EMAIL:
        emails.append(config.OPS_TEAM_EMAIL)
    if emails:
        p["email_to"] = list(dict.fromkeys(e.rstrip(".") for e in emails))

    m = re.search(r"\b(highest|high|medium|low|critical|urgent)[\s-]+priority\b", t)
    if m:
        p["priority"] = {"critical": "Highest", "urgent": "Highest"}.get(m.group(1), m.group(1).title())
        t = t.replace(m.group(0), " ")
    elif re.search(r"\bpriority\s+(highest|high|medium|low)\b", t):
        p["priority"] = re.search(r"\bpriority\s+(highest|high|medium|low)\b", t).group(1).title()

    m = re.search(r"\b(?:last|past|previous)\s+(\d+)?\s*(hour|hr|h|day|d|week)s?\b", t)
    if m:
        n = int(m.group(1) or 1)
        unit = m.group(2)
        p["hours"] = n * (24 if unit.startswith("d") else 168 if unit == "week" else 1)

    m = re.search(r"\b(?:more than|over|longer than|at least|exceeding|>)\s*(\d+)\s*(minute|min|hour|hr|h)s?\b", t)
    if m:
        p["min_active_minutes"] = int(m.group(1)) * (60 if m.group(2).startswith("h") else 1)

    sev = []
    for word, val in (("down", "DOWN"), ("critical", "DOWN"), ("high", "HIGH"), ("medium", "MEDIUM"),
                      ("stopped", "STOPPED"), ("low", "LOW")):
        if re.search(rf"\b{word}\b", t) and val not in sev:
            sev.append(val)
    if sev:
        p["intensities"] = sev

    metrics = [m.upper() for m in ("link", "interface", "latency", "snmp", "load") if re.search(rf"\b{m}s?\b", t)]
    if metrics and re.search(r"\b(ticket|alerts?)\b", t) and not re.search(r"\bperformance\b", t):
        p["metrics"] = metrics

    m = re.search(r"\bat least\s+(\d+)\s+(?:\w+\s+)?alerts?\b", t)
    if m:
        p["min_count"] = int(m.group(1))

    zones = available_zones()
    for z in zones:
        if re.search(rf"\b{z.lower()}\b", t):
            p["zone"] = z
            break
    else:
        if re.search(r"\b(all|every)\s+(zones|regions)\b", t):
            p["zone"] = "all"
        else:
            for m in re.finditer(r"\b(?:for|in|of)\s+(?:the\s+)?([A-Za-z][A-Za-z]+)(?:\s+(?:zone|region))?", text):
                cand = m.group(1)
                if cand.lower() not in ZONE_STOP and cand[0].isupper():
                    p["zone"] = cand
                    break
    return p


# --------------------------------------------------------------------------- intents
def detect_intents(text: str) -> dict:
    t = text.lower()
    return {
        "help": bool(re.search(r"\b(help|what can you do|list (the )?workflows|show (me )?(the )?workflows|"
                               r"available workflows|which workflows|what workflows)\b", t)
                     or re.search(r"\bworkflows?\b.*\b(available|exist|supported|do you have)\b", t)),
        "performance": bool(re.search(r"\b(performance|uptime|packet drop|jitter|link health)\b", t)),
        "ticket": bool(re.search(r"\b(ticket|tickets|jira|incident ticket|raise an? incident)\b", t)),
        "email": bool(EMAIL_RE.search(text) or re.search(r"\b(e-?mail|mail|notify|send)\b", t)),
        "summary": bool(re.search(r"\b(summar\w*|digest|overview|check|review)\b", t)
                        or re.search(r"\breport\b", t)),
        "alerts": bool(re.search(r"\b(alerts?|down|devices?|links?|outages?)\b", t)),
        "conditional": bool(re.search(r"\b(if|when|only if|whenever)\b", t)),
    }


def detect_command(text: str) -> dict | None:
    """Monitoring questions, schedule management, workflow definition and 'run WF00X'."""
    t = text.lower().strip()
    num = lambda m: int(m.group(1)) if m and m.group(1) else None  # noqa: E731

    # schedule management: "pause schedule 1", "resume schedule #2", "run schedule 1 now", "delete schedule 3"
    m = re.search(r"\b(pause|disable|stop|suspend|resume|enable|unpause|restart|delete|remove|cancel)\b"
                  r"[^0-9]*\bschedules?\b\s*#?(\d+)?", t) or \
        re.search(r"\b(pause|disable|stop|suspend|resume|enable|unpause|delete|remove|cancel)\b\s+#?(\d+)", t)
    if m:
        verb = m.group(1)
        op = ("pause" if verb in ("pause", "disable", "stop", "suspend") else
              "resume" if verb in ("resume", "enable", "unpause", "restart") else "delete")
        return {"action": "manage_schedule", "schedule_op": op,
                "target_id": int(m.group(2)) if m.group(2) else None,
                "reasoning": f"User wants to {op} a schedule."}
    m = re.search(r"\b(?:run|trigger|execute|start)\s+schedule\s*#?(\d+)", t)
    if m:
        return {"action": "manage_schedule", "schedule_op": "run_now", "target_id": int(m.group(1)),
                "reasoning": "User wants to run a schedule immediately."}

    # define (create & save, don't run): "create a workflow that ..."
    if re.search(r"\b(create|define|build|make|add|design|save|set up)\b.*\bworkflows?\b", t) and \
            not re.search(r"\bworkflows?\b.*\b(available|exist)\b", t):
        return {"action": "define_workflow", "reasoning": "User wants to create (save) a new workflow."}

    # run a workflow by code: "run WF005", "execute workflow WF002"
    m = re.search(r"\b(?:run|execute|trigger|start)\s+(?:the\s+)?(?:workflow\s+)?(wf\d{3})\b", t)
    if m:
        return {"action": "run_existing", "workflow_code": m.group(1).upper(),
                "reasoning": f"User asked to run {m.group(1).upper()} explicitly."}

    # monitoring questions
    m = re.search(r"\brun\s*#\s*(\d+)\b|\brun\s+(?:number\s+)?(\d+)\b", t)
    if m and re.search(r"\b(status|show|what|how|details?|happened|result)\b", t):
        return {"action": "monitor", "monitor_topic": "run", "target_id": int(m.group(1) or m.group(2)),
                "reasoning": "User asked about a specific run."}
    if re.search(r"\b(fail(ed|ures?)?|errors?|partial)\b.*\bruns?\b|\bruns?\b.*\bfail", t):
        return {"action": "monitor", "monitor_topic": "failed_runs", "reasoning": "User asked about failed runs."}
    if re.search(r"\b(last|latest|previous|most recent)\s+(run|execution|workflow run)\b", t) or \
            re.search(r"\bstatus of (my|the) (last|latest)\b", t) or re.search(r"\bwhat happened\b", t):
        return {"action": "monitor", "monitor_topic": "last_run", "reasoning": "User asked about the last run."}
    if re.search(r"\b(recent|today'?s?|all|show|list)\b.*\bruns\b|\bruns\b.*\btoday\b|\bhow many runs\b", t):
        return {"action": "monitor", "monitor_topic": "recent_runs", "reasoning": "User asked about recent runs."}
    if re.search(r"\b(show|list|what|which|my|any|active|upcoming|view)\b.*\bschedul(es|ed)\b|^schedules\??$", t) \
            and not parse_schedule(text):
        return {"action": "monitor", "monitor_topic": "schedules", "reasoning": "User asked about schedules."}
    if re.search(r"\b(how many|show|list|which|what|any|view)\b.*\btickets\b|^tickets\??$", t) and \
            not re.search(r"\bcreate\b", t):
        return {"action": "monitor", "monitor_topic": "tickets", "reasoning": "User asked about tickets."}
    if re.search(r"\b(how many|show|list|which|what|any|view)\b.*\be-?mails?\b|^e-?mails\??$", t) and \
            not EMAIL_RE.search(text) and not re.search(r"\bsend\b", t):
        return {"action": "monitor", "monitor_topic": "emails", "reasoning": "User asked about emails."}
    if re.search(r"\b(how many|current|active|any|open)\b.*\balerts?\b.*\??|\balert status\b", t) and \
            not re.search(r"\b(summar|report|ticket|email|send)", t):
        return {"action": "monitor", "monitor_topic": "alerts", "reasoning": "User asked about current alerts."}
    if re.search(r"\b(overview|stats|statistics|dashboard|system status|how are things|status)\b", t) and \
            not re.search(r"\b(report|summar|ticket|send)", t):
        return {"action": "monitor", "monitor_topic": "stats", "reasoning": "User asked for an overview."}
    return None


def select(text: str) -> dict:
    """Return {'action', 'workflow_code', 'params', 'schedule', 'reasoning'} or a dynamic composition."""
    i = detect_intents(text)
    params = parse_params(text)
    schedule = parse_schedule(text)
    base = {"params": params, "schedule": schedule}

    cmd = detect_command(text)
    if cmd:
        return {**base, "intents": i, **cmd}

    if i["help"] and not (i["ticket"] or i["performance"]):
        return {"action": "list_workflows", **base, "reasoning": "User asked which workflows exist."}
    if i["performance"]:
        return {"action": "run_existing", "workflow_code": "WF004", **base,
                "reasoning": "Request mentions performance data -> Performance Report."}
    if i["ticket"] and (i["summary"] or (i["email"] and "email_to" in params)):
        return {"action": "generate_dynamic", **base, "intents": i,
                "reasoning": "Request combines summary/ticketing/email -> composing a dynamic workflow."}
    if i["ticket"]:
        params.setdefault("intensities", ["DOWN"])
        return {"action": "run_existing", "workflow_code": "WF002", **base,
                "reasoning": "Request asks to create tickets for alerts -> Ticket Creation."}
    if i["email"] and i["conditional"] and not i["summary"]:
        return {"action": "run_existing", "workflow_code": "WF003", **base,
                "reasoning": "Conditional email request -> Email Notification."}
    if i["alerts"] or i["summary"]:
        return {"action": "run_existing", "workflow_code": "WF001", **base,
                "reasoning": "Request asks to collect/summarise alerts -> Daily Alert Summary."}
    return {"action": "clarify", **base, "reasoning": "No workflow matched.",
            "reply": "I couldn't match that to a workflow. Try e.g. *\"Summarise down alerts\"*, "
                     "*\"Create tickets for devices down more than 15 minutes\"* or "
                     "*\"Performance report for Mangaluru for the last 6 hours\"*."}


def compose_dynamic(text: str, intents: dict, params: dict) -> dict:
    """Compose a workflow definition from capability building blocks."""
    t = text.lower()
    if intents.get("performance"):
        return _compose_performance(text, params)
    if isinstance(params.get("metrics"), str):
        params = {**params, "metrics": [m.strip().upper() for m in params["metrics"].split(",") if m.strip()]}
    critical_only = bool(re.search(r"\b(critical|each critical|every critical)\b", t))
    fetch_inputs = {"intensities": "$params.intensities", "min_active_minutes": "$params.min_active_minutes"}
    if params.get("metrics"):
        fetch_inputs["metrics"] = "$params.metrics"
    steps = [{"id": "fetch", "plugin": "alerts", "action": "fetch", "inputs": fetch_inputs}]
    parts = [f"Collect {'/'.join(params['metrics'])} alerts" if params.get("metrics") else "Collect alerts"]
    if intents.get("summary"):
        steps.append({"id": "summarize", "plugin": "ai", "action": "summarize_alerts",
                      "inputs": {"data": "$steps.fetch"}})
        parts.append("summarise")
    target = "$steps.fetch.critical" if critical_only else "$steps.fetch.alerts"
    target_expr = "steps.fetch.critical" if critical_only else "steps.fetch.alerts"
    if intents.get("ticket"):
        steps.append({"id": "tickets", "plugin": "ticketing", "action": "create_for_alerts",
                      "when": f"len({target_expr}) > 0", "on_error": "continue",
                      "inputs": {"alerts": target, "priority": "$params.priority", "skip_existing": True}})
        parts.append("ticket " + ("critical incidents" if critical_only else "alerts"))
    report_inputs = {"title": "Alert Summary & Incident Report", "data": "$steps.fetch",
                     "summary": "$steps.summarize.summary" if intents.get("summary")
                     else "{{steps.fetch.count}} alert(s) in scope."}
    if intents.get("ticket"):
        report_inputs["tickets"] = "$steps.tickets"
    steps.append({"id": "report", "plugin": "report", "action": "alert_report", "inputs": report_inputs})
    if params.get("email_to") or intents.get("email"):
        steps.append({"id": "notify", "plugin": "email", "action": "send", "when": "params.email_to",
                      "inputs": {"to": "$params.email_to", "body": "$steps.report.markdown",
                                 "subject": "Alert Summary & Incident Report - {{steps.fetch.count}} alerts, "
                                            "{{steps.fetch.critical_count}} critical"}})
        parts.append("email report")
    schema = {
        "intensities": {"type": "list", "default": params.get("intensities", ["DOWN", "HIGH"]),
                        "description": "Severities to include"},
        "min_active_minutes": {"type": "integer", "default": params.get("min_active_minutes", 0),
                               "description": "Minimum minutes active"},
        "priority": {"type": "string", "default": params.get("priority", "High"),
                     "enum": ["Highest", "High", "Medium", "Low"], "description": "Ticket priority"},
        "email_to": {"type": "email_list", "description": "Report recipients",
                     **({"default": params["email_to"]} if params.get("email_to") else
                        {"required": True} if intents.get("email") else {})},
    }
    if params.get("metrics"):
        schema["metrics"] = {"type": "list", "default": params["metrics"], "description": "Metric types"}
    return {
        "name": " -> ".join(p.capitalize() if i == 0 else p for i, p in enumerate(parts)),
        "description": f"Generated from request: \"{text.strip()[:300]}\"",
        "params_schema": schema,
        "definition": {"steps": steps, "output": {"report": "$steps.report.markdown",
                                                  "alerts": "$steps.fetch.count",
                                                  "tickets": "$steps.tickets.created_count"}},
    }


def _compose_performance(text: str, params: dict) -> dict:
    zone = params.get("zone") or "all"
    steps = [
        {"id": "fetch", "plugin": "performance", "action": "fetch",
         "inputs": {"zone": "$params.zone", "hours": "$params.hours"}},
        {"id": "summarize", "plugin": "ai", "action": "summarize_performance", "inputs": {"data": "$steps.fetch"}},
        {"id": "report", "plugin": "report", "action": "performance_report",
         "inputs": {"summary": "$steps.summarize.summary", "data": "$steps.fetch"}},
        {"id": "notify", "plugin": "email", "action": "send", "when": "params.email_to",
         "inputs": {"to": "$params.email_to", "subject": "$steps.report.title", "body": "$steps.report.markdown"}},
    ]
    schema = {
        "zone": {"type": "string", "default": zone, "validator": "zone", "description": "Zone or 'all'"},
        "hours": {"type": "number", "default": params.get("hours", 6), "description": "Look-back window (hours)"},
        "email_to": {"type": "email_list", "description": "Recipients",
                     **({"default": params["email_to"]} if params.get("email_to") else {})},
    }
    return {"name": f"Performance report ({zone}){' + email' if params.get('email_to') else ''}",
            "description": f"Generated from request: \"{text.strip()[:300]}\"", "params_schema": schema,
            "definition": {"steps": steps, "output": {"report": "$steps.report.markdown"}}}


def merge_followup(text: str, pending: dict) -> dict | None:
    """If the message supplies values for a pending request (e.g. a zone name or email), merge them."""
    i = detect_intents(text)
    if i["performance"] or i["ticket"] or i["help"] or detect_command(text):
        return None  # looks like a new request
    new = parse_params(text)
    stripped = text.strip().strip(".")
    if "zone" in (pending.get("needs") or []) or any("zone" in e for e in pending.get("errors", [])):
        if "zone" not in new and re.fullmatch(r"[A-Za-z][A-Za-z ]{1,40}", stripped):
            new["zone"] = stripped
    if not new:
        return None
    merged = dict(pending.get("params") or {})
    merged.update(new)
    return {"action": "run_existing", "workflow_code": pending["workflow_code"], "params": merged,
            "schedule": pending.get("schedule"), "reasoning": "Follow-up supplied missing/corrected parameters."}
