"""Structured-output schemas the agents ask Claude to fill."""
from typing import Literal

from pydantic import BaseModel, Field


class KV(BaseModel):
    name: str
    value: str = Field(description="Value as a string. Lists: comma separated. Use JSON literals for "
                                   "booleans/numbers/lists when inside workflow step inputs.")


class ScheduleSpec(BaseModel):
    frequency: Literal["once", "daily", "weekly", "monthly", "interval"]
    time: str | None = Field(None, description="24h HH:MM, e.g. '09:00'")
    day_of_week: str | None = Field(None, description="mon..sun (weekly)")
    day_of_month: int | None = Field(None, description="1-31 (monthly)")
    interval_minutes: int | None = None
    run_at: str | None = Field(None, description="ISO datetime for 'once'")


class SelectionDecision(BaseModel):
    action: Literal["run_existing", "generate_dynamic", "define_workflow", "monitor", "manage_schedule",
                    "list_workflows", "answer", "clarify"] = Field(
        description="run_existing: a single catalog workflow covers the request (also 'run WF005'). "
                    "generate_dynamic: run/schedule a request that needs several capabilities no single workflow "
                    "covers. define_workflow: the user wants to CREATE/SAVE a new workflow without running it. "
                    "monitor: the user asks about status/history (runs, schedules, tickets, emails, alerts, overview). "
                    "manage_schedule: pause/resume/delete/run-now an existing schedule. list_workflows: user asks "
                    "what workflows exist. answer: general question you can answer directly. clarify: too ambiguous.")
    workflow_code: str | None = Field(None, description="catalog code for run_existing, e.g. WF004")
    monitor_topic: Literal["last_run", "run", "recent_runs", "failed_runs", "schedules", "tickets", "emails",
                           "alerts", "stats"] | None = Field(None, description="for action=monitor")
    schedule_op: Literal["pause", "resume", "delete", "run_now"] | None = Field(
        None, description="for action=manage_schedule")
    target_id: int | None = Field(None, description="run number (monitor topic 'run') or schedule number "
                                                    "(manage_schedule), if the user gave one")
    params: list[KV] = Field(default_factory=list, description="parameters extracted from the request")
    schedule: ScheduleSpec | None = Field(None, description="set only if the user asks for recurring/later execution")
    reply: str = Field("", description="message to the user for answer/clarify/list_workflows")
    reasoning: str = Field("", description="one sentence explaining the choice")


class StepSpec(BaseModel):
    id: str
    plugin: str
    action: str
    inputs: list[KV] = Field(default_factory=list)
    when: str | None = None
    on_error: Literal["stop", "continue"] = "stop"


class ParamSpec(BaseModel):
    name: str
    type: Literal["string", "integer", "number", "boolean", "list", "email_list"]
    default: str | None = Field(None, description="default value (as extracted from the request)")
    description: str = ""


class DynamicWorkflow(BaseModel):
    name: str
    description: str
    params: list[ParamSpec]
    steps: list[StepSpec]
    output_report_step: str | None = Field(None, description="id of the step whose 'markdown' output is the report")
