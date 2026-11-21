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
        lines += ["| Ticket | System | Priority | Device | Title |", "|---|---|---|---|---|"]
        for t in created:
            key = f"[{t['key']}]({t['url']})" if t.get("url") else t["key"]
            lines.append(f"| {key} | {t.get('system', '')} | {t['priority']} | {t['device']} | {t['title']} |")
    else:
        lines.append("No new tickets were created.")
    failed = tickets.get("failed") or []
    if failed:
        lines += ["", f"**{len(failed)} ticket(s) could not be created:**"]
        lines += [f"- {f['device']}: {f['error']}" for f in failed]
    skipped = tickets.get("skipped") or []
    if skipped:
        lines.append("")
        lines.append("Skipped (already ticketed): " + ", ".join(f"{s['device']} ({s['reason']})" for s in skipped))
    return lines


def _alert_table(alerts: list[dict]) -> list[str]:
    """Every alert in scope - never a sample."""
    if not alerts:
        return []
    lines = ["", f"## Alerts in scope ({len(alerts)})",
             "| Device | Hostname | Client | Metric | Severity | Active (min) | IP | Ticket |",
             "|---|---|---|---|---|---|---|---|"]
    for a in sorted(alerts, key=lambda a: (a.get("intensity") != "DOWN", -int(a.get("active_minutes") or 0))):
        lines.append(f"| {a.get('device')} | {a.get('hostname') or '-'} | {a.get('client')} | {a.get('metric')} | "
                     f"{a.get('intensity')} | {a.get('active_minutes')} | {a.get('ip_address')} | "
                     f"{a.get('ticket_id') or '-'} |")
    return lines


def _link_table(title: str, links: list[dict], empty: str) -> list[str]:
    lines = ["", f"## {title} ({len(links)})"]
    if not links:
        return lines + [empty]
    lines += ["| # | Site | IP | Address | Circuit | Speed (Mbps) | Uptime % | Score | Latency ms | Drop % |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(links, 1):
        addr = (r.get("address") or "-").replace("|", "/")
        lines.append(f"| {i} | {r['site_code']} | {r['ip']} | {addr} | {r['circuit_id']} | {r.get('speed_mbps')} | "
                     f"{r['uptime']:.1f} | {r['score']:.0f} | {r['latency_ms']:.0f} | {r['packet_drop']:.0f} |")
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
        lines += _alert_table(data.get("alerts") or [])
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
        ]
        lines += _link_table("Links down", data.get("down_links", []), "No link is down.")
        lines += _link_table("Degraded links (score below 80)", data.get("degraded_links", []),
                             "No link is degraded.")
        lines += ["", f"The remaining {s.get('links_healthy')} link(s) are healthy (score 80 or above)."]
        return {"markdown": "\n".join(lines), "title": title}
