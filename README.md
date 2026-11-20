# FlowPilot — Agentic Workflow Automation

A configurable workflow automation platform for network operations. Users **define, execute, schedule and
monitor** workflows through a **chat interface** (natural language → AI agents select, parameterise,
validate and run a workflow) or through **scheduled triggers** (daily / weekly / monthly / interval / one-off).

- **Backend:** Python · FastAPI · LangGraph · Claude (Anthropic SDK) · SQLAlchemy (SQLite by default) · APScheduler
- **Frontend:** React 18 (Vite)
- **Data:** alerts and performance stats come from the provided JSON files; everything else (workflows, runs,
  step logs, schedules, tickets, emails, plugins, chat history) is stored in SQL.

The design is described in [docs/DESIGN.md](docs/DESIGN.md). A project overview (features, how to run and test, limitations, deployment) is in [docs/FlowPilot-Project-Overview.pdf](docs/FlowPilot-Project-Overview.pdf), also available [as Word](docs/FlowPilot-Project-Overview.docx).

---

## Quick start

Prerequisites: Python 3.11+, Node 18+.

```powershell
# Windows: opens backend + frontend in two windows
./start.ps1
```
```bash
# macOS / Linux / Git Bash
./start.sh
```

Or manually:

```bash
# backend  -> http://localhost:8000  (API docs at /docs)
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# frontend -> http://localhost:5173
cd frontend
npm install
npm run dev
```

### Enabling the LLM (optional)

Copy `backend/.env.example` to `backend/.env` and set **one** of:
- `GEMINI_API_KEY`: a free key from https://aistudio.google.com/apikey. `GEMINI_MODEL` is a fallback chain, by default `gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash`.
- `ANTHROPIC_API_KEY`: Claude. The model defaults to `claude-opus-5-5`.

- **With a key:** the agents use the LLM for workflow selection, parameter extraction, dynamic workflow
  generation, summaries and execution insights.
- **Without a key:** each agent falls back to a deterministic rule-based implementation, so the whole
  platform (including all three assignment prompts) still works offline. The sidebar shows which mode is
  active.

Claude calls opt in to Anthropic's server-side refusal fallback (`fallbacks: "default"`). Any LLM error falls back
to the rules, and the error appears in the health endpoint.

SMTP and Jira credentials are optional too. Without them, emails go to a simulated outbox and tickets go to a
local ticket table. Both are visible on the *Tickets & Emails* page.

---

## Demo walkthrough (assignment prompts)

Open **Chat** and click the sample prompts.

| # | Prompt | What happens |
|---|---|---|
| 1 | *Every day at 9 AM, check all unresolved down and high operational alerts, generate a summary, create a ticket for each critical incident and email the report to the operations team.* | No single workflow covers summary, tickets and email, so the **Workflow Generation Agent** composes a **dynamic workflow** (WF005: `alerts.fetch → ai.summarize_alerts → ticketing.create_for_alerts (when critical > 0) → report.alert_report → email.send`). It is saved in the DB and **scheduled daily at 09:00**. Use *Schedules → Run now* to see it execute: 2 tickets are created, and 2 critical alerts are skipped because they already have tickets SR601810/SR601811. Re-running creates no duplicates. |
| 2 | *Create a high-priority Jira ticket for all devices that have been down for more than 15 minutes.* | **WF002 Ticket Creation** runs with `intensities=[DOWN]`, `min_active_minutes=15`, `priority=High`. A `branches` rule skips ticketing when nothing matches. |
| 3 | *Generate the performance report for Mumbai for last 6 hours and email it to test@gmail.com* | **WF004 Performance Report** is selected. The **Parameter Validation Agent** rejects `Mumbai`, because the dataset only has Mangaluru and Shivamogga, and asks the user to pick a zone. Reply `Mangaluru` and the pending request resumes: it builds the report and emails it to test@gmail.com. |

Monitoring and management from chat:

| Say | Result |
|---|---|
| *What's the status of my last run?* / *show run #3* | Step-by-step status of that run |
| *Show my schedules* / *pause schedule 1* / *resume schedule 1* / *run schedule 1 now* / *delete schedule 1* | Lists or manages schedules |
| *How many tickets have been created?* / *show emails* / *how many active alerts?* / *give me an overview* | Answers straight from the database |
| *Create a workflow that fetches LINK alerts and emails the report to noc@example.com* | Designs and **saves** a workflow without running it |
| *run WF005* | Runs a workflow by its code |

Then explore:

- **Runs:** live monitor; each run has a step timeline (inputs/outputs, durations, skipped/failed steps) and the
  Reporting Agent's execution report.
- **Workflows:** WF001–WF004 (predefined), AI-generated (dynamic) and custom workflows. You can run them with a
  parameter form, schedule them, or duplicate/edit them as JSON with live validation.
- **Schedules:** pause, resume, run now, delete.
- **Plugins:** every integration, its actions and live/simulated mode. You can disable a plugin; the Plugin
  Selection Agent then blocks workflows that need it.
- **Dashboard:** run outcomes, upcoming schedules, recent runs, current alert feed.

---

## Deploy (free, one URL)

The repo includes a `Dockerfile` that builds the React UI and serves it from the FastAPI server, so one service hosts the whole app. There's also a `render.yaml` blueprint for [Render](https://render.com):

1. On Render, choose **New → Blueprint**, connect this GitHub repo, and pick the free plan.
2. Optionally set `GEMINI_API_KEY`. Leave it empty to run in rule-based mode.
3. Deploy. The app is then live at `https://<service-name>.onrender.com`.

Free-tier notes:
- The service sleeps after about 15 minutes idle; the first request wakes it in about a minute.
- Schedules don't fire while it sleeps.
- The SQLite data resets on each restart or redeploy. WF001–WF004 are recreated automatically.

---

## Tests

```bash
cd backend
.venv/Scripts/python -m pytest -q
```

The suite runs the three assignment prompts end to end in rule-based mode, with ticket deduplication, the
unknown-zone follow-up and scheduling. It also covers conditional branching, parameter validation, custom
workflows, the safe expression evaluator and definition validation.

---

## Project structure

```
backend/
  app/
    main.py              FastAPI routes
    config.py            env configuration
    db.py                SQLAlchemy models (workflows, runs, steps, schedules, tickets, emails, plugins, chat)
    data_sources.py      read-only JSON data (alerts, performance)
    seed.py              predefined workflows WF001-WF004
    scheduler.py         APScheduler integration (persisted schedules)
    llm.py               Claude wrapper + fallback switch
    agents/
      graph.py           LangGraph agent pipeline (selection → generation → validation → plugins → execution → reporting)
      rules.py           rule-based NLU fallback
      schemas.py         structured-output schemas
      reporting.py       Reporting Agent (execution reports)
    engine/
      executor.py        compiles a workflow definition into a LangGraph StateGraph and runs it
      expr.py            safe expression evaluator + $reference resolution
      params.py          parameter schema validation
      validate.py        static workflow-definition validation
    plugins/             plugin architecture: alerts, performance, ai, ticketing, email (mailer), report
  data/                  alerts.json, performance_stats.json
  tests/
frontend/
  src/pages/             Chat, Dashboard, Workflows, Runs, Schedules, Outputs (tickets & emails), Plugins
docs/DESIGN.md
```
