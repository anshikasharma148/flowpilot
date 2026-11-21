"""LLM plugin - natural-language summaries of alerts / performance data."""
import json

from .. import llm
from .base import Plugin, PluginContext, action, register

SUMMARY_SYSTEM = (
    "You are a network operations analyst. Write a concise incident summary for an "
    "operations team in Markdown: a one-paragraph overview, then a bullet list of the most "
    "important issues (device, metric, severity, how long active), then recommended next "
    "actions. Use only the data provided; do not invent devices or numbers. Field meanings: active_minutes is "
    "how long the alert has been active (e.g. 10276 min is about 7 days); last_seen is the most recent time the "
    "alert was observed, NOT when it started. Under 250 words."
)


def _rule_alert_summary(data: dict) -> str:
    alerts = data.get("alerts", [])
    if not alerts:
        return "No matching unresolved alerts. All monitored devices are operating normally."
    by_int = data.get("by_intensity", {})
    lines = [
        f"**{len(alerts)} unresolved alert(s)** - "
        + ", ".join(f"{v} {k}" for k, v in sorted(by_int.items())),
        f"Critical (DOWN): **{data.get('critical_count', 0)}**.",
        "",
        "| Device | Client | Metric | Severity | Active (min) | Ticket |",
        "|---|---|---|---|---|---|",
    ]
    for a in sorted(alerts, key=lambda a: (a["intensity"] != "DOWN", -a["active_minutes"])):
        lines.append(f"| {a['device']} | {a['client']} | {a['metric']} | {a['intensity']} | "
                     f"{a['active_minutes']} | {a['ticket_id'] or '-'} |")
    lines += ["", "**Recommended actions:** dispatch field checks for DOWN links without tickets; "
              "review persisting HIGH alerts for capacity or interface errors."]
    return "\n".join(lines)


def _rule_perf_summary(data: dict) -> str:
    s = data.get("summary", {})
    return (f"**{s.get('zone')}**: {s.get('links')} links, average performance score "
            f"{s.get('avg_score')}, average uptime {s.get('avg_uptime')}%. "
            f"{s.get('links_down')} link(s) fully down, {s.get('links_degraded')} degraded. "
            f"Average latency {s.get('avg_latency_ms')} ms, packet drop {s.get('avg_packet_drop')}%, "
            f"jitter {s.get('avg_jitter_ms')} ms (links that were up).")


@register
class AIPlugin(Plugin):
    name = "ai"
    description = "LLM-powered summarisation (Claude), with a deterministic fallback."
    category = "intelligence"

    def mode(self) -> str:
        return "live" if llm.enabled() else "rules"

    @action("summarize_alerts", "Summarise a set of alerts for the operations team.",
            inputs={"data": "output of alerts.fetch"}, outputs={"summary": "markdown", "generated_by": "str"}, required=("data",))
    def summarize_alerts(self, ctx: PluginContext, data: dict | None = None) -> dict:
        data = data or {}
        slim = {k: data.get(k) for k in ("count", "critical_count", "by_intensity", "by_metric", "by_client")}
        slim["alerts"] = data.get("alerts", [])[:100]
        text = llm.text(SUMMARY_SYSTEM, "Alert data (JSON):\n" + json.dumps(slim, default=str))
        if text:
            return {"summary": text, "generated_by": llm.provider()}
        return {"summary": _rule_alert_summary(data), "generated_by": "rules"}

    @action("summarize_performance", "Summarise performance statistics.",
            inputs={"data": "output of performance.fetch"}, outputs={"summary": "markdown", "generated_by": "str"}, required=("data",))
    def summarize_performance(self, ctx: PluginContext, data: dict | None = None) -> dict:
        data = data or {}
        keep = ("site_code", "ip", "address", "circuit_id", "speed_mbps", "score", "uptime", "packet_drop",
                "latency_ms")
        slim = {"summary": data.get("summary"), "window": data.get("window"),
                "down_links": [{k: r.get(k) for k in keep} for r in data.get("down_links", [])],
                "degraded_links": [{k: r.get(k) for k in keep} for r in data.get("degraded_links", [])]}
        text = llm.text(
            "You are a network performance analyst. Summarise the performance of this zone in "
            "Markdown for an operations audience: overall health with the exact counts, what the down and "
            "degraded links have in common (sites, areas, link speeds), and recommendations. Do NOT list the "
            "links one by one - the report already contains complete tables of every down and degraded link. "
            "Use only the given data. Under 200 words.",
            json.dumps(slim, default=str))
        if text:
            return {"summary": text, "generated_by": llm.provider()}
        return {"summary": _rule_perf_summary(data), "generated_by": "rules"}
