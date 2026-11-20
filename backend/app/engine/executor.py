"""Workflow execution engine.

Each workflow definition is compiled into a LangGraph ``StateGraph``:

* every step becomes a node that resolves its inputs, evaluates its ``when``
  condition, calls ``plugin.action`` and persists a ``StepRun`` row;
* edges are conditional - after each node a router follows the first matching
  ``branches`` entry (``{"when": expr, "goto": step_id|"END"}``), stops on a
  failed ``on_error: stop`` step, or falls through to the next step.

Definition example::

    {"steps": [
        {"id": "fetch", "plugin": "alerts", "action": "fetch",
         "inputs": {"intensities": "$params.intensities"},
         "branches": [{"when": "steps.fetch.count == 0", "goto": "report"}]},
        {"id": "tickets", "plugin": "ticketing", "action": "create_for_alerts",
         "inputs": {"alerts": "$steps.fetch.critical"},
         "when": "len(steps.fetch.critical) > 0", "on_error": "continue"},
        {"id": "report", "plugin": "report", "action": "alert_report", "inputs": {...}}
     ],
     "output": {"report": "$steps.report.markdown"}}
"""
import operator
import traceback
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from ..db import PluginConfig, StepRun, Workflow, WorkflowRun, utcnow
from ..plugins import PluginContext, get_plugin
from .expr import evaluate, resolve
from .params import validate_params


def _merge(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class RunState(TypedDict, total=False):
    params: dict
    steps: Annotated[dict, _merge]          # step_id -> output
    status: Annotated[dict, _merge]         # step_id -> success|failed|skipped
    errors: Annotated[list, operator.add]
    halted: bool


def _jsonable(v: Any) -> Any:
    """Make step outputs safe to store in a JSON column."""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def build_graph(definition: dict, db: Session, run_id: int | None):
    steps = definition["steps"]
    ids = [s["id"] for s in steps]
    plugin_cfg = {p.name: p for p in db.query(PluginConfig).all()}

    def make_node(step: dict):
        sid = step["id"]

        def node(state: RunState) -> dict:
            ctx = {"params": state.get("params", {}), "steps": state.get("steps", {})}
            started = utcnow()
            rec = StepRun(run_id=run_id, step_id=sid, plugin=step["plugin"], action=step["action"],
                          status="running", started_at=started)
            try:
                cond = step.get("when")
                if cond and not evaluate(cond, ctx):
                    rec.status = "skipped"
                    rec.output = {"reason": f"condition not met: {cond}"}
                    return {"status": {sid: "skipped"}, "steps": {sid: None}}
                cfg = plugin_cfg.get(step["plugin"])
                if cfg is not None and not cfg.enabled:
                    raise RuntimeError(f"plugin '{step['plugin']}' is disabled")
                inputs = resolve(step.get("inputs", {}), ctx)
                rec.inputs = _summarize(inputs)
                plugin = get_plugin(step["plugin"])
                out = plugin.run(step["action"], PluginContext(db=db, run_id=run_id,
                                                               config=cfg.config if cfg else {}), **inputs)
                out = _jsonable(out)
                rec.status, rec.output = "success", out
                return {"status": {sid: "success"}, "steps": {sid: out}}
            except Exception as e:  # noqa: BLE001 - recorded on the step
                db.rollback()
                rec.status, rec.error = "failed", f"{type(e).__name__}: {e}"
                rec.output = {"traceback": traceback.format_exc(limit=3)}
                halted = step.get("on_error", "stop") == "stop"
                return {"status": {sid: "failed"}, "steps": {sid: None},
                        "errors": [f"{sid}: {e}"], "halted": halted}
            finally:
                rec.finished_at = utcnow()
                if run_id is not None:
                    db.add(rec)
                    db.commit()
        return node

    def make_router(idx: int):
        step = steps[idx]
        default_next = ids[idx + 1] if idx + 1 < len(ids) else END

        def route(state: RunState) -> str:
            if state.get("halted"):
                return END
            ctx = {"params": state.get("params", {}), "steps": state.get("steps", {})}
            for b in step.get("branches", []):
                if evaluate(b["when"], ctx):
                    return END if b["goto"] == "END" else b["goto"]
            return default_next
        return route

    g = StateGraph(RunState)
    for s in steps:
        g.add_node(s["id"], make_node(s))
    g.add_edge(START, ids[0])
    for i, s in enumerate(steps):
        targets = {t: t for t in ids} | {END: END}
        g.add_conditional_edges(s["id"], make_router(i), targets)
    return g.compile()


def _summarize(inputs: dict) -> dict:
    """Store compact inputs (large lists are truncated for readability)."""
    out = {}
    for k, v in inputs.items():
        if isinstance(v, list) and len(v) > 5:
            out[k] = {"_list_len": len(v), "first": _jsonable(v[:3])}
        elif isinstance(v, dict) and len(str(v)) > 2000:
            out[k] = {"_keys": list(v.keys())}
        else:
            out[k] = _jsonable(v)
    return out


class ParamValidationError(ValueError):
    def __init__(self, missing: list[str], errors: list[str]):
        self.missing, self.errors = missing, errors
        super().__init__("; ".join(errors + [f"missing parameter: {m}" for m in missing]))


def execute_workflow(db: Session, workflow: Workflow, params: dict | None, trigger: str,
                     schedule_id: int | None = None) -> WorkflowRun:
    pr = validate_params(workflow.params_schema, params)
    if not pr.ok:
        raise ParamValidationError(pr.missing, pr.errors)

    run = WorkflowRun(workflow_id=workflow.id, trigger=trigger, schedule_id=schedule_id,
                      params=_jsonable(pr.values), status="running")
    db.add(run)
    db.commit()

    try:
        graph = build_graph(workflow.definition, db, run.id)
        final = graph.invoke({"params": pr.values, "steps": {}, "status": {}, "errors": [], "halted": False})
        statuses = final.get("status", {})
        failed = [k for k, v in statuses.items() if v == "failed"]
        if not failed:
            run.status = "success"
        else:
            run.status = "failed" if final.get("halted") else "partial"
            run.error = "; ".join(final.get("errors", []))
        ctx = {"params": pr.values, "steps": final.get("steps", {})}
        run.output = _jsonable(resolve(workflow.definition.get("output", {}), ctx))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        run.status, run.error = "failed", f"{type(e).__name__}: {e}"
    run.finished_at = utcnow()
    db.add(run)
    db.commit()

    from ..agents.reporting import build_execution_report  # local import avoids a cycle
    run.report = build_execution_report(db, run)
    db.commit()
    db.refresh(run)
    return run
