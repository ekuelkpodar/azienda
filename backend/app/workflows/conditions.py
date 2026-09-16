"""Safe condition-expression evaluator for CONDITION nodes.

Expressions are data, never code: no eval(), no attribute access, no function
calls. Supported forms:

    {"var": "lead.status"}                                   # value lookup (dotted path)
    {"op": "eq", "left": <expr|literal>, "right": <expr|literal>}
    {"and": [<expr>, ...]}  {"or": [...]}  {"not": <expr>}

Operators: eq, ne, gt, lt, gte, lte, contains, startswith, in.
Comparisons against a missing (None) value are False, except eq/ne.
"""

from __future__ import annotations

from typing import Any


class ExpressionError(ValueError):
    pass


_OPERATORS = {"eq", "ne", "gt", "lt", "gte", "lte", "contains", "startswith", "in"}


def resolve_path(context: dict[str, Any], path: str) -> Any:
    """Dotted lookup into the execution context. Missing -> None (never raises)."""
    current: Any = context
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _operand(node: Any, context: dict[str, Any]) -> Any:
    if isinstance(node, dict) and set(node.keys()) == {"var"}:
        return resolve_path(context, str(node["var"]))
    if isinstance(node, dict) and ("op" in node or "and" in node or "or" in node or "not" in node):
        return evaluate(node, context)
    return node  # literal


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if left is None or right is None:
        return False
    try:
        if op == "gt":
            return left > right
        if op == "lt":
            return left < right
        if op == "gte":
            return left >= right
        if op == "lte":
            return left <= right
        if op == "contains":
            return right in left
        if op == "startswith":
            return str(left).startswith(str(right))
        if op == "in":
            return left in right
    except TypeError:
        return False
    raise ExpressionError(f"unknown operator '{op}'")


def evaluate(expr: Any, context: dict[str, Any]) -> bool:
    """Evaluate an expression against the context. Returns a strict bool."""
    if not isinstance(expr, dict):
        raise ExpressionError(f"expression must be an object, got {type(expr).__name__}")
    if set(expr.keys()) == {"var"}:
        return bool(resolve_path(context, str(expr["var"])))
    if "and" in expr:
        items = expr["and"]
        if not isinstance(items, list):
            raise ExpressionError("'and' requires a list")
        return all(evaluate(e, context) for e in items)
    if "or" in expr:
        items = expr["or"]
        if not isinstance(items, list):
            raise ExpressionError("'or' requires a list")
        return any(evaluate(e, context) for e in items)
    if "not" in expr:
        return not evaluate(expr["not"], context)
    if "op" in expr:
        op = expr["op"]
        if op not in _OPERATORS:
            raise ExpressionError(f"unknown operator '{op}'")
        return _compare(op, _operand(expr.get("left"), context),
                        _operand(expr.get("right"), context))
    raise ExpressionError(f"unrecognized expression shape: {sorted(expr.keys())}")


def render(value: Any, context: dict[str, Any]) -> Any:
    """Interpolate ``{var.path}`` placeholders in strings using the context.

    Used for action params / notification messages. Non-string values pass
    through; missing paths render as empty string.
    """
    if isinstance(value, str):
        out = []
        i = 0
        while i < len(value):
            if value[i] == "{" and "}" in value[i:]:
                end = value.index("}", i)
                path = value[i + 1:end].strip()
                resolved = resolve_path(context, path)
                out.append("" if resolved is None else str(resolved))
                i = end + 1
            else:
                out.append(value[i])
                i += 1
        return "".join(out)
    if isinstance(value, dict):
        return {k: render(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, context) for v in value]
    return value
