# Design — Agentic Workflow Automation

## 1. Goals

| Requirement | How it is met |
|---|---|
| Execute workflows from natural language | Chat → LangGraph pipeline of agents (§3) |
| Scheduled execution (daily / weekly / monthly / specific time) | APScheduler with schedules persisted in SQL (§6) |
| Predefined workflows | WF001–WF004 seeded from code (§4.3) |
| Dynamically generated workflows | Workflow Generation Agent composes steps from plugin actions, validates and saves them (§3, §4.4) |
| Conditional workflows | Step `when` conditions + graph `branches` (conditional LangGraph edges) (§4.2) |
| Plugin architecture | Registry of `Plugin` classes exposing typed actions (§5) |
| Database design | SQLAlchemy models (§7); alert/performance data read from JSON as allowed |
| Monitoring | Per-run and per-step records, live Runs page, dashboard, execution reports, **and from chat** (Monitoring Agent) |
| Define / manage via chat | "Create a workflow that …" saves without running; "pause / resume / delete / run schedule N"; "run WF00X" |

## 2. Architecture

```
 React UI (Vite)                    FastAPI backend
 ┌─────────────────┐  REST /api   ┌──────────────────────────────────────────────────────────┐
 │ Chat            │ ───────────► │  Agent pipeline (LangGraph)                              │
 │ Dashboard       │              │   Selection → [Generation] → Validation → Plugin         │
 │ Workflows       │              │   Selection → Execution → Reporting                      │
 │ Runs (monitor)  │              │                 │                                        │
 │ Schedules       │              │                 ▼                                        │
 │ Tickets/Emails  │              │  Workflow engine: definition ──compile──► StateGraph     │
 │ Plugins         │              │                 │ step nodes call plugin.action          │
 └─────────────────┘              │                 ▼                                        │
                                  │  Plugins: alerts · performance · ai · ticketing · email  │
                                  │           · report            (registry, enable/disable) │
                                  │      │ JSON files      │ Claude API     │ Jira / SMTP     │
                                  │  APScheduler ──(cron/interval/date)──► engine            │
                                  │  SQL DB (SQLAlchemy): workflows, runs, steps, schedules, │
                                  │           tickets, emails, plugins, chat                 │
                                  └──────────────────────────────────────────────────────────┘
```

LangGraph is used at **two levels**:

1. **Orchestration graph** (`agents/graph.py`): the cooperating agents that turn a chat message into an action.
2. **Execution graph** (`engine/executor.py`): every workflow definition is compiled into its own
   `StateGraph` at run time. Steps are nodes and branches are conditional edges.

## 3. Agents

| Agent | Responsibility | LLM path | Fallback |
|---|---|---|---|
| **Workflow Selection Agent** | Classify the request (`run_existing`, `generate_dynamic`, `list_workflows`, `answer`, `clarify`), choose a workflow, extract parameters and schedule | Claude structured output (`SelectionDecision`) with the workflow catalog in the prompt | Regex intents / parameter / schedule parser (`rules.py`) |
| **Workflow Generation Agent** | Build a new workflow when no single workflow fits, or when the user asks to *define* one (saved, not run). Values stated in the request (for example an email address) become the workflow's defaults | Claude structured output (`DynamicWorkflow`) from the plugin catalog; validated statically and retried once with the errors fed back | Capability composer (`rules.compose_dynamic`) |
| **Parameter Validation Agent** | Coerce and validate against the workflow's `params_schema`: types, enums, ranges, emails, zone existence. Asks for missing or invalid values and remembers the pending request, so the next message can complete it | — | — |
| **Plugin Selection Agent** | Find the integrations the workflow needs and check they are enabled; report live vs simulated mode | — | — |
| **Execution Agent** | Run the workflow now, or create and register a schedule | — | — |
| **Monitoring Agent** | Answers status questions from the database (last run, a specific run, recent/failed runs, schedules, tickets, emails, alerts, overview) and manages schedules (pause / resume / delete / run now). The LLM only classifies the question, so every number comes straight from the database | Classification by the Selection Agent (`monitor` / `manage_schedule`) | Regex command detection (`rules.detect_command`) |
| **Reporting Agent** | Build the execution report: step table, durations, actions taken (tickets, emails), business report, plus a short AI assessment when Claude is available | Claude text | Deterministic report |

Every agent appends to a `trace`, which the chat UI shows as **Agent trace**. That makes the decision path
auditable during a demo.

**Multi-turn:** when validation fails, the assistant message stores `meta.pending = {workflow_code, params,
schedule, needs, errors}`. On the next message the Selection Agent gets that context. Claude sees it in the
prompt; the rules use `merge_followup`. For example, `Mumbai → (invalid) → "Mangaluru" → run`.

## 4. Workflow model

### 4.1 Definition format

```json
{
  "steps": [
    {"id": "fetch", "plugin": "alerts", "action": "fetch",
     "inputs": {"intensities": "$params.intensities"},
     "branches": [{"when": "steps.fetch.count == 0", "goto": "report"}]},
    {"id": "tickets", "plugin": "ticketing", "action": "create_for_alerts",
     "inputs": {"alerts": "$steps.fetch.critical", "priority": "$params.priority"},
     "when": "len(steps.fetch.critical) > 0", "on_error": "continue"},
    {"id": "report", "plugin": "report", "action": "alert_report",
     "inputs": {"data": "$steps.fetch", "tickets": "$steps.tickets",
                "summary": "{{steps.fetch.count}} alerts"}}
  ],
  "output": {"report": "$steps.report.markdown"}
}
```

- **References:** `$params.x` and `$steps.<id>.<field>` pass the referenced value with its type preserved;
  `{{...}}` interpolates into strings.
- **Parameters** (`params_schema`): `type` (string / integer / number / boolean / list / email_list),
  `required`, `default`, `enum`, `min` / `max`, `validator` (`zone` checks against the dataset).

### 4.2 Conditional execution

- **`when`:** a step runs only if its expression is true; otherwise it is recorded as `skipped`.
- **`branches`:** after a step, the first matching rule jumps to another step or to `END`. These are compiled
  to LangGraph `add_conditional_edges`.
- **`on_error`:** `stop` (the default) halts the run and marks it `failed`; `continue` records the failure and
  marks the run `partial`.
- Expressions are evaluated by a **whitelisted AST interpreter** (`engine/expr.py`): comparisons, boolean
  operators, arithmetic, attribute and index access, and the functions `len`, `any`, `all`, `int`, `str`,
  `bool`, `lower`. Private attributes, imports and calls to anything else are rejected, so user or LLM-defined
  workflows cannot execute arbitrary code.

### 4.3 Predefined workflows

| Code | Name | Steps | Notable logic |
|---|---|---|---|
| WF001 | Daily Alert Summary | fetch → AI summary → report → email | email step `when: params.email_to` |
| WF002 | Ticket Creation | fetch → tickets → report | branch to report when 0 matches; dedup on feed `ticket_id` and local open tickets |
| WF003 | Email Notification | fetch → report → email | branch to `END` if `count < min_count` (conditional notification) |
| WF004 | Performance Report | fetch → AI summary → report → email | zone validator; requested vs actual data window shown |

### 4.4 Dynamic and custom workflows

- **Dynamic** workflows (generated from chat) and **custom** workflows (written in the UI JSON editor or via
  `POST /api/workflows`) go through `validate_definition`. It checks that plugins and actions exist, step IDs
  are unique and well formed, every reference points to an earlier step, expressions parse, and branch
  targets exist.
- Generated workflows are content-hashed, so an identical request reuses the existing workflow instead of
  creating duplicates.
- Workflows are versioned (`version` increments on edit). Predefined workflows are read-only and can be
  duplicated.

## 5. Plugin architecture

```python
@register
class TicketingPlugin(Plugin):
    name = "ticketing"
    def mode(self): return "live" if jira_configured else "simulated"

    @action("create_for_alerts", "Create one ticket per alert", inputs={...}, outputs={...})
    def create_for_alerts(self, ctx: PluginContext, alerts=None, priority="High", skip_existing=True): ...
```

- `@register` adds an instance to the registry, and `@action` publishes typed metadata. That metadata is used
  by the UI, by the Generation Agent's prompt and by validation.
- `PluginContext` carries the DB session, the run ID and per-plugin config from the `plugins` table.
- Plugins can be enabled or disabled at runtime (`PATCH /api/plugins/{name}`).
- Built-in plugins:
  - `alerts` and `performance`: JSON data sources
  - `ai`: Claude summaries with a deterministic fallback
  - `ticketing`: Jira REST API, or the local SQL ticket store
  - `email`: SMTP, or a simulated outbox
  - `report`: Markdown reports
- **Adding an integration** (for example Slack or ServiceNow) means one new module with no engine changes. It
  immediately appears in the catalog, so the Generation Agent can use it.

## 6. Scheduling

- A schedule spec from chat or the UI (`daily 09:00`, `weekly mon 08:00`, `monthly day 1`, `interval 30 min`,
  `once at …`, or raw cron) is normalised to `cron`, `interval_minutes` or `run_at` columns.
- APScheduler `BackgroundScheduler` jobs use the configured timezone (default Asia/Kolkata), with
  `misfire_grace_time=1h`, `coalesce=True` and `max_instances=1`.
- Schedules are re-registered from the DB on startup, so they survive restarts.
- Runs started by a schedule have `trigger="schedule"` and a `schedule_id`. One-off schedules disable
  themselves after they fire. Pause, resume, run-now and delete are available in the UI and API.

## 7. Database design

```
workflows(id, code UNIQUE, name, description, kind[predefined|dynamic|custom], definition JSON,
          params_schema JSON, is_active, version, created_at, updated_at)
workflow_runs(id, workflow_id FK, trigger[chat|manual|schedule|api], schedule_id FK NULL, params JSON,
          status[running|success|partial|failed], error, output JSON, report TEXT, started_at, finished_at)
step_runs(id, run_id FK, step_id, plugin, action, status[success|failed|skipped], inputs JSON, output JSON,
          error, started_at, finished_at)
schedules(id, workflow_id FK, name, frequency, cron, run_at, interval_minutes, params JSON, enabled,
          created_from[chat|ui|api], last_run_at, created_at)
tickets(id, key UNIQUE, system[local|jira], title, description, priority, status, alert_uid, device,
          run_id FK, created_at)
email_logs(id, to, subject, body, status[sent|simulated|failed], error, run_id FK, created_at)
plugins(name PK, enabled, config JSON)
chat_messages(id, session_id, role, content, meta JSON(trace, run_id, schedule_id, pending), created_at)
```

- SQLite is the zero-setup default. Any SQLAlchemy URL works through `DATABASE_URL`, for example PostgreSQL.
- Alert and performance data are read from the provided JSON files and cached until the files change, as the
  assignment allows.

## 8. Assumptions about the data

- **Unresolved alerts:** every alert in the feed is active (unresolved). `acknowledged` is a separate filter.
- **Critical:** `intensity == DOWN`. This is configurable per step via `critical_intensities`.
- **`active_since`** is treated as minutes, so "down for more than 15 minutes" becomes `active_since > 15`.
- **Alerts with an existing `ticket_id`** in the feed are not ticketed again.
- **Performance window:** `performance_stats.json` is a single aggregated 8-hour snapshot (04:30–12:30). A
  "last N hours" request is anchored at the snapshot end, and the report shows both the requested window and
  the actual coverage.
- **Zones:** the dataset has only Mangaluru and Shivamogga, so requests for other regions (for example Mumbai)
  are rejected by validation with the list of valid zones.
- **"Operations team":** resolves to `OPS_TEAM_EMAIL`.

## 9. Scalability and production notes

- **Engine:** stateless per run; workflow definitions are data. To scale out, move execution to a worker queue
  (Celery, RQ or Arq) behind the same `execute_workflow` call, and switch APScheduler to a SQLAlchemy job store
  or a dedicated scheduler process so multiple API replicas don't double-fire.
- **Database:** PostgreSQL in production; JSON columns map to JSONB. Add retention for `step_runs` outputs.
- **LLM:** `llm.py` supports Claude (Anthropic) and Gemini (Google). For Gemini, `GEMINI_MODEL` is a fallback chain: a model that is busy or times out is skipped for 5 minutes. Each call is isolated behind `llm.py` with a deterministic fallback, so an LLM outage degrades the
  system instead of stopping it. LangGraph checkpointers could add durable, resumable runs and
  human-in-the-loop approvals, for example "approve ticket creation".
- **Security:** the expression sandbox, definition validation and Markdown rendering through DOMPurify on the
  frontend are in place. Authentication and RBAC are out of scope for the assignment.

## 10. API summary

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/chat` | conversational entry point (`{message, session_id}`) |
| GET | `/api/chat/{session}` | chat history |
| GET/POST | `/api/workflows` | list / create custom workflow |
| GET/PUT/DELETE | `/api/workflows/{code}` | read / update / archive |
| POST | `/api/workflows/validate` | validate a definition |
| POST | `/api/workflows/{code}/run` | manual run with params |
| GET | `/api/runs`, `/api/runs/{id}` | monitoring (filters: workflow, status) |
| GET/POST | `/api/schedules` | list / create |
| PATCH/DELETE | `/api/schedules/{id}` | pause, resume / delete |
| POST | `/api/schedules/{id}/run` | run now |
| GET/PATCH | `/api/plugins`, `/api/plugins/{name}` | catalog / enable-disable |
| GET | `/api/tickets`, `/api/emails` | side effects |
| GET | `/api/stats`, `/api/health` | dashboard / status |

Interactive OpenAPI docs: `http://localhost:8000/docs`.
