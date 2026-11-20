"""Report builder plugin - turns step outputs into Markdown reports."""
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import TIMEZONE

from .base import Plugin, PluginContext, action, register


def _now() -> str:
    # use the configured timezone (servers usually run on UTC)
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M")


def _ticket_section(tickets: dict | None) -> list[str]:
    if not tickets:
        return []
    lines = ["", "## Tickets"]
    created = tickets.get("created") or []
    if created:
        lines += ["| Ticket | Priority | Device | Title |", "|---|---|---|---|"]
        lines += [f"| {t['key']} | {t['priority']} | {t['device']} | {t['title']} |" for t in created]
    else:
        lines.append("No new tickets were created.")
    skipped = tickets.get("skipped") or []
    if skipped:
        lines.append("")
        lines.append("Skipped (already ticketed): " + ", ".join(f"{s['device']} ({s['reason']})" for s in skipped))
    return lines


@register
class ReportPlugin(Plugin):
    name = "report"
    description = "Builds Markdown reports from workflow data."
    category = "utility"

    @action("alert_report", "Build an alert summary report (optionally including created tickets).",
            inputs={"title": "report title", "summary": "summary text", "data": "alerts.fetch output",
                    "tickets": "ticketing.create_for_alerts output (optional)"},
            outputs={"markdown": "str", "title": "str"}, required=("data",))
    def alert_report(self, ctx: PluginContext, title="Alert Summary Report", summary="", data=None,
                     tickets=None) -> dict:
        data = data or {}
        lines = [f"# {title}", f"_Generated {_now()}_", "",
                 f"**Alerts in scope:** {data.get('count', 0)} &nbsp; | &nbsp; "
                 f"**Critical:** {data.get('critical_count', 0)}", "", "## Summary", summary or "-"]
        by_int = data.get("by_intensity") or {}
        if by_int:
            lines += ["", "## Breakdown", "| Severity | Count |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in sorted(by_int.items())]
        lines += _ticket_section(tickets)
        return {"markdown": "\n".join(lines), "title": title}

    @action("ticket_report", "Build a report of tickets created for alerts.",
            inputs={"data": "alerts.fetch output", "tickets": "ticketing output", "criteria": "text"},
            outputs={"markdown": "str", "title": "str"}, required=("data",))
    def ticket_report(self, ctx: PluginContext, data=None, tickets=None, criteria="") -> dict:
        data = data or {}
        title = "Ticket Creation Report"
        lines = [f"# {title}", f"_Generated {_now()}_", "", f"**Criteria:** {criteria or '-'}", "",
                 f"**Matching alerts:** {data.get('count', 0)}"]
        if not data.get("count"):
            lines.append("\nNo alerts matched the criteria - no tickets needed.")
        lines += _ticket_section(tickets or {"created": [], "skipped": []})
        return {"markdown": "\n".join(lines), "title": title}

    @action("performance_report", "Build a performance report for a zone.",
            inputs={"summary": "summary text", "data": "performance.fetch output"},
            outputs={"markdown": "str", "title": "str"}, required=("data",))
    def performance_report(self, ctx: PluginContext, summary="", data=None) -> dict:
        data = data or {}
        s = data.get("summary", {})
        win = data.get("window", {})
        req = win.get("requested", {})
        actual = win.get("data", {})
        hrs = req.get("hours")
        hrs = f"{hrs:g}" if isinstance(hrs, (int, float)) else "?"
        title = f"Performance Report - {s.get('zone', 'All zones')} (last {hrs}h)"
        lines = [
            f"# {title}", f"_Generated {_now()}_", "",
            f"**Requested window:** {req.get('start', '?')} -> {req.get('end', '?')}  ",
            f"**Data coverage:** {actual.get('start', '?')} -> {actual.get('end', '?')} (aggregated snapshot)",
            "", "## Summary", summary or "-", "", "## Key metrics", "| Metric | Value |", "|---|---|",
            f"| Links | {s.get('links')} |", f"| Healthy (score >= 80) | {s.get('links_healthy')} |",
            f"| Degraded | {s.get('links_degraded')} |", f"| Down | {s.get('links_down')} |",
            f"| Avg uptime | {s.get('avg_uptime')} % |", f"| Avg performance score | {s.get('avg_score')} |",
            f"| Avg latency | {s.get('avg_latency_ms')} ms |", f"| Avg packet drop | {s.get('avg_packet_drop')} % |",
            f"| Avg jitter | {s.get('avg_jitter_ms')} ms |",
            "", "## Worst performing links",
            "| Site | IP | Score | Uptime % | Latency ms | Drop % | Circuit |", "|---|---|---|---|---|---|---|",
        ]
        for r in data.get("worst_links", []):
            lines.append(f"| {r['site_code']} | {r['ip']} | {r['score']:.0f} | {r['uptime']:.1f} | "
                         f"{r['latency_ms']:.0f} | {r['packet_drop']:.0f} | {r['circuit_id']} |")
        return {"markdown": "\n".join(lines), "title": title}
