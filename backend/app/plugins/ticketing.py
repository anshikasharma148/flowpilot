"""Ticketing plugin - Jira when configured, otherwise a local SQL ticket store."""
import httpx
from sqlalchemy import select

from .. import config
from ..db import Ticket
from .base import Plugin, PluginContext, action, register

PRIORITY_MAP = {"critical": "Highest", "highest": "Highest", "high": "High",
                "medium": "Medium", "low": "Low"}


def _jira_enabled() -> bool:
    return bool(config.JIRA_URL and config.JIRA_EMAIL and config.JIRA_API_TOKEN)


def _create_jira_issue(title: str, description: str, priority: str) -> str:
    resp = httpx.post(
        f"{config.JIRA_URL.rstrip('/')}/rest/api/2/issue",
        auth=(config.JIRA_EMAIL, config.JIRA_API_TOKEN),
        json={"fields": {"project": {"key": config.JIRA_PROJECT_KEY}, "summary": title[:250],
                         "description": description, "issuetype": {"name": "Task"},
                         "priority": {"name": priority}}},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["key"]


@register
class TicketingPlugin(Plugin):
    name = "ticketing"
    description = "Create incident tickets (Jira REST API when configured; local SQL ticket store otherwise)."
    category = "integration"

    def mode(self) -> str:
        return "live" if _jira_enabled() else "simulated"

    def _next_local_key(self, ctx: PluginContext) -> str:
        count = ctx.db.query(Ticket).count()
        return f"{config.JIRA_PROJECT_KEY}-{1001 + count}"

    @action(
        "create_for_alerts",
        "Create one ticket per alert. Skips alerts that already have a ticket (in the feed or locally).",
        inputs={"alerts": "list of alerts (from alerts.fetch)", "priority": "Highest|High|Medium|Low",
                "skip_existing": "skip alerts that already have a ticket (default true)",
                "title_prefix": "optional prefix for ticket titles"},
        outputs={"created": "list", "skipped": "list", "created_count": "int"},
        required=("alerts",), side_effect=True,
    )
    def create_for_alerts(self, ctx: PluginContext, alerts=None, priority="High", skip_existing=True,
                          title_prefix="") -> dict:
        priority = PRIORITY_MAP.get(str(priority).lower(), "High")
        created, skipped = [], []
        for a in alerts or []:
            if skip_existing:
                if a.get("ticket_id"):
                    skipped.append({"device": a.get("device"), "reason": f"existing ticket {a['ticket_id']}"})
                    continue
                existing = ctx.db.scalar(select(Ticket).where(Ticket.alert_uid == a.get("uid"),
                                                              Ticket.status != "Closed"))
                if existing:
                    skipped.append({"device": a.get("device"), "reason": f"existing ticket {existing.key}"})
                    continue
            title = (f"{title_prefix} " if title_prefix else "") + \
                f"[{a.get('intensity')}] {a.get('metric')} on {a.get('device')}"
            description = (
                f"Device: {a.get('device')}\nHostname: {a.get('hostname') or '-'}\nClient: {a.get('client')}\n"
                f"IP: {a.get('ip_address')}\nMetric: {a.get('metric')}\nSeverity: {a.get('intensity')}\n"
                f"State: {a.get('bucket')}\nActive for: {a.get('active_minutes')} minutes\n"
                f"Last seen: {a.get('last_seen')}\nAlert UID: {a.get('uid')}"
            )
            system = "local"
            if _jira_enabled():
                key = _create_jira_issue(title, description, priority)
                system = "jira"
            else:
                key = self._next_local_key(ctx)
            t = Ticket(key=key, system=system, title=title, description=description, priority=priority,
                       alert_uid=a.get("uid"), device=a.get("device"), run_id=ctx.run_id)
            ctx.db.add(t)
            ctx.db.flush()
            created.append({"key": key, "title": title, "priority": priority, "system": system,
                            "device": a.get("device")})
        ctx.db.commit()
        return {"created": created, "skipped": skipped, "created_count": len(created)}
