"""Alert source plugin - reads the operational alert feed (alerts.json)."""
from collections import Counter

from ..data_sources import load_alerts
from .base import Plugin, PluginContext, action, register


def _upper_list(v) -> list[str] | None:
    if v in (None, "", []):
        return None
    if isinstance(v, str):
        v = [x for x in v.replace(";", ",").split(",")]
    return [str(x).strip().upper() for x in v if str(x).strip()]


def normalize(a: dict) -> dict:
    metric = a.get("metric") or ""
    return {
        "uid": a.get("uid"),
        "device": a.get("device/link"),
        "hostname": a.get("hostname"),
        "client": a.get("client"),
        "metric": metric,
        "metric_type": metric.split(":")[0],
        "intensity": (a.get("intensity") or "").upper(),
        "bucket": a.get("bucket"),
        "active_minutes": int(a.get("active_since") or 0),
        "last_seen": a.get("last_seen"),
        "acknowledged": bool(a.get("acknowledged")),
        "ticket_id": a.get("ticket_id") or "",
        "ip_address": a.get("ip_address"),
        "label": a.get("label"),
    }


@register
class AlertsPlugin(Plugin):
    name = "alerts"
    description = "Operational alert feed (network devices, links, agents)."
    category = "data-source"

    @action(
        "fetch",
        "Fetch active (unresolved) alerts with optional filters.",
        inputs={
            "intensities": "list of severities to include, e.g. ['DOWN','HIGH'] (default: all)",
            "metrics": "list of metric types, e.g. ['LINK','INTERFACE'] (default: all)",
            "clients": "list of client names (default: all)",
            "min_active_minutes": "only alerts active for at least N minutes",
            "acknowledged": "true/false to filter on acknowledgement (default: both)",
            "critical_intensities": "severities treated as critical (default: ['DOWN'])",
        },
        outputs={"alerts": "list", "count": "int", "critical": "list", "critical_count": "int",
                 "by_intensity": "dict", "by_metric": "dict", "by_client": "dict"},
    )
    def fetch(self, ctx: PluginContext, intensities=None, metrics=None, clients=None,
              min_active_minutes=None, acknowledged=None, critical_intensities=None) -> dict:
        intens = _upper_list(intensities)
        mets = _upper_list(metrics)
        cls = _upper_list(clients)
        crit = _upper_list(critical_intensities) or ["DOWN"]
        out = []
        for raw in load_alerts():
            a = normalize(raw)
            if intens and a["intensity"] not in intens:
                continue
            if mets and a["metric_type"].upper() not in mets:
                continue
            if cls and (a["client"] or "").upper() not in cls:
                continue
            if min_active_minutes not in (None, "") and float(min_active_minutes) > 0 \
                    and a["active_minutes"] <= float(min_active_minutes):
                continue
            if acknowledged is not None and a["acknowledged"] != bool(acknowledged):
                continue
            out.append(a)
        critical = [a for a in out if a["intensity"] in crit]
        return {
            "alerts": out,
            "count": len(out),
            "critical": critical,
            "critical_count": len(critical),
            "by_intensity": dict(Counter(a["intensity"] for a in out)),
            "by_metric": dict(Counter(a["metric_type"] for a in out)),
            "by_client": dict(Counter(a["client"] for a in out)),
        }
