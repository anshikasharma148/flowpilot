"""Parameter validation against a workflow's ``params_schema``.

Schema format (per parameter)::

    {"type": "string|integer|number|boolean|list|email_list",
     "required": bool, "default": any, "description": str,
     "enum": [...], "validator": "zone", "min": n, "max": n}
"""
import re
from typing import Any

from ..data_sources import available_zones, normalize_zone

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ParamResult:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.missing: list[str] = []
        self.errors: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.missing and not self.errors


def _coerce(name: str, spec: dict, value: Any, res: ParamResult) -> Any:
    t = spec.get("type", "string")
    try:
        if t == "integer":
            value = int(float(value))
        elif t == "number":
            value = float(value)
        elif t == "boolean":
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes", "y", "on")
            else:
                value = bool(value)
        elif t in ("list", "email_list"):
            if isinstance(value, str):
                value = [v for v in re.split(r"[,;\s]+" if t == "email_list" else r"[,;]+", value) if v.strip()]
            value = [str(v).strip() for v in (value or [])]
            if t == "email_list":
                bad = [v for v in value if not EMAIL_RE.match(v)]
                if bad:
                    res.errors.append(f"'{name}': invalid email address(es) {', '.join(bad)}")
        else:
            value = str(value).strip()
    except (TypeError, ValueError):
        res.errors.append(f"'{name}' must be of type {t} (got {value!r})")
        return value

    if "enum" in spec:
        canon = {str(e).upper(): e for e in spec["enum"]}
        items = value if isinstance(value, list) else [value]
        bad = [i for i in items if str(i).upper() not in canon]
        if bad:
            res.errors.append(f"'{name}': {', '.join(map(str, bad))} not allowed; choose from {', '.join(spec['enum'])}")
        else:
            value = [canon[str(i).upper()] for i in items] if isinstance(value, list) else canon[str(value).upper()]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "min" in spec and value < spec["min"]:
            res.errors.append(f"'{name}' must be >= {spec['min']}")
        if "max" in spec and value > spec["max"]:
            res.errors.append(f"'{name}' must be <= {spec['max']}")
    if spec.get("validator") == "zone" and value and str(value).lower() not in ("all", "*"):
        zones = available_zones()
        z = normalize_zone(str(value))
        if z not in zones:
            res.errors.append(f"No performance data for zone '{value}'. Available zones: {', '.join(zones)}")
        else:
            value = z
    return value


def validate_params(schema: dict, params: dict | None) -> ParamResult:
    res = ParamResult()
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    for name, spec in (schema or {}).items():
        if name in params:
            res.values[name] = _coerce(name, spec, params[name], res)
        elif "default" in spec:
            res.values[name] = spec["default"]
        elif spec.get("required"):
            res.missing.append(name)
        else:
            res.values[name] = None
    # keep unknown extras (dynamic workflows may use them) without validation
    for k, v in params.items():
        res.values.setdefault(k, v)
    return res
