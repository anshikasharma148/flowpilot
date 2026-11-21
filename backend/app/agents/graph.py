"""Conversational orchestration - a LangGraph of cooperating agents.

    user message
        |
  [Workflow Selection Agent] --(list/answer/clarify)--> END
        |                 \
        |            (generate_dynamic)
        |                   v
        |        [Workflow Generation Agent]
        v                   |
  [Parameter Validation Agent] <-+  --(missing / invalid)--> END (ask user)
        v
  [Plugin Selection Agent]  --(plugin disabled)--> END
        v
  [Execution Agent]  --(scheduled)--> END
        v
  [Reporting Agent] --> END

Every agent uses Claude when available and falls back to deterministic rules.
"""
import hashlib
import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from .. import llm, scheduler
from ..db import ChatMessage, PluginConfig, Schedule, Workflow
from ..engine.executor import execute_workflow
from ..engine.params import validate_params
from ..engine.validate import validate_definition
from ..plugins import all_plugins, get_plugin
from . import monitor, rules
from .schemas import DynamicWorkflow, SelectionDecision


class ChatState(TypedDict, total=False):
    message: str
    session_id: str
    pending: dict | None
    action: str
    workflow_code: str | None
    params: dict
    schedule: dict | None
    intents: dict
    plugins: list
    run_id: int | None
    schedule_id: int | None
    reply: str
    status: str            # answered | needs_input | executed | scheduled | error
    meta: dict
    trace: Annotated[list, operator.add]


def _t(agent: str, summary: str, **detail: Any) -> list:
    return [{"agent": agent, "summary": summary, "detail": detail}]


# ----------------------------------------------------------------------------- catalogs
def workflow_catalog(db: Session) -> list[dict]:
    return [{"code": w.code, "name": w.name, "kind": w.kind, "description": w.description,
             "params": {k: {kk: vv for kk, vv in v.items() if kk in ("type", "required", "default", "enum",
                                                                     "description")}
                        for k, v in (w.params_schema or {}).items()}}
            for w in db.query(Workflow).filter_by(is_active=True).order_by(Workflow.code).all()]


def plugin_catalog() -> list[dict]:
    return [p.describe() for p in all_plugins()]


def _kv_to_dict(kvs) -> dict:
    out = {}
    for kv in kvs or []:
        v = kv.value
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            pass
        out[kv.name] = v
    return out


def _list_workflows_reply(db: Session) -> str:
    lines = ["Here are the available workflows:", "", "| Code | Workflow | Type | What it does |", "|---|---|---|---|"]
    for w in workflow_catalog(db):
        lines.append(f"| {w['code']} | {w['name']} | {w['kind']} | {w['description'][:120]} |")
    lines += ["", "Ask in plain language, e.g. *\"Generate the performance report for Mangaluru for the last "
              "6 hours and email it to me@example.com\"*, or add a schedule: *\"every day at 9 AM ...\"*."]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- agents
SELECTION_SYSTEM = """You are the Workflow Selection Agent of a network-operations workflow automation platform.
Map the user's request onto the workflow catalog and extract parameters.

Rules:
- Prefer run_existing when ONE catalog workflow fully covers the request (its parameters can express it).
- Use generate_dynamic when the request needs several capabilities together (e.g. summarise alerts AND create
  tickets AND email) that no single catalog workflow covers.
- Extract parameters using the catalog's parameter names. Lists are comma separated (e.g. "DOWN,HIGH").
  Severities: DOWN, HIGH, MEDIUM, LOW, STOPPED ("critical" means DOWN). Durations for alert age go in
  min_active_minutes (minutes). "operations team"/"ops team" email = {ops_email} (if empty, leave
  email_to out so the user is asked for it). Never invent an email address.
- Keep the zone exactly as the user wrote it (validation happens later).
- schedule: only when the user asks for recurring or deferred execution ("every day at 9 AM" -> daily 09:00).
- If a pending request is waiting for parameters and the new message supplies them, return run_existing for
  the pending workflow with the merged parameters (and keep its schedule).
- "run WF005" / "execute workflow WF002" -> run_existing with that code.
- define_workflow when the user asks to create/define/build/save a NEW workflow for later (do not run it);
  still extract its parameters (e.g. metrics, intensities, email_to, zone).
- monitor for questions about what happened or what exists: last run, a specific run (target_id = run number),
  recent runs, failed runs, schedules, tickets, emails, current alerts, overall stats.
- manage_schedule to pause / resume / delete / run_now an existing schedule (target_id = schedule number).
- Asking for a report or summary of alerts/performance is a workflow (run_existing), NOT monitor."""

GENERATION_SYSTEM = """You are the Workflow Generation Agent. Design a workflow (a list of steps) that fulfils the
user's request using ONLY the plugin actions listed. Step input values may be literals (JSON) or references:
"$params.<name>" for workflow parameters, "$steps.<step_id>.<field>" for outputs of EARLIER steps, and
"{{steps.<id>.<field>}}" for string interpolation. A step may have a "when" condition, a Python-like boolean
expression over params/steps, e.g. "len(steps.fetch.critical) > 0" or "params.email_to". Use on_error
"continue" for non-essential side effects. Always build a report step (report.alert_report or
report.performance_report) and set output_report_step to it. Declare every $params.* you use in params with
defaults taken from the request. Give every step ALL of its required_inputs (e.g. email.send needs "to" and
"body"); never add a step with empty inputs and never repeat a side-effect step (one email.send, one ticket
creation). Reference a list parameter directly ("$params.metrics"), not inside another list.
Example of a valid workflow:
{example}"""


def make_graph(db: Session):
    def selection_agent(state: ChatState) -> dict:
        msg, pending = state["message"], state.get("pending")
        decision: dict | None = None
        source = "rules"
        if llm.enabled():
            prompt = (f"Workflow catalog:\n{json.dumps(workflow_catalog(db), indent=1)}\n\n"
                      + (f"Pending request awaiting input: {json.dumps(pending)}\n\n" if pending else "")
                      + f"User message: {msg}")
            res = llm.structured(SELECTION_SYSTEM.format(ops_email=rules.config.OPS_TEAM_EMAIL), prompt,
                                 SelectionDecision)
            if res is not None:
                decision = {"action": res.action, "workflow_code": res.workflow_code,
                            "params": _kv_to_dict(res.params),
                            "schedule": res.schedule.model_dump(exclude_none=True) if res.schedule else None,
                            "reply": res.reply, "reasoning": res.reasoning,
                            "monitor_topic": res.monitor_topic, "schedule_op": res.schedule_op,
                            "target_id": res.target_id, "intents": rules.detect_intents(msg)}
                source = llm.provider()
        if decision is not None:
            decision["params"] = _only_known_emails(decision.get("params") or {}, msg, pending)
        if decision is None:
            decision = (rules.merge_followup(msg, pending) if pending else None) or rules.select(msg)

        action = decision["action"]
        trace = _t("Workflow Selection Agent",
                   f"{action}" + (f" -> {decision.get('workflow_code')}" if decision.get("workflow_code") else ""),
                   source=source, reasoning=decision.get("reasoning"), params=decision.get("params"),
                   schedule=decision.get("schedule"))
        out: dict = {"action": action, "workflow_code": decision.get("workflow_code"),
                     "params": decision.get("params") or {}, "schedule": decision.get("schedule"),
                     "intents": decision.get("intents") or rules.detect_intents(msg), "trace": trace}
        if action == "monitor":
            topic = decision.get("monitor_topic") or "stats"
            reply, meta = monitor.answer(db, topic, decision.get("target_id"))
            out.update(reply=reply, status="answered", run_id=meta.get("run_id"),
                       trace=trace + _t("Monitoring Agent", f"answered '{topic}' from the database",
                                        target_id=decision.get("target_id")))
        elif action == "manage_schedule":
            op = decision.get("schedule_op") or "pause"
            reply, meta = monitor.manage_schedule(db, op, decision.get("target_id"))
            out.update(reply=reply, status="answered", run_id=meta.get("run_id"), schedule_id=meta.get("schedule_id"),
                       trace=trace + _t("Monitoring Agent", f"schedule {op} #{decision.get('target_id')}"))
        elif action == "list_workflows":
            out.update(reply=_list_workflows_reply(db), status="answered")
        elif action in ("answer", "clarify"):
            out.update(reply=decision.get("reply") or rules.select("")["reply"], status="answered")
        elif action == "run_existing":
            wf = db.query(Workflow).filter_by(code=decision.get("workflow_code"), is_active=True).first()
            if wf is None:
                out.update(action="clarify", status="answered",
                           reply=f"Workflow '{decision.get('workflow_code')}' does not exist. " + _list_workflows_reply(db))
        return out

    def generation_agent(state: ChatState) -> dict:
        msg, params = state["message"], state.get("params") or {}
        spec, source, errors = None, "rules", []
        if llm.enabled():
            example = json.dumps(db.query(Workflow).filter_by(code="WF001").first().definition)
            prompt = (f"Plugins:\n{json.dumps(plugin_catalog(), indent=1)}\n\nExtracted parameters: "
                      f"{json.dumps(params)}\n\nUser request: {msg}")
            for _attempt in range(2):
                res = llm.structured(GENERATION_SYSTEM.replace("{example}", example), prompt, DynamicWorkflow,
                                     max_tokens=8000)
                if res is None:
                    break
                cand = _apply_extracted_defaults(_strip_invented_email_defaults(_dynamic_to_spec(res, msg), msg),
                                                 params)
                errors = validate_definition(cand["definition"])
                if not errors:
                    spec, source = cand, llm.provider()
                    break
                prompt += f"\n\nYour previous design was invalid: {errors}. Fix it."
        if spec is None:
            spec = rules.compose_dynamic(msg, state.get("intents") or rules.detect_intents(msg), params)
            errors = validate_definition(spec["definition"])
            if errors:
                return {"status": "error", "reply": "I could not build a valid workflow: " + "; ".join(errors),
                        "trace": _t("Workflow Generation Agent", "failed", errors=errors)}
        before = db.query(Workflow).count()
        wf = _save_dynamic(db, spec)
        if state.get("action") == "define_workflow":
            existed = db.query(Workflow).count() == before
            steps_md = "\n".join(
                f"{i}. `{s['plugin']}.{s['action']}`" + (f" — only when `{s['when']}`" if s.get("when") else "")
                for i, s in enumerate(wf.definition["steps"], 1))
            params_md = ", ".join(f"`{k}`" + (f" = {v['default']}" if v.get("default") not in (None, "") else "")
                                  for k, v in (wf.params_schema or {}).items()) or "none"
            reply = (f"{'An identical workflow already exists' if existed else 'Created'}: **{wf.code} - {wf.name}** "
                     f"(saved, not run).\n\n**Steps:**\n{steps_md}\n\n**Parameters:** {params_md}\n\n"
                     f"Say *\"run {wf.code}\"* to execute it, add a schedule (e.g. *\"run {wf.code} every day at "
                     f"8 AM\"*), or edit it on the **Workflows** page.")
            return {"workflow_code": wf.code, "status": "defined", "reply": reply,
                    "trace": _t("Workflow Generation Agent", f"{'reused' if existed else 'defined'} {wf.code} "
                                f"'{wf.name}' (not run)", source=source,
                                steps=[f"{s['id']}: {s['plugin']}.{s['action']}" for s in wf.definition["steps"]])}
        return {"workflow_code": wf.code, "action": "run_existing",
                "trace": _t("Workflow Generation Agent", f"generated {wf.code} '{wf.name}'", source=source,
                            steps=[f"{s['id']}: {s['plugin']}.{s['action']}" for s in wf.definition["steps"]])}

    def validation_agent(state: ChatState) -> dict:
        wf = db.query(Workflow).filter_by(code=state["workflow_code"]).first()
        res = validate_params(wf.params_schema, state.get("params"))
        # The user asked for something to be emailed but gave no recipient: ask instead of silently skipping.
        if ((state.get("intents") or {}).get("email") and "email_to" in (wf.params_schema or {})
                and not res.values.get("email_to") and "email_to" not in res.missing):
            res.missing.append("email_to")
        if res.ok:
            return {"params": res.values,
                    "trace": _t("Parameter Validation Agent", "parameters valid", params=res.values)}
        lines = [f"I selected **{wf.code} - {wf.name}**, but I need a bit more information:"]
        for e in res.errors:
            lines.append(f"- {e}")
        for m in res.missing:
            desc = (wf.params_schema.get(m) or {}).get("description", "")
            lines.append(f"- Missing **{m}**" + (f" ({desc})" if desc else ""))
        lines.append("\nReply with the corrected value(s) and I'll continue.")
        pending = {"workflow_code": wf.code, "params": state.get("params") or {}, "schedule": state.get("schedule"),
                   "needs": res.missing, "errors": res.errors}
        return {"status": "needs_input", "reply": "\n".join(lines), "meta": {"pending": pending},
                "trace": _t("Parameter Validation Agent", "needs input", missing=res.missing, errors=res.errors)}

    def plugin_agent(state: ChatState) -> dict:
        wf = db.query(Workflow).filter_by(code=state["workflow_code"]).first()
        names = list(dict.fromkeys(s["plugin"] for s in wf.definition["steps"]))
        cfg = {p.name: p for p in db.query(PluginConfig).all()}
        info, disabled = [], []
        for n in names:
            p = get_plugin(n)
            enabled = cfg[n].enabled if n in cfg else True
            info.append({"plugin": n, "mode": p.mode(), "enabled": enabled})
            if not enabled:
                disabled.append(n)
        if disabled:
            return {"status": "error", "plugins": info,
                    "reply": f"Workflow {wf.code} needs plugin(s) that are disabled: {', '.join(disabled)}. "
                             "Enable them on the Plugins page and try again.",
                    "trace": _t("Plugin Selection Agent", "blocked: disabled plugins", plugins=info)}
        return {"plugins": info, "trace": _t("Plugin Selection Agent",
                                             ", ".join(f"{i['plugin']} ({i['mode']})" for i in info), plugins=info)}

    def execution_agent(state: ChatState) -> dict:
        wf = db.query(Workflow).filter_by(code=state["workflow_code"]).first()
        sched = state.get("schedule")
        if sched:
            fields = scheduler.spec_to_fields(sched)
            # Don't create a duplicate of an identical active schedule
            # ("once" schedules are excluded: their run_at differs on every request).
            if fields["frequency"] != "once":
                params_key = json.dumps(state["params"], sort_keys=True, default=str)
                for existing in db.query(Schedule).filter_by(workflow_id=wf.id, enabled=True).all():
                    same_timing = all(getattr(existing, k) == v for k, v in fields.items())
                    if same_timing and json.dumps(existing.params, sort_keys=True, default=str) == params_key:
                        nxt = scheduler.next_run(existing.id)
                        reply = (f"This is already scheduled as **schedule #{existing.id}** "
                                 f"({wf.code} - {wf.name}, {scheduler.describe(existing)}). "
                                 f"Next run: **{nxt.strftime('%Y-%m-%d %H:%M') if nxt else 'n/a'}**. "
                                 "No duplicate was created.")
                        return {"status": "scheduled", "schedule_id": existing.id, "reply": reply,
                                "trace": _t("Execution Agent", f"schedule #{existing.id} already exists - reused")}
            s = Schedule(workflow_id=wf.id, name=f"{wf.name} ({sched.get('frequency')})", params=state["params"],
                         created_from="chat", **fields)
            db.add(s)
            db.commit()
            scheduler.register(s)
            nxt = scheduler.next_run(s.id)
            reply = (f"Scheduled **{wf.code} - {wf.name}**: {scheduler.describe(s)} "
                     f"({scheduler.TZ.key}). Next run: **{nxt.strftime('%Y-%m-%d %H:%M') if nxt else 'n/a'}**.\n\n"
                     f"Steps: " + " -> ".join(f"`{x['plugin']}.{x['action']}`" for x in wf.definition["steps"])
                     + f"\n\nParameters: `{json.dumps(state['params'], default=str)}`\n\n"
                       "You can run it immediately, pause it or delete it from the **Schedules** page.")
            return {"status": "scheduled", "schedule_id": s.id, "reply": reply,
                    "trace": _t("Execution Agent", f"created schedule #{s.id}: {scheduler.describe(s)}",
                                next_run=str(nxt))}
        run = execute_workflow(db, wf, state["params"], trigger="chat")
        return {"status": "executed", "run_id": run.id,
                "trace": _t("Execution Agent", f"run #{run.id} {run.status}",
                            steps=[f"{s.step_id}: {s.status}" for s in run.steps])}

    def reporting_agent(state: ChatState) -> dict:
        from ..db import WorkflowRun
        run = db.get(WorkflowRun, state["run_id"])
        out = run.output if isinstance(run.output, dict) else {}
        head = {"success": "completed successfully", "partial": "completed with warnings",
                "failed": "failed"}.get(run.status, run.status)
        lines = [f"**{run.workflow.code} - {run.workflow.name}** {head} (run #{run.id})."]
        actions = [ln for ln in (run.report or "").splitlines() if ln.startswith("- ")]
        if run.error:
            lines.append(f"\n**Error:** {run.error}")
        if actions:
            lines += ["", *actions]
        if out.get("report"):
            lines += ["", "---", "", out["report"]]
        return {"reply": "\n".join(lines), "trace": _t("Reporting Agent", "execution report generated",
                                                        run_id=run.id, status=run.status)}

    g = StateGraph(ChatState)
    g.add_node("selection", selection_agent)
    g.add_node("generation", generation_agent)
    g.add_node("validation", validation_agent)
    g.add_node("plugins", plugin_agent)
    g.add_node("execution", execution_agent)
    g.add_node("reporting", reporting_agent)
    g.add_edge(START, "selection")
    g.add_conditional_edges("selection", lambda s: END if s.get("status") else (
        "generation" if s["action"] in ("generate_dynamic", "define_workflow") else "validation"),
        {END: END, "generation": "generation", "validation": "validation"})
    g.add_conditional_edges("generation", lambda s: END if s.get("status") else "validation",
                            {END: END, "validation": "validation"})
    g.add_conditional_edges("validation", lambda s: END if s.get("status") else "plugins",
                            {END: END, "plugins": "plugins"})
    g.add_conditional_edges("plugins", lambda s: END if s.get("status") else "execution",
                            {END: END, "execution": "execution"})
    g.add_conditional_edges("execution", lambda s: END if s.get("status") == "scheduled" else "reporting",
                            {END: END, "reporting": "reporting"})
    g.add_edge("reporting", END)
    return g.compile()


# ----------------------------------------------------------------------------- helpers
def _allowed_emails(msg: str, pending: dict | None = None) -> set[str]:
    """Email addresses the assistant may use: ones the user typed (now or in the pending request) and the
    configured operations-team address when the user refers to that team. Never anything the LLM made up."""
    allowed = {e.lower().rstrip(".") for e in rules.EMAIL_RE.findall(msg or "")}
    for e in ((pending or {}).get("params") or {}).get("email_to") or []:
        allowed.add(str(e).lower())
    if rules.config.OPS_TEAM_EMAIL and rules.re.search(r"\b(ops|operations|noc)\s+team\b", (msg or "").lower()):
        allowed.add(rules.config.OPS_TEAM_EMAIL.lower())
    return allowed


def _only_known_emails(params: dict, msg: str, pending: dict | None = None) -> dict:
    value = params.get("email_to")
    if not value:
        return params
    items = value if isinstance(value, list) else rules.re.split(r"[,;\s]+", str(value))
    allowed = _allowed_emails(msg, pending)
    kept = [e.strip() for e in items if e and e.strip().lower() in allowed]
    params = dict(params)
    if kept:
        params["email_to"] = kept
    else:
        params.pop("email_to", None)  # -> the validation agent asks the user for a real address
    return params


def _strip_invented_email_defaults(spec: dict, msg: str) -> dict:
    allowed = _allowed_emails(msg)
    for name, p in (spec.get("params_schema") or {}).items():
        d = p.get("default")
        if "email" in name and d:
            items = d if isinstance(d, list) else [d]
            kept = [e for e in items if str(e).strip().lower() in allowed]
            if kept:
                p["default"] = kept
            else:
                p.pop("default", None)
    return spec


def _apply_extracted_defaults(spec: dict, params: dict) -> dict:
    """Make sure values the user stated (e.g. an email address) become defaults of the generated workflow,
    and that every $params.x referenced by a step is declared in the schema."""
    import re as _re
    schema = spec["params_schema"]
    referenced = set(_re.findall(r"\$params\.([A-Za-z_][A-Za-z0-9_]*)|\bparams\.([A-Za-z_][A-Za-z0-9_]*)",
                                 json.dumps(spec["definition"])))
    for name in {a or b for a, b in referenced}:
        val = (params or {}).get(name)
        typ = ("email_list" if "email" in name else "list" if isinstance(val, list) else
               "integer" if isinstance(val, int) and not isinstance(val, bool) else
               "number" if isinstance(val, float) else "boolean" if isinstance(val, bool) else "string")
        schema.setdefault(name, {"type": typ, "description": name})
    for name, value in (params or {}).items():
        if value in (None, "", []):
            continue
        if name in schema and schema[name].get("default") in (None, "", []):
            schema[name]["default"] = value
    return spec


def _repair_split_steps(steps: list[dict]) -> list[dict]:
    """LLMs sometimes split one step in two: an empty-input step carrying the `when`, followed by the same
    plugin.action carrying the inputs. Merge such pairs into a single step."""
    out: list[dict] = []
    i = 0
    while i < len(steps):
        s = steps[i]
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        if (nxt and not s.get("inputs") and nxt.get("inputs")
                and (s["plugin"], s["action"]) == (nxt["plugin"], nxt["action"])):
            merged = {**nxt, "id": s["id"]}
            for k in ("when", "on_error", "branches"):
                if s.get(k) and not nxt.get(k):
                    merged[k] = s[k]
            out.append(merged)
            i += 2
            continue
        out.append(s)
        i += 1
    return out


def _dynamic_to_spec(res: DynamicWorkflow, msg: str) -> dict:
    steps = []
    for s in res.steps:
        step = {"id": s.id, "plugin": s.plugin, "action": s.action, "inputs": _kv_to_dict(s.inputs)}
        if s.when:
            step["when"] = s.when
        if s.on_error != "stop":
            step["on_error"] = s.on_error
        steps.append(step)
    schema = {}
    for p in res.params:
        spec = {"type": p.type, "description": p.description}
        if p.default not in (None, ""):
            try:
                spec["default"] = json.loads(p.default)
            except (ValueError, TypeError):
                spec["default"] = p.default
        schema[p.name] = spec
    steps = _repair_split_steps(steps)
    output = {"report": f"$steps.{res.output_report_step}.markdown"} if res.output_report_step else {}
    return {"name": res.name, "description": res.description or f"Generated from: {msg[:300]}",
            "params_schema": schema, "definition": {"steps": steps, "output": output}}


def _save_dynamic(db: Session, spec: dict) -> Workflow:
    """Persist a generated workflow; reuse an identical one if it already exists."""
    digest = hashlib.sha256(json.dumps([spec["definition"], spec["params_schema"]], sort_keys=True)
                            .encode()).hexdigest()[:16]
    for w in db.query(Workflow).filter_by(kind="dynamic", is_active=True).all():
        if (w.definition or {}).get("_hash") == digest:
            return w
    codes = [w.code for w in db.query(Workflow).all() if w.code.startswith("WF") and w.code[2:].isdigit()]
    nxt = max([int(c[2:]) for c in codes] + [4]) + 1
    definition = dict(spec["definition"], _hash=digest)
    wf = Workflow(code=f"WF{nxt:03d}", name=spec["name"][:200], description=spec["description"], kind="dynamic",
                  definition=definition, params_schema=spec["params_schema"])
    db.add(wf)
    db.commit()
    return wf


def handle_message(db: Session, session_id: str, message: str) -> dict:
    last = (db.query(ChatMessage).filter_by(session_id=session_id, role="assistant")
            .order_by(ChatMessage.id.desc()).first())
    pending = (last.meta or {}).get("pending") if last else None

    db.add(ChatMessage(session_id=session_id, role="user", content=message))
    db.commit()

    graph = make_graph(db)
    try:
        final = graph.invoke({"message": message, "session_id": session_id, "pending": pending, "trace": []})
    except Exception as e:  # noqa: BLE001
        db.rollback()
        final = {"status": "error", "reply": f"Something went wrong: {type(e).__name__}: {e}",
                 "trace": [{"agent": "Orchestrator", "summary": "error", "detail": {"error": str(e)}}]}

    meta = {"trace": final.get("trace", []), "status": final.get("status"), "run_id": final.get("run_id"),
            "schedule_id": final.get("schedule_id"), "workflow_code": final.get("workflow_code"),
            "llm": llm.status()["mode"]}
    meta.update(final.get("meta") or {})
    reply = final.get("reply") or "Done."
    db.add(ChatMessage(session_id=session_id, role="assistant", content=reply, meta=meta))
    db.commit()
    return {"reply": reply, **meta}
