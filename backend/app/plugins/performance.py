"""Performance statistics plugin - reads performance_stats.json."""
from datetime import datetime, timedelta
from statistics import mean

from ..data_sources import available_zones, load_performance, normalize_zone
from .base import Plugin, PluginContext, action, register


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@register
class PerformancePlugin(Plugin):
    name = "performance"
    description = "Link performance statistics (uptime, latency, packet drop, jitter, CPU, memory) per site."
    category = "data-source"

    @action(
        "fetch",
        "Collect performance statistics for a zone (or all zones) over a time window.",
        inputs={"zone": "zone / region name, e.g. 'Mangaluru' (default: all zones)",
                "hours": "look-back window in hours (default 6)",
                "isp": "filter by ISP (optional)"},
        outputs={"rows": "list", "summary": "dict", "worst_links": "list", "window": "dict"},
    )
    def fetch(self, ctx: PluginContext, zone=None, hours=6, isp=None) -> dict:
        data = load_performance()
        rows = data["rows"]
        zone_norm = normalize_zone(zone) if zone and str(zone).lower() not in ("all", "*") else None
        if zone_norm:
            known = available_zones()
            if zone_norm not in known:
                raise ValueError(f"No performance data for zone '{zone}'. Available zones: {', '.join(known)}")
            rows = [r for r in rows if normalize_zone(r.get("site_zone", "")) == zone_norm]
        if isp:
            rows = [r for r in rows if str(r.get("isp", "")).upper() == str(isp).upper()]

        # The dataset is one pre-aggregated snapshot. The requested look-back window is
        # anchored at the dataset end time and reported alongside the actual coverage.
        win = data["window"]
        hours = float(hours or 6)
        try:
            end = datetime.fromisoformat(win["end"])
            req_start = end - timedelta(hours=hours)
            requested = {"start": req_start.isoformat(), "end": end.isoformat(), "hours": hours}
        except (KeyError, ValueError):
            requested = {"hours": hours}

        rows_out = [{
            "site_code": r.get("site_code"), "ip": r.get("IP"), "zone": normalize_zone(r.get("site_zone", "")),
            "isp": r.get("isp"), "link_type": r.get("link_type"), "speed_mbps": r.get("link_speed (Mbps)"),
            "uptime": _f(r.get("uptime")), "score": _f(r.get("performance_score")),
            "latency_ms": _f(r.get("avg_latency")), "packet_drop": _f(r.get("avg_packet_drop")),
            "jitter_ms": _f(r.get("avg_jitter")), "cpu": _f(r.get("avg_cpu")), "memory": _f(r.get("avg_memory")),
            "circuit_id": r.get("circuit_id"), "address": (r.get("site_address") or "").replace("\n", " "),
        } for r in rows]

        n = len(rows_out)
        down = [r for r in rows_out if r["uptime"] == 0]
        summary = {
            "zone": zone_norm or "All zones",
            "links": n,
            "links_down": len(down),
            "links_healthy": sum(1 for r in rows_out if r["score"] >= 80),
            "links_degraded": sum(1 for r in rows_out if 0 < r["score"] < 80),
            "avg_uptime": round(mean([r["uptime"] for r in rows_out]), 2) if n else 0,
            "avg_score": round(mean([r["score"] for r in rows_out]), 2) if n else 0,
            "avg_latency_ms": round(mean([r["latency_ms"] for r in rows_out if r["uptime"] > 0] or [0]), 2),
            "avg_packet_drop": round(mean([r["packet_drop"] for r in rows_out if r["uptime"] > 0] or [0]), 2),
            "avg_jitter_ms": round(mean([r["jitter_ms"] for r in rows_out if r["uptime"] > 0] or [0]), 2),
        }
        worst = sorted(rows_out, key=lambda r: (r["score"], -r["packet_drop"]))[:10]
        return {"rows": rows_out, "summary": summary, "worst_links": worst,
                "window": {"data": win, "requested": requested}}

    @action("zones", "List zones that have performance data.", outputs={"zones": "list"})
    def zones(self, ctx: PluginContext) -> dict:
        return {"zones": available_zones()}
