"""Safe reference resolution and condition evaluation for workflow definitions.

References
----------
Step inputs may reference runtime values:
  * ``"$params.zone"``             -> whole value (keeps type: list, dict, int ...)
  * ``"$steps.fetch.alerts"``      -> output of an earlier step
  * ``"Report for {{params.zone}}"`` -> string interpolation

Conditions
----------
A step's ``when`` is a Python-like boolean expression evaluated with a strict
AST whitelist (no attribute access to dunders, no imports, only a few functions):
  ``len(steps.fetch.critical) > 0 and params.create_tickets``
"""
import ast
import operator
import re
from typing import Any

_TEMPLATE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def lookup(path: str, ctx: dict) -> Any:
    cur: Any = ctx
    for part in path.strip().split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def resolve(value: Any, ctx: dict) -> Any:
    if isinstance(value, str):
        if value.startswith("$") and " " not in value:
            return lookup(value[1:], ctx)
        if "{{" in value:
            def repl(m: re.Match) -> str:
                v = lookup(m.group(1), ctx)
                if v is None:
                    return ""
                if isinstance(v, (list, tuple)):
                    return ", ".join(str(x) for x in v)
                return str(v)
            return _TEMPLATE.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        out = []
        for v in value:
            r = resolve(v, ctx)
            # ["$params.metrics"] where metrics is itself a list -> flatten (a common LLM slip)
            if isinstance(v, str) and v.startswith("$") and isinstance(r, list):
                out.extend(r)
            else:
                out.append(r)
        return out
    return value


_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Mod: operator.mod}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_FUNCS = {"len": len, "any": any, "all": all, "bool": bool, "int": int, "str": str,
          "lower": lambda s: str(s).lower()}


class ExpressionError(ValueError):
    pass


def evaluate(expr: str, ctx: dict) -> Any:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"invalid expression {expr!r}: {e}") from e
    return _eval(tree.body, ctx)


def _eval(node: ast.AST, ctx: dict) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ("true", "True"):
            return True
        if node.id in ("false", "False"):
            return False
        if node.id in ("none", "None", "null"):
            return None
        return ctx.get(node.id)
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ExpressionError("private attributes are not allowed")
        base = _eval(node.value, ctx)
        return base.get(node.attr) if isinstance(base, dict) else None
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, ctx)
        key = _eval(node.slice, ctx)
        try:
            return base[key]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.BoolOp):
        vals = (_eval(v, ctx) for v in node.values)
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return not v
        if isinstance(node.op, ast.USub):
            return -v
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return _BIN[type(node.op)](_eval(node.left, ctx), _eval(node.right, ctx))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, ctx)
            try:
                if not _CMP[type(op)](left, right):
                    return False
            except TypeError:
                return False
            left = right
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        args = [_eval(a, ctx) for a in node.args]
        try:
            return _FUNCS[node.func.id](*args)
        except TypeError:
            return 0 if node.func.id == "len" else None
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, ctx) for e in node.elts]
    raise ExpressionError(f"unsupported expression element: {type(node).__name__}")
