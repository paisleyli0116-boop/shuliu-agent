from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from itertools import product
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from shuliu_runtime.policy.risk import RiskLevel, parse_risk_level

from .executor import ToolExecutor
from .idempotency import (
    IDEMPOTENCY_RESULT_REFERENCE_FIELDS,
    SAFE_BROWSER_ERROR_CODE_SCHEMA_PATTERN,
    SAFE_ERROR_CODE_SCHEMA_PATTERN,
    SAFE_IDEMPOTENCY_ACTION_KINDS,
    SAFE_RESULT_REFERENCE_SCHEMA_PATTERN,
    SAFE_IDEMPOTENCY_STATUSES,
    SAFE_IDEMPOTENCY_OUTPUT_FIELDS,
    SAFE_TRACE_OR_SESSION_ID_SCHEMA_PATTERN,
    is_safe_idempotency_trace_id,
)


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
        if definition.idempotency_required and not _supports_idempotency_replay(
            output_schema
        ):
            raise ToolRegistryError(
                "TOOL_DEFINITION_INVALID",
                "Idempotent Tools must declare a safe result reference contract",
            )
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


def _supports_idempotency_replay(schema: Mapping[str, object]) -> bool:
    if schema.get("type") != "object":
        return False
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping) or not (
        IDEMPOTENCY_RESULT_REFERENCE_FIELDS & properties.keys()
    ):
        return False
    required = schema.get("required", [])
    if not isinstance(required, list):
        return False
    if any(not isinstance(field, str) for field in required):
        return False
    required_field_set = set(required)
    required_references = IDEMPOTENCY_RESULT_REFERENCE_FIELDS & required_field_set
    if not required_references or any(
        not _has_safe_result_reference_contract(properties.get(field))
        for field in required_references
    ):
        return False
    if any(
        not _has_safe_projected_field_contract(field, properties.get(field))
        for field in required_field_set - IDEMPOTENCY_RESULT_REFERENCE_FIELDS
    ):
        return False
    if any(
        not _has_safe_projected_field_contract(field, properties[field])
        for field in properties.keys() & SAFE_IDEMPOTENCY_OUTPUT_FIELDS
        if field not in IDEMPOTENCY_RESULT_REFERENCE_FIELDS
    ):
        return False
    if any(
        not _has_safe_result_reference_contract(properties[field])
        for field in properties.keys() & IDEMPOTENCY_RESULT_REFERENCE_FIELDS
    ):
        return False
    required_fields = _idempotency_replay_required_fields(schema, set(required))
    if (
        required_fields is None
        or any(field not in SAFE_IDEMPOTENCY_OUTPUT_FIELDS for field in required)
        or not required_fields.issubset(required_field_set)
    ):
        return False
    return _has_safe_replay_example(schema, required_field_set)


def _has_safe_result_reference_contract(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    minimum = value.get("minLength")
    maximum = value.get("maxLength")
    return (
        value.get("type") == "string"
        and value.get("pattern") == SAFE_RESULT_REFERENCE_SCHEMA_PATTERN
        and isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and minimum >= 1
        and isinstance(maximum, int)
        and not isinstance(maximum, bool)
        and maximum <= 256
        and _reference_applicator_is_compatible(value)
    )


def _has_safe_projected_field_contract(field: str, value: object) -> bool:
    if field == "trace_id":
        return _has_safe_identifier_contract(value, nullable=False)
    if field == "session_id":
        return _has_safe_identifier_contract(value, nullable=True)
    if field == "error_code":
        return _has_safe_error_code_contract(value)
    if field == "before_evidence_ref":
        return _has_safe_result_reference_contract(value)
    if field == "status":
        return _finite_projection_values(value, SAFE_IDEMPOTENCY_STATUSES, "string") is not None
    if field == "action_kind":
        return _finite_projection_values(value, SAFE_IDEMPOTENCY_ACTION_KINDS, "string") is not None
    if field in {"retryable", "outcome_unknown", "ok"}:
        return _finite_projection_values(value, frozenset({True, False}), "boolean") is not None
    return False


def _finite_projection_values(
    value: object, allowed: frozenset[object], expected_type: str
) -> frozenset[object] | None:
    """Return a proven safe finite value set, or None when the schema is broad."""
    if value is False:
        return frozenset()
    if value is True or not isinstance(value, Mapping):
        return None
    annotations = {
        "$comment",
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
    current = allowed
    bounded = False
    schema_type = value.get("type")
    if schema_type is not None:
        if schema_type != expected_type:
            return None
        # Boolean is itself a closed safe domain; strings need enum/const.
        bounded = expected_type == "boolean"
    enum = value.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or any(
            type(item) is not type(next(iter(allowed))) or item not in allowed
            for item in enum
        ):
            return None
        current = current & frozenset(enum)
        bounded = True
    if "const" in value:
        constant = value["const"]
        if type(constant) is not type(next(iter(allowed))) or constant not in allowed:
            return None
        current = current & frozenset({constant})
        bounded = True
    for branch in value.get("allOf", []):
        branch_values = _finite_projection_values(branch, allowed, expected_type)
        if branch_values is not None:
            current = current & branch_values
            bounded = True
    if "anyOf" in value:
        branches = value["anyOf"]
        if not isinstance(branches, list):
            return None
        branch_values = [
            _finite_projection_values(branch, allowed, expected_type)
            for branch in branches
        ]
        if any(values is None for values in branch_values):
            return None
        current = current & frozenset().union(*branch_values)
        bounded = True
    supported = annotations | {
        "type",
        "enum",
        "const",
        "allOf",
        "anyOf",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
    }
    if any(keyword not in supported for keyword in value):
        return None
    return current if bounded else None


def _has_safe_identifier_contract(value: object, *, nullable: bool) -> bool:
    if not isinstance(value, Mapping):
        return False
    expected_types = {"string", "null"} if nullable else {"string"}
    schema_type = value.get("type")
    actual_types = set(schema_type) if isinstance(schema_type, list) else {schema_type}
    minimum = value.get("minLength")
    maximum = value.get("maxLength")
    return (
        actual_types == expected_types
        and value.get("pattern") == SAFE_TRACE_OR_SESSION_ID_SCHEMA_PATTERN
        and isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and minimum >= 1
        and isinstance(maximum, int)
        and not isinstance(maximum, bool)
        and maximum <= 128
        and _identifier_applicator_is_compatible(value, nullable=nullable)
    )


def _identifier_applicator_is_compatible(value: object, *, nullable: bool) -> bool:
    if not isinstance(value, Mapping):
        return False
    annotations = {
        "$comment",
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
    allowed_types = {"string", "null"} if nullable else {"string"}
    for keyword, constraint in value.items():
        if keyword in annotations:
            continue
        if keyword == "type":
            actual = set(constraint) if isinstance(constraint, list) else {constraint}
            if not actual or not actual.issubset(allowed_types):
                return False
        elif keyword == "pattern":
            if constraint != SAFE_TRACE_OR_SESSION_ID_SCHEMA_PATTERN:
                return False
        elif keyword == "minLength":
            if isinstance(constraint, bool) or not isinstance(constraint, int) or constraint < 1:
                return False
        elif keyword == "maxLength":
            if isinstance(constraint, bool) or not isinstance(constraint, int) or not 1 <= constraint <= 128:
                return False
        elif keyword == "allOf":
            if not isinstance(constraint, list) or not all(
                _identifier_applicator_is_compatible(branch, nullable=nullable)
                for branch in constraint
            ):
                return False
        elif keyword == "const":
            candidate = constraint
            if candidate is None and nullable:
                continue
            if not is_safe_idempotency_trace_id(candidate):
                return False
        elif keyword == "enum":
            if not isinstance(constraint, list) or not all(
                candidate is None and nullable
                or is_safe_idempotency_trace_id(candidate)
                for candidate in constraint
            ):
                return False
        else:
            return False
    return True


def _has_safe_error_code_contract(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    minimum = value.get("minLength")
    maximum = value.get("maxLength")
    return (
        value.get("type") == "string"
        and value.get("pattern")
        in {SAFE_ERROR_CODE_SCHEMA_PATTERN, SAFE_BROWSER_ERROR_CODE_SCHEMA_PATTERN}
        and isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and minimum >= 1
        and isinstance(maximum, int)
        and not isinstance(maximum, bool)
        and maximum <= 128
        and _error_code_applicator_is_compatible(value)
        and _has_safe_error_code_example(value, minimum, maximum)
    )


def _has_safe_error_code_example(
    schema: Mapping[str, object], minimum: int, maximum: int
) -> bool:
    pattern = schema.get("pattern")
    base_length = 9 if pattern == SAFE_BROWSER_ERROR_CODE_SCHEMA_PATTERN else 1
    length = max(minimum, base_length)
    if length > maximum:
        return False
    candidate = (
        "BROWSER_" + "A" * (length - len("BROWSER_"))
        if pattern == SAFE_BROWSER_ERROR_CODE_SCHEMA_PATTERN
        else "A" * length
    )
    return Draft202012Validator(schema).is_valid(candidate)


def _error_code_applicator_is_compatible(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    annotations = {
        "$comment",
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
    for keyword, constraint in value.items():
        if keyword in annotations:
            continue
        if keyword == "type":
            if constraint != "string":
                return False
        elif keyword == "pattern":
            if constraint not in {
                SAFE_ERROR_CODE_SCHEMA_PATTERN,
                SAFE_BROWSER_ERROR_CODE_SCHEMA_PATTERN,
            }:
                return False
        elif keyword == "minLength":
            if isinstance(constraint, bool) or not isinstance(constraint, int) or constraint < 0:
                return False
        elif keyword == "maxLength":
            if isinstance(constraint, bool) or not isinstance(constraint, int) or not 0 <= constraint <= 128:
                return False
        elif keyword == "allOf":
            if not isinstance(constraint, list) or not all(
                _error_code_applicator_is_compatible(branch) for branch in constraint
            ):
                return False
        else:
            return False
    return True


def _has_safe_replay_example(
    schema: Mapping[str, object], required_fields: set[str]
) -> bool:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return False
    domains: list[list[object]] = []
    fields = sorted(required_fields)
    for field in fields:
        if field in IDEMPOTENCY_RESULT_REFERENCE_FIELDS or field == "before_evidence_ref":
            domains.append(["r"])
        elif field == "trace_id":
            domains.append(["t"])
        elif field == "session_id":
            domains.append([None, "s"])
        elif field == "status":
            domains.append(sorted(SAFE_IDEMPOTENCY_STATUSES))
        elif field == "action_kind":
            domains.append(sorted(SAFE_IDEMPOTENCY_ACTION_KINDS))
        elif field == "error_code":
            domains.append(["BROWSER_A"])
        elif field in {"retryable", "outcome_unknown", "ok"}:
            domains.append([False, True])
        else:
            return False
    validator = Draft202012Validator(schema)
    return any(
        validator.is_valid(dict(zip(fields, values, strict=True)))
        for values in product(*domains)
    )


def _reference_applicator_is_compatible(value: object) -> bool:
    """Reject applicator constraints that could invalidate persisted references.

    The root schema already requires a canonical 1–256 character reference.
    Applicators may repeat that contract, or leave it wider, but must not narrow
    it in a way that could make a post-dispatch result impossible to persist.
    """
    if not isinstance(value, Mapping):
        return False
    annotations = {
        "$comment",
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
    for keyword, constraint in value.items():
        if keyword in annotations:
            continue
        if keyword == "type":
            if constraint != "string":
                return False
        elif keyword == "pattern":
            if constraint != SAFE_RESULT_REFERENCE_SCHEMA_PATTERN:
                return False
        elif keyword == "minLength":
            if (
                isinstance(constraint, bool)
                or not isinstance(constraint, int)
                or constraint > 1
            ):
                return False
        elif keyword == "maxLength":
            if (
                isinstance(constraint, bool)
                or not isinstance(constraint, int)
                or constraint < 256
            ):
                return False
        elif keyword == "allOf":
            if not isinstance(constraint, list) or not all(
                _reference_applicator_is_compatible(branch)
                for branch in constraint
            ):
                return False
        else:
            # Assertions, references, or unrecognized schema keywords can
            # narrow the canonical reference set and are not proven safe here.
            return False
    return True


def _idempotency_replay_required_fields(
    schema: Mapping[str, object],
    root_required: set[str],
) -> set[str] | None:
    """Collect fields required by root-level schema applicators.

    Property schemas describe optional nested payloads and are intentionally not
    traversed: idempotency replay drops those payloads. Local references in a
    root/applicator position are rejected because their requirements cannot be
    proven to survive the safe projection here.
    """
    required_fields: set[str] = set()
    applicators = ("allOf", "anyOf")
    conditional = ("if", "then", "else")
    if any(
        keyword in schema
        for keyword in ("patternProperties", "propertyNames", "unevaluatedProperties")
    ):
        return None

    root_properties = schema.get("properties")

    def visit(
        node: object,
        *,
        inside_applicator: bool = False,
        conditional_applicator: bool = False,
        inside_if_condition: bool = False,
    ) -> bool:
        if not isinstance(node, Mapping):
            return True
        if "$ref" in node or "$dynamicRef" in node or "$recursiveRef" in node:
            return False
        if inside_if_condition and any(
            keyword in node for keyword in ("minProperties", "maxProperties")
        ):
            # Property counts can change when replay drops non-projected fields,
            # which could select a different conditional branch after restart.
            return False
        if inside_if_condition:
            dependent_schemas = node.get("dependentSchemas")
            if isinstance(dependent_schemas, Mapping) and any(
                field not in SAFE_IDEMPOTENCY_OUTPUT_FIELDS
                for field in dependent_schemas
            ):
                return False
            dependent_required = node.get("dependentRequired")
            if isinstance(dependent_required, Mapping) and any(
                field not in SAFE_IDEMPOTENCY_OUTPUT_FIELDS
                or not isinstance(required, list)
                or any(
                    dependency not in SAFE_IDEMPOTENCY_OUTPUT_FIELDS
                    for dependency in required
                )
                for field, required in dependent_required.items()
            ):
                return False
        # These assertions can change truth when replay removes output fields.
        if "oneOf" in node or "not" in node:
            return False
        if inside_applicator:
            properties = node.get("properties")
            if isinstance(properties, Mapping) and any(
                field not in SAFE_IDEMPOTENCY_OUTPUT_FIELDS for field in properties
            ):
                return False
            if isinstance(properties, Mapping) and any(
                field in properties
                and not _reference_applicator_is_compatible(properties[field])
                for field in IDEMPOTENCY_RESULT_REFERENCE_FIELDS
            ):
                return False
            if isinstance(properties, Mapping) and not conditional_applicator:
                if not isinstance(root_properties, Mapping):
                    return False
                if any(
                    field in SAFE_IDEMPOTENCY_OUTPUT_FIELDS
                    and field not in IDEMPOTENCY_RESULT_REFERENCE_FIELDS
                    and not _safe_projected_applicator_is_compatible(
                        field, root_properties.get(field), property_schema
                    )
                    for field, property_schema in properties.items()
                ):
                    return False
            if any(
                keyword in node
                for keyword in (
                    "patternProperties",
                    "additionalProperties",
                    "unevaluatedProperties",
                    "propertyNames",
                )
            ):
                return False
        minimum_properties = node.get("minProperties")
        if isinstance(minimum_properties, int) and minimum_properties > len(
            root_required
        ):
            return False
        maximum_properties = node.get("maxProperties")
        if (
            isinstance(maximum_properties, int)
            and not isinstance(maximum_properties, bool)
            and maximum_properties < len(root_required)
        ):
            return False
        required = node.get("required")
        if isinstance(required, list):
            if any(not isinstance(field, str) for field in required):
                return False
            required_fields.update(required)
        dependent_required = node.get("dependentRequired")
        if isinstance(dependent_required, Mapping):
            for dependencies in dependent_required.values():
                if not isinstance(dependencies, list) or any(
                    not isinstance(field, str) for field in dependencies
                ):
                    return False
                required_fields.update(dependencies)
        for keyword in applicators:
            branches = node.get(keyword)
            if branches is not None:
                if not isinstance(branches, list) or not all(
                    visit(
                        branch,
                        inside_applicator=True,
                        conditional_applicator=conditional_applicator,
                        inside_if_condition=inside_if_condition,
                    )
                    for branch in branches
                ):
                    return False
        for keyword in conditional:
            branch = node.get(keyword)
            if branch is not None and not visit(
                branch,
                inside_applicator=True,
                conditional_applicator=True,
                # Every nested if/then/else subschema still contributes to the
                # truth of an enclosing if predicate. Preserve that context so
                # projection-sensitive assertions cannot hide in nested then/
                # else branches.
                inside_if_condition=inside_if_condition or keyword == "if",
            ):
                return False
        dependent_schemas = node.get("dependentSchemas")
        if isinstance(dependent_schemas, Mapping) and not all(
            visit(
                branch,
                inside_applicator=True,
                conditional_applicator=conditional_applicator,
                inside_if_condition=inside_if_condition,
            )
            for branch in dependent_schemas.values()
        ):
            return False
        return True

    if not visit(schema):
        return None
    return required_fields


def _safe_projected_applicator_is_compatible(
    field: str, base_schema: object, applicator: object
) -> bool:
    if not isinstance(base_schema, Mapping) or not isinstance(applicator, Mapping):
        return False
    if field == "trace_id":
        if not _identifier_applicator_is_compatible(applicator, nullable=False):
            return False
    elif field == "session_id":
        if not _identifier_applicator_is_compatible(applicator, nullable=True):
            return False
    elif field == "error_code":
        if not _error_code_applicator_is_compatible(applicator):
            return False
    elif field == "before_evidence_ref":
        if not _reference_applicator_is_compatible(applicator):
            return False
    elif field == "status":
        if not _finite_projection_applicator_is_compatible(
            applicator, SAFE_IDEMPOTENCY_STATUSES, "string"
        ):
            return False
    elif field == "action_kind":
        if not _finite_projection_applicator_is_compatible(
            applicator, SAFE_IDEMPOTENCY_ACTION_KINDS, "string"
        ):
            return False
    elif field in {"retryable", "outcome_unknown", "ok"}:
        if not _finite_projection_applicator_is_compatible(
            applicator, frozenset({True, False}), "boolean"
        ):
            return False
    else:
        return False

    combined = dict(base_schema)
    base_all_of = combined.get("allOf", [])
    if not isinstance(base_all_of, list):
        return False
    combined["allOf"] = [*base_all_of, applicator]
    validator = Draft202012Validator(combined)
    candidates = _safe_projected_field_candidates(field, base_schema, applicator)
    return any(validator.is_valid(candidate) for candidate in candidates)


def _finite_projection_applicator_is_compatible(
    value: object, allowed: frozenset[object], expected_type: str
) -> bool:
    if not isinstance(value, Mapping):
        return False
    annotations = {
        "$comment",
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
    supported = annotations | {
        "type",
        "enum",
        "const",
        "allOf",
        "anyOf",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
    }
    if any(keyword not in supported for keyword in value):
        return False
    schema_type = value.get("type")
    actual_types = set(schema_type) if isinstance(schema_type, list) else {schema_type}
    if schema_type is not None and expected_type not in actual_types:
        return False
    for item in value.get("enum", []):
        if type(item) is not type(next(iter(allowed))) or item not in allowed:
            return False
    if "const" in value:
        item = value["const"]
        if type(item) is not type(next(iter(allowed))) or item not in allowed:
            return False
    for keyword in ("allOf", "anyOf"):
        branches = value.get(keyword, [])
        if not isinstance(branches, list) or not all(
            _finite_projection_applicator_is_compatible(
                branch, allowed, expected_type
            )
            for branch in branches
        ):
            return False
    return True


def _safe_projected_field_candidates(
    field: str, base_schema: Mapping[str, object], applicator: Mapping[str, object]
) -> list[object]:
    if field == "error_code":
        pattern = base_schema.get("pattern")
        prefix = "BROWSER_" if pattern == SAFE_BROWSER_ERROR_CODE_SCHEMA_PATTERN else ""
        start = max(1, len(prefix))
        candidates = [prefix + "A" * (length - len(prefix)) for length in range(start, 129)]
    elif field == "trace_id":
        candidates = ["A" * length for length in range(1, 129)]
    elif field == "session_id":
        candidates = [None, *("A" * length for length in range(1, 129))]
    elif field == "before_evidence_ref":
        candidates = ["A" * length for length in range(1, 257)]
    elif field == "status":
        candidates = list(SAFE_IDEMPOTENCY_STATUSES)
    elif field == "action_kind":
        candidates = list(SAFE_IDEMPOTENCY_ACTION_KINDS)
    elif field in {"retryable", "outcome_unknown", "ok"}:
        candidates = [False, True]
    else:
        return []

    pending: list[object] = [base_schema, applicator]
    while pending:
        node = pending.pop()
        if not isinstance(node, Mapping):
            continue
        if "const" in node:
            candidates.append(node["const"])
        enum = node.get("enum")
        if isinstance(enum, list):
            candidates.extend(enum)
        for keyword in ("allOf", "anyOf"):
            branches = node.get(keyword, [])
            if isinstance(branches, list):
                pending.extend(branches)
    return candidates
