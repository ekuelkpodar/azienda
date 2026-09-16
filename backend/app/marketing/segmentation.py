"""Rule-based segmentation engine (pure logic, no I/O).

An audience filter is a rule tree evaluated against contact attribute dicts:

    {"all": [rule, ...]}   # every rule must match
    {"any": [rule, ...]}   # at least one rule must match
    {"not": rule}          # negation
    {"field": "tags", "op": "contains", "value": "vip"}
    {"field": "custom.plan", "op": "eq", "value": "pro"}

Leaf operators: eq, ne, gt, gte, lt, lte, in, not_in, contains, starts_with,
ends_with, is_set, is_empty. ``field`` supports dotted paths (e.g.
``custom.plan``). A missing field evaluates as ``None``.

Contacts are supplied by the caller (the CRM package will feed them once its
service exists) — this module never reaches across the package boundary.
"""
from __future__ import annotations

from typing import Any

_LEAF_OPS = {
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in",
    "contains", "starts_with", "ends_with", "is_set", "is_empty",
}


class SegmentFilterError(ValueError):
    """Raised when a filter spec is malformed."""


def _resolve_field(contact: dict[str, Any], field: str) -> Any:
    current: Any = contact
    for part in field.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _match_leaf(rule: dict[str, Any], contact: dict[str, Any]) -> bool:
    field = rule.get("field")
    op = rule.get("op")
    if not isinstance(field, str) or op not in _LEAF_OPS:
        raise SegmentFilterError(f"malformed leaf rule: {rule!r}")
    actual = _resolve_field(contact, field)
    value = rule.get("value")

    if op == "eq":
        return bool(actual == value)
    if op == "ne":
        return bool(actual != value)
    if op == "is_set":
        return bool(actual is not None)
    if op == "is_empty":
        return actual is None or actual == "" or actual == []
    if actual is None:
        return False
    if op == "gt":
        return bool(actual > value)
    if op == "gte":
        return bool(actual >= value)
    if op == "lt":
        return bool(actual < value)
    if op == "lte":
        return bool(actual <= value)
    if op == "in":
        return bool(actual in (value or []))
    if op == "not_in":
        return bool(actual not in (value or []))
    if op == "contains":
        if isinstance(actual, (list, tuple, set)):
            return value in actual
        return str(value) in str(actual)
    if op == "starts_with":
        return str(actual).startswith(str(value))
    if op == "ends_with":
        return str(actual).endswith(str(value))
    raise SegmentFilterError(f"unknown operator: {op}")  # pragma: no cover


def evaluate_filter(filter_spec: dict[str, Any], contact: dict[str, Any]) -> bool:
    """Evaluate one rule tree against one contact. Empty filter matches everyone."""
    if not filter_spec:
        return True
    if "all" in filter_spec:
        rules = filter_spec["all"]
        if not isinstance(rules, list):
            raise SegmentFilterError("'all' must be a list")
        return all(evaluate_filter(r, contact) for r in rules)
    if "any" in filter_spec:
        rules = filter_spec["any"]
        if not isinstance(rules, list):
            raise SegmentFilterError("'any' must be a list")
        return any(evaluate_filter(r, contact) for r in rules)
    if "not" in filter_spec:
        return not evaluate_filter(filter_spec["not"], contact)
    if "field" in filter_spec:
        return _match_leaf(filter_spec, contact)
    raise SegmentFilterError(f"malformed filter spec: {filter_spec!r}")


def validate_filter(filter_spec: dict[str, Any]) -> None:
    """Raise SegmentFilterError if the spec is malformed (validates shape only)."""
    evaluate_filter(filter_spec, {})  # shape errors surface here


def segment_contacts(
    filter_spec: dict[str, Any], contacts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return the contacts matching the filter. Pure function."""
    validate_filter(filter_spec)
    return [c for c in contacts if evaluate_filter(filter_spec, c)]
