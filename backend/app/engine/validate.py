"""Static validation of workflow definitions (used for custom & AI-generated workflows)."""
import re

from ..plugins import get_plugin
from .expr import ExpressionError, evaluate

_REF = re.compile(r"\$steps\.([A-Za-z0-9_]+)|\{\{\s*steps\.([A-Za-z0-9_]+)")
STEP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")


def _refs(value) -> set[str]:
    out: set[str] = set()
    if isinstance(value, str):
        for m in _REF.finditer(value):
            out.add(m.group(1) or m.group(2))
    elif isinstance(value, dict):
        for v in value.values():
            out |= _refs(v)
    elif isinstance(value, list):
        for v in value:
            out |= _refs(v)
    return out


def validate_definition(definition: dict) -> list[str]:
    errors: list[str] = []
    steps = (definition or {}).get("steps")
    if not isinstance(steps, list) or not steps:
        return ["definition.steps must be a non-empty list"]
    ids = [s.get("id") for s in steps]
    all_ids = set(ids)
    if len(all_ids) != len(ids):
        errors.append("step ids must be unique")
    seen: set[str] = set()
    side_effects: list[tuple] = []
    for i, s in enumerate(steps):
        sid = s.get("id")
        where = f"step {i + 1} ({sid})"
        if not sid or not STEP_ID.match(str(sid)):
            errors.append(f"{where}: id must be alphanumeric/underscore and start with a letter")
        try:
            plugin = get_plugin(s.get("plugin", ""))
            spec = plugin.actions.get(s.get("action"))
            if spec is None:
                errors.append(f"{where}: plugin '{plugin.name}' has no action '{s.get('action')}'")
            else:
                inputs = s.get("inputs") or {}
                missing = [r for r in spec.required if inputs.get(r) in (None, "", [], {})]
                if missing:
                    errors.append(f"{where}: {plugin.name}.{spec.name} is missing required input(s): "
                                  f"{', '.join(missing)}")
                if spec.side_effect:
                    key = (plugin.name, spec.name, repr(sorted(inputs.items(), key=str)))
                    if any(k[:2] == key[:2] for k in side_effects):
                        errors.append(f"{where}: duplicate {plugin.name}.{spec.name} step - the workflow would "
                                      f"perform this action twice; keep only one")
                    side_effects.append(key)
        except KeyError:
            errors.append(f"{where}: unknown plugin '{s.get('plugin')}'")
        for ref in _refs(s.get("inputs", {})) | _refs(s.get("when", "")):
            if ref not in seen:
                errors.append(f"{where}: references step '{ref}' which does not run before it")
        for expr in [s.get("when")] + [b.get("when") for b in s.get("branches", [])]:
            if expr:
                try:
                    evaluate(expr, {"params": {}, "steps": {}})
                except ExpressionError as e:
                    errors.append(f"{where}: {e}")
        for b in s.get("branches", []):
            if b.get("goto") not in all_ids and b.get("goto") != "END":
                errors.append(f"{where}: branch goto '{b.get('goto')}' is not a step id")
        if s.get("on_error", "stop") not in ("stop", "continue"):
            errors.append(f"{where}: on_error must be 'stop' or 'continue'")
        seen.add(sid)
    return errors
