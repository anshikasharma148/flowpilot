"""Ticketing plugin - creates real Jira Cloud issues (REST API v2, basic auth with an API token).

Set JIRA_URL (https://<site>.atlassian.net), JIRA_EMAIL, JIRA_API_TOKEN and JIRA_PROJECT_KEY.
Robustness:
* fields that the project's create screen does not accept (often `priority` or `labels` in
  team-managed projects) are dropped and the request retried, with the value kept in the description;
* one failing alert never blocks the others - failures are reported per alert;
* duplicates are avoided: alerts that already carry a ticket in the feed, or that have an open
  ticket created earlier by FlowPilot, are skipped.
Without Jira credentials, tickets are written to the local SQL ticket table (system = "local").
"""
import hashlib

import httpx
from sqlalchemy import select

from .. import config
from ..db import Ticket
from .base import Plugin, PluginContext, action, register

PRIORITY_MAP = {"critical": "Highest", "highest": "Highest", "high": "High",
                "medium": "Medium", "low": "Low", "lowest": "Lowest"}


class JiraError(RuntimeError):
    pass


def jira_enabled() -> bool:
    return bool(config.JIRA_URL and config.JIRA_EMAIL and config.JIRA_API_TOKEN)


def _client() -> httpx.Client:
    return httpx.Client(base_url=config.JIRA_URL, auth=(config.JIRA_EMAIL, config.JIRA_API_TOKEN),
                        headers={"Accept": "application/json"}, timeout=30)


def _error_text(resp: httpx.Response) -> str:
    try:
        j = resp.json()
        parts = list(j.get("errorMessages") or []) + [f"{k}: {v}" for k, v in (j.get("errors") or {}).items()]
        return "; ".join(parts) or resp.text[:300]
    except ValueError:
        return resp.text[:300]


def create_issue(summary: str, description: str, priority: str, labels: list[str]) -> dict:
    fields = {"project": {"key": config.JIRA_PROJECT_KEY}, "summary": summary[:250],
              "description": description, "issuetype": {"name": config.JIRA_ISSUE_TYPE},
              "priority": {"name": priority}, "labels": labels}
    dropped: list[str] = []
    with _client() as c:
        for _ in range(3):
            resp = c.post("/rest/api/2/issue", json={"fields": fields})
            if resp.status_code < 400:
                key = resp.json()["key"]
                return {"key": key, "url": f"{config.JIRA_URL}/browse/{key}", "dropped_fields": dropped}
            errs = {}
            try:
                errs = resp.json().get("errors") or {}
            except ValueError:
                pass
            removable = [f for f in ("priority", "labels") if f in errs and f in fields]
            if resp.status_code == 400 and removable:
                for f in removable:
                    fields.pop(f)
                    dropped.append(f)
                if "priority" in removable:
                    fields["description"] = f"Priority: {priority}\n\n{fields['description']}"
                continue
            raise JiraError(f"Jira {resp.status_code}: {_error_text(resp)}")
    raise JiraError("Jira rejected the issue after removing optional fields")


def check_connection() -> dict:
    """Verify credentials and project access (used by the /api/integrations/jira/test endpoint)."""
    if not jira_enabled():
        return {"ok": False, "error": "JIRA_URL, JIRA_EMAIL and JIRA_API_TOKEN must be set"}
    with _client() as c:
        me = c.get("/rest/api/2/myself")
        if me.status_code >= 400:
            return {"ok": False, "error": f"authentication failed ({me.status_code}): {_error_text(me)}"}
        proj = c.get(f"/rest/api/2/project/{config.JIRA_PROJECT_KEY}")
        if proj.status_code >= 400:
            return {"ok": False, "user": me.json().get("displayName"),
                    "error": f"project {config.JIRA_PROJECT_KEY} not accessible ({proj.status_code}): "
                             f"{_error_text(proj)}"}
        types = [t.get("name") for t in proj.json().get("issueTypes", [])]
        ok = config.JIRA_ISSUE_TYPE in types
        return {"ok": ok, "user": me.json().get("displayName"), "project": proj.json().get("name"),
                "issue_types": types,
                **({} if ok else {"error": f"issue type '{config.JIRA_ISSUE_TYPE}' not in project; "
                                            f"set JIRA_ISSUE_TYPE to one of {types}"})}


def alert_label(uid: str | None) -> str:
    """Stable, Jira-safe label identifying the alert an issue was created for."""
    return "fp-alert-" + hashlib.sha1((uid or "").encode()).hexdigest()[:12]


def find_open_issue(label: str) -> str | None:
    """Return the key of an unresolved Jira issue carrying `label`, if any (Jira is the source of truth,
    so duplicates are avoided even after the local database is reset)."""
    jql = f'project = "{config.JIRA_PROJECT_KEY}" AND labels = "{label}" AND statusCategory != Done'
    body = {"jql": jql, "fields": ["key"], "maxResults": 1}
    with _client() as c:
        resp = c.post("/rest/api/3/search/jql", json=body)
        if resp.status_code == 404:  # older Jira deployments
            resp = c.post("/rest/api/2/search", json=body)
        if resp.status_code >= 400:
            raise JiraError(f"Jira search {resp.status_code}: {_error_text(resp)}")
        issues = resp.json().get("issues") or []
        return issues[0]["key"] if issues else None


def _description(a: dict) -> str:
    return (
        "Created automatically by FlowPilot for an operational alert.\n\n"
        f"Device: {a.get('device')}\nHostname: {a.get('hostname') or '-'}\nClient: {a.get('client')}\n"
        f"IP address: {a.get('ip_address')}\nMetric: {a.get('metric')}\nSeverity: {a.get('intensity')}\n"
        f"State: {a.get('bucket')}\nActive for: {a.get('active_minutes')} minutes\n"
        f"Last seen: {a.get('last_seen')}\nAlert UID: {a.get('uid')}"
    )


@register
class TicketingPlugin(Plugin):
    name = "ticketing"
    description = "Create incident tickets in Jira Cloud (REST API); local SQL ticket table when Jira is not configured."
    category = "integration"

    def mode(self) -> str:
        return "live" if jira_enabled() else "local"

    def _next_local_key(self, ctx: PluginContext) -> str:
        count = ctx.db.query(Ticket).filter(Ticket.system == "local").count()
        return f"LOCAL-{1001 + count}"

    @action(
        "create_for_alerts",
        "Create one ticket per alert. Skips alerts that already have a ticket (in the feed or created earlier).",
        inputs={"alerts": "list of alerts (from alerts.fetch)", "priority": "Highest|High|Medium|Low",
                "skip_existing": "skip alerts that already have a ticket (default true)",
                "title_prefix": "optional prefix for ticket titles"},
        outputs={"created": "list", "skipped": "list", "failed": "list", "created_count": "int"},
        required=("alerts",), side_effect=True,
    )
    def create_for_alerts(self, ctx: PluginContext, alerts=None, priority="High", skip_existing=True,
                          title_prefix="") -> dict:
        priority = PRIORITY_MAP.get(str(priority).lower(), "High")
        live = jira_enabled()
        created, skipped, failed = [], [], []
        for a in alerts or []:
            if skip_existing:
                if a.get("ticket_id"):
                    skipped.append({"device": a.get("device"), "reason": f"existing ticket {a['ticket_id']}"})
                    continue
                # only tickets in the system currently in use count (a local ticket is not a Jira issue)
                existing = ctx.db.scalar(select(Ticket).where(Ticket.alert_uid == a.get("uid"),
                                                              Ticket.system == ("jira" if live else "local"),
                                                              Ticket.status != "Closed"))
                if existing:
                    skipped.append({"device": a.get("device"), "reason": f"existing ticket {existing.key}"})
                    continue
                if live:
                    try:
                        remote = find_open_issue(alert_label(a.get("uid")))
                    except Exception:  # noqa: BLE001 - a search problem must not block ticketing
                        remote = None
                    if remote:
                        skipped.append({"device": a.get("device"), "reason": f"open Jira issue {remote}"})
                        continue
            title = (f"{title_prefix} " if title_prefix else "") + \
                f"[{a.get('intensity')}] {a.get('metric')} on {a.get('device')}"
            description = _description(a)
            url, note = None, None
            try:
                if live:
                    res = create_issue(title, description, priority,
                                       labels=["flowpilot", f"severity-{str(a.get('intensity', '')).lower()}",
                                               alert_label(a.get("uid"))])
                    key, url, system = res["key"], res["url"], "jira"
                    if res["dropped_fields"]:
                        note = f"project does not accept {', '.join(res['dropped_fields'])}; kept in description"
                else:
                    key, system = self._next_local_key(ctx), "local"
            except Exception as e:  # noqa: BLE001 - per-alert failure, keep going
                failed.append({"device": a.get("device"), "error": str(e)[:300]})
                continue
            t = Ticket(key=key, system=system, title=title, description=description, priority=priority,
                       alert_uid=a.get("uid"), device=a.get("device"), run_id=ctx.run_id, url=url)
            ctx.db.add(t)
            ctx.db.commit()
            created.append({"key": key, "title": title, "priority": priority, "system": system,
                            "device": a.get("device"), "url": url, **({"note": note} if note else {})})
        if failed and not created:
            raise JiraError(f"no ticket could be created: {failed[0]['error']}")
        return {"created": created, "skipped": skipped, "failed": failed, "created_count": len(created)}
