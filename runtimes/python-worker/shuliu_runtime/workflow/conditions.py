from __future__ import annotations

from collections.abc import Mapping


class WorkflowValueError(ValueError):
    """Raised when a workflow input/output reference cannot be resolved."""


_MISSING = object()


def evaluate_condition(
    condition: Mapping[str, object], variables: Mapping[str, object]
) -> bool:
    path = condition.get("path")
    operator = condition.get("operator")
    if not isinstance(path, str) or not isinstance(operator, str):
        raise WorkflowValueError("workflow condition is invalid")
    actual = resolve_path(path, variables)
    if operator == "exists":
        return actual is not _MISSING
    if operator == "truthy":
        if actual is _MISSING:
            return False
        return bool(actual)
    if actual is _MISSING:
        raise WorkflowValueError(f"workflow condition path {path} is missing")
    expected = condition.get("value")
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    raise WorkflowValueError(f"workflow condition operator {operator} is unsupported")


def resolve_references(value: object, variables: Mapping[str, object]) -> object:
    if isinstance(value, Mapping):
        if set(value) == {"$ref"}:
            path = value.get("$ref")
            if not isinstance(path, str):
                raise WorkflowValueError("workflow reference must be a string")
            resolved = resolve_path(path, variables)
            if resolved is _MISSING:
                raise WorkflowValueError(f"workflow reference path {path} is missing")
            return resolved
        return {
            str(key): resolve_references(child, variables)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [resolve_references(child, variables) for child in value]
    return value


def resolve_path(path: str, variables: Mapping[str, object]) -> object:
    current: object = variables
    for part in path.split("."):
        if not part:
            return _MISSING
        if isinstance(current, Mapping):
            current = current.get(part, _MISSING)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else _MISSING
        else:
            return _MISSING
        if current is _MISSING:
            return _MISSING
    return current
