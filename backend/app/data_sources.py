"""Read-only access to the hardcoded alert / performance JSON files."""
import json
from functools import lru_cache
from pathlib import Path

from .config import ALERTS_FILE, PERFORMANCE_FILE


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _mtime(path: Path) -> float:
    return path.stat().st_mtime


@lru_cache(maxsize=4)
def _alerts_cached(mtime: float) -> list[dict]:
    return _load(ALERTS_FILE).get("data", [])


@lru_cache(maxsize=4)
def _perf_cached(mtime: float) -> dict:
    return _load(PERFORMANCE_FILE)


def load_alerts() -> list[dict]:
    """All alerts; reloaded automatically when the file changes."""
    return [dict(a) for a in _alerts_cached(_mtime(ALERTS_FILE))]


def load_performance() -> dict:
    raw = _perf_cached(_mtime(PERFORMANCE_FILE))
    rows = raw["data"]["performance_stats_table"]["ip_table_info"]["data"]
    return {"window": raw.get("date", {}), "rows": [dict(r) for r in rows]}


def available_zones() -> list[str]:
    """Zone names without the trailing 'Zone', title-cased, e.g. ['Mangaluru', 'Shivamogga']."""
    zones = {r.get("site_zone", "") for r in load_performance()["rows"]}
    return sorted({normalize_zone(z) for z in zones if z})


def normalize_zone(name: str) -> str:
    name = (name or "").strip()
    if name.lower().endswith(" zone"):
        name = name[:-5]
    return name.strip().title()
