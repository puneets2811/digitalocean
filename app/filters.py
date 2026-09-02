from __future__ import annotations

from typing import Any


def _get_by_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def field_matches(filter_value: str | None, actual: str) -> bool:
    if filter_value is None or filter_value == "" or filter_value == "*":
        return True
    return filter_value == actual


def payload_conditions_match(conditions: list[dict[str, Any]], payload: dict[str, Any]) -> bool:
    for condition in conditions:
        path = condition.get("path")
        if not path:
            return False
        expected = condition.get("eq")
        actual = _get_by_path(payload, path)
        if actual != expected:
            return False
    return True


def subscription_matches(
    *,
    type_filter: str,
    source_filter: str,
    payload_conditions: list[dict[str, Any]],
    event_type: str,
    event_source: str,
    payload: dict[str, Any],
) -> bool:
    return (
        field_matches(type_filter, event_type)
        and field_matches(source_filter, event_source)
        and payload_conditions_match(payload_conditions, payload)
    )
