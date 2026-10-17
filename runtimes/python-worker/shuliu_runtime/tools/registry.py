from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from shuliu_runtime.policy.risk import RiskLevel, parse_risk_level

from .executor import ToolExecutor


class ToolRegistryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    permissions: tuple[str, ...]
    risk_level: RiskLevel | str
    idempotency_required: bool


@dataclass(frozen=True)
class _Registration:
    definition: ToolDefinition
    executor: ToolExecutor
    input_validator: Draft202012Validator
    output_validator: Draft202012Validator


class ToolRegistry:
    """Stores trusted Tool contracts and their non-public Executor bindings."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(self, definition: ToolDefinition, executor: ToolExecutor) -> None:
        if not isinstance(definition, ToolDefinition):
            raise ToolRegistryError(
                "TOOL_DEFINITION_INVALID", "Tool definition has an unsupported shape"
            )
        if (
            not _non_empty(definition.name)
            or not _non_empty(definition.version)
            or not isinstance(definition.idempotency_required, bool)
        ):
            raise ToolRegistryError(
                "TOOL_DEFINITION_INVALID", "Tool name, version, or flags are invalid"
            )

        risk = parse_risk_level(definition.risk_level)
        if risk is None:
            raise ToolRegistryError(
                "TOOL_DEFINITION_INVALID", "Tool risk level is unknown"
            )
        permissions = definition.permissions
        if (
            not isinstance(permissions, (tuple, list))
            or not permissions
            or any(not _non_empty(permission) for permission in permissions)
            or len(set(permissions)) != len(permissions)
        ):
            raise ToolRegistryError(
                "TOOL_DEFINITION_INVALID",
                "Tool permissions must be unique non-empty values",
            )
        if (
            risk in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)
            and not definition.idempotency_required
        ):
            raise ToolRegistryError(
                "TOOL_DEFINITION_INVALID",
                "Write-capable tools require idempotency",
            )
        if not callable(getattr(executor, "execute", None)) or not callable(
            getattr(executor, "verify", None)
        ):
            raise ToolRegistryError(
                "TOOL_DEFINITION_INVALID", "Executor must execute and verify results"
            )

        input_schema = _validated_schema(definition.input_schema)
        output_schema = _validated_schema(definition.output_schema)
        normalized = ToolDefinition(
            name=definition.name,
            version=definition.version,
            input_schema=input_schema,
            output_schema=output_schema,
            permissions=tuple(permissions),
            risk_level=risk,
            idempotency_required=definition.idempotency_required,
        )
        if normalized.name in self._registrations:
            raise ToolRegistryError(
                "TOOL_ALREADY_REGISTERED", "Tool name is already registered"
            )
        self._registrations[normalized.name] = _Registration(
            definition=normalized,
            executor=executor,
            input_validator=Draft202012Validator(
                input_schema, format_checker=FormatChecker()
            ),
            output_validator=Draft202012Validator(
                output_schema, format_checker=FormatChecker()
            ),
        )

    def get(self, name: str) -> ToolDefinition:
        registration = self._registrations.get(name)
        if registration is None:
            raise ToolRegistryError("TOOL_NOT_FOUND", "Tool is not registered")
        return deepcopy(registration.definition)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def validate_input(self, name: str, value: object) -> bool:
        return not next(
            self._registration(name).input_validator.iter_errors(value), None
        )

    def validate_output(self, name: str, value: object) -> bool:
        return not next(
            self._registration(name).output_validator.iter_errors(value), None
        )

    def _registration(self, name: str) -> _Registration:
        registration = self._registrations.get(name)
        if registration is None:
            raise ToolRegistryError("TOOL_NOT_FOUND", "Tool is not registered")
        return registration

    def _executor(self, name: str) -> ToolExecutor:
        return self._registration(name).executor


def _validated_schema(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ToolRegistryError("TOOL_SCHEMA_INVALID", "Tool schema must be an object")
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        schema = cast(dict[str, object], json.loads(encoded))
    except (TypeError, ValueError) as error:
        raise ToolRegistryError(
            "TOOL_SCHEMA_INVALID", "Tool schema must contain JSON values"
        ) from error
    schema_version = schema.get("$schema")
    if schema_version not in (None, Draft202012Validator.META_SCHEMA["$id"]):
        raise ToolRegistryError(
            "TOOL_SCHEMA_INVALID", "Only JSON Schema Draft 2020-12 is supported"
        )
    if _has_external_reference(schema):
        raise ToolRegistryError(
            "TOOL_SCHEMA_INVALID", "Tool schemas cannot retrieve remote references"
        )
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ToolRegistryError(
            "TOOL_SCHEMA_INVALID", "Tool schema is invalid"
        ) from error
    return schema


def _has_external_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        for field in ("$ref", "$dynamicRef", "$recursiveRef"):
            reference = value.get(field)
            if isinstance(reference, str) and not reference.startswith("#"):
                return True
        return any(_has_external_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_external_reference(item) for item in value)
    return False


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
