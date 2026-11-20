"""Reporting Agent - produces the execution report for a workflow run."""
import json

from sqlalchemy.orm import Session

from .. import llm
from ..db import WorkflowRun

ICON = {"success": "OK", "failed": "FAILED", "skipped": "skipped", "partial": "PARTIAL"}


def _duration(a, b) -> str:
    if not a or not b:
        return "-"
    return f"{(b - a).total_seconds() * 1000:.0f} ms"


def build_execution_report(db: Session, run: WorkflowRun) -> str:
    db.refresh(run)
    wf = run.workflow
    lines = [
        f"## Execution report - {wf.code} {wf.name} (run #{run.id})",
        f"**Status:** {run.status.upper()} &nbsp; **Trigger:** {run.trigger} &nbsp; "
        f"**Duration:** {_duration(run.started_at, run.finished_at)}",
        f"**Parameters:** `{json.dumps(run.params, default=str)}`",
        "", "| # | Step | Plugin.action | Status | Duration | Note |", "|---|---|---|---|---|---|",
    ]
    for i, s in enumerate(run.steps, 1):
        out = s.output if isinstance(s.output, dict) else {}
        note = s.error or out.get("reason", "")
        lines.append(f"| {i} | {s.step_id} | {s.plugin}.{s.action} | {ICON.get(s.status, s.status)} | "
                     f"{_duration(s.started_at, s.finished_at)} | {str(note)[:120]} |")
    if run.error:
        lines += ["", f"**Errors:** {run.error}"]

    side_effects = []
    for s in run.steps:
        out = s.output or {}
        if s.status != "success" or not isinstance(out, dict):
            continue
        if s.plugin == "ticketing":
            keys = [t["key"] for t in out.get("created", [])]
            side_effects.append(f"Tickets created: {', '.join(keys) if keys else 'none'}"
                                + (f" ({len(out.get('skipped', []))} skipped as already ticketed)"
                                   if out.get("skipped") else ""))
        if s.plugin == "email":
            side_effects.append(f"Email {out.get('status')} to {', '.join(out.get('to', []))}")
    if side_effects:
        lines += ["", "**Actions taken:**"] + [f"- {x}" for x in side_effects]

    if llm.enabled():
        insight = llm.text(
            "You review automated workflow executions. In at most two sentences, state whether the run "
            "achieved its goal and anything an operator should follow up on. Plain text, no preamble.",
            "\n".join(lines), max_tokens=1000)
        if insight:
            lines += ["", f"**Reporting agent:** {insight}"]

    body = (run.output or {}).get("report") if isinstance(run.output, dict) else None
    if body:
        lines += ["", "---", "", body]
    return "\n".join(lines)
