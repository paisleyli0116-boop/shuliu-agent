from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow definition is invalid or unsafe to execute."""


_STEP_ID = {"type": "string", "pattern": "^(?!END$)[A-Za-z][A-Za-z0-9_-]*$"}
_TARGET = {"anyOf": [_STEP_ID, {"const": "END"}]}
_RETRY = {
    "type": "object",
    "properties": {"max_retries": {"type": "integer", "minimum": 1}},
    "required": ["max_retries"],
    "additionalProperties": False,
}
_ON_FAILURE = {
    "oneOf": [
        {
            "type": "object",
            "properties": {"action": {"const": "fail"}},
            "required": ["action"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"action": {"const": "pause"}},
            "required": ["action"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "action": {"const": "fallback"},
                "step_id": _STEP_ID,
            },
            "required": ["action", "step_id"],
            "additionalProperties": False,
        },
    ]
}
_STEP_COMMON = {
    "id": _STEP_ID,
    "retry": _RETRY,
    "on_failure": _ON_FAILURE,
}
_CONDITION = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "operator": {"const": "eq"},
                "value": {},
            },
            "required": ["path", "operator", "value"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "operator": {"const": "ne"},
                "value": {},
            },
            "required": ["path", "operator", "value"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "operator": {"enum": ["exists", "truthy"]},
            },
            "required": ["path", "operator"],
            "additionalProperties": False,
        },
    ]
}
_WORKFLOW_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "workflow_id": {"type": "string", "minLength": 1},
        "workflow_version_id": {"type": "string", "minLength": 1},
        "input_schema": {"type": "object"},
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            **_STEP_COMMON,
                            "type": {"const": "tool"},
                            "tool_id": {"type": "string", "minLength": 1},
                            "arguments": {"type": "object"},
                            "next": _TARGET,
                        },
                        "required": ["id", "type", "tool_id", "arguments"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            **_STEP_COMMON,
                            "type": {"const": "condition"},
                            "condition": _CONDITION,
                            "then": _TARGET,
                            "else": _TARGET,
                        },
                        "required": ["id", "type", "condition", "then", "else"],
                        "additionalProperties": False,
                    },
                ]
            },
        },
    },
    "required": ["workflow_id", "workflow_version_id", "input_schema", "steps"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    workflow_version_id: str
    canonical_json: str

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> WorkflowDefinition:
        if not isinstance(data, Mapping):
            raise WorkflowDefinitionError("workflow definition must be an object")
        try:
            canonical = json.dumps(
                data, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            normalized = cast(dict[str, object], json.loads(canonical))
        except (TypeError, ValueError) as error:
            raise WorkflowDefinitionError(
                "workflow definition must contain JSON values"
            ) from error

        errors = list(Draft202012Validator(_WORKFLOW_SCHEMA).iter_errors(normalized))
        if errors:
            error = min(errors, key=lambda item: (len(item.absolute_path), str(item)))
            location = ".".join(str(part) for part in error.absolute_path)
            raise WorkflowDefinitionError(
                f"workflow definition step or field is invalid at {location or 'root'}"
            )

        input_schema = normalized["input_schema"]
        assert isinstance(input_schema, dict)
        try:
            Draft202012Validator.check_schema(input_schema)
        except SchemaError as error:
            raise WorkflowDefinitionError("workflow input schema is invalid") from error
        if _has_remote_reference(input_schema):
            raise WorkflowDefinitionError(
                "workflow input schema cannot retrieve remote references"
            )

        steps = cast(list[dict[str, object]], normalized["steps"])
        _validate_steps(steps)
        return cls(
            workflow_id=cast(str, normalized["workflow_id"]),
            workflow_version_id=cast(str, normalized["workflow_version_id"]),
            canonical_json=canonical,
        )

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.canonical_json))

    def validate_input(self, value: object) -> bool:
        normalized = self.to_dict()
        schema = cast(dict[str, object], normalized["input_schema"])
        return Draft202012Validator(schema).is_valid(value)


def _validate_steps(steps: list[dict[str, object]]) -> None:
    positions: dict[str, int] = {}
    for index, step in enumerate(steps):
        step_id = cast(str, step["id"])
        if step_id in positions:
            raise WorkflowDefinitionError(f"workflow step id {step_id} is duplicated")
        positions[step_id] = index

    for index, step in enumerate(steps):
        step_id = cast(str, step["id"])
        targets: list[str] = []
        if step["type"] == "condition":
            targets.extend((cast(str, step["then"]), cast(str, step["else"])))
            condition = cast(dict[str, object], step["condition"])
            _validate_reference_path(cast(str, condition["path"]), step_id, positions)
        elif "next" in step:
            targets.append(cast(str, step["next"]))

        on_failure = cast(dict[str, object], step.get("on_failure", {"action": "fail"}))
        if on_failure["action"] == "fallback":
            targets.append(cast(str, on_failure["step_id"]))

        for target in targets:
            if target == "END":
                continue
            if target not in positions:
                raise WorkflowDefinitionError(
                    f"workflow step {step_id} references unknown step {target}"
                )
            if positions[target] <= index:
                raise WorkflowDefinitionError(
                    f"workflow step {step_id} must branch only to a later step"
                )

        if step["type"] == "tool":
            for path in _reference_paths(step["arguments"]):
                _validate_reference_path(path, step_id, positions)


def _validate_reference_path(
    path: str, current_step_id: str, positions: Mapping[str, int]
) -> None:
    parts = path.split(".")
    if len(parts) < 2 or parts[0] not in {"input", "context", "steps"}:
        raise WorkflowDefinitionError(f"workflow reference path {path} is invalid")
    if parts[0] == "steps" and (len(parts) < 4 or parts[2] != "output" or not parts[3]):
        raise WorkflowDefinitionError(f"workflow reference path {path} is invalid")
    if parts[0] == "steps" and parts[1] == current_step_id:
        raise WorkflowDefinitionError("workflow step cannot reference its own output")
    if parts[0] == "steps" and (
        parts[1] not in positions or positions[parts[1]] >= positions[current_step_id]
    ):
        raise WorkflowDefinitionError(
            "workflow step can reference only earlier step outputs"
        )


def _reference_paths(value: object):
    if isinstance(value, Mapping):
        if set(value) == {"$ref"} and isinstance(value["$ref"], str):
            yield value["$ref"]
            return
        for child in value.values():
            yield from _reference_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from _reference_paths(child)


def _has_remote_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        for key in ("$ref", "$dynamicRef", "$recursiveRef"):
            reference = value.get(key)
            if isinstance(reference, str) and not reference.startswith("#"):
                return True
        return any(_has_remote_reference(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_remote_reference(child) for child in value)
    return False
