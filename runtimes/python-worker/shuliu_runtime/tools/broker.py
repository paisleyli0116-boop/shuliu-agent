from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from math import isfinite
from typing import Protocol
from uuid import uuid4

from shuliu_runtime.approval.service import ApprovalError
from shuliu_runtime.events.models import EventRecord
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome

from .idempotency import IdempotencyClaim, IdempotencyClaimStatus, IdempotencyStore
from .registry import ToolDefinition, ToolRegistry, ToolRegistryError


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ToolExecutionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class IdentityPrincipal:
    subject_id: str
    organization_id: str
    permissions: tuple[str, ...]


@dataclass(frozen=True)
class ToolResult:
    tool_id: str
    tool_version: str
    execution_status: ToolExecutionStatus
    verification_status: VerificationStatus
    output: object
    error_code: str | None
    policy_decision: PolicyOutcome
    policy_version: str
    policy_rule_id: str | None


class IdentityResolver(Protocol):
    def resolve(self, actor: Mapping[str, object]) -> IdentityPrincipal | None: ...


class PolicyEvaluator(Protocol):
    def evaluate(
        self,
        *,
        subject: Mapping[str, object],
        action: Mapping[str, object],
        resource: Mapping[str, object] | None,
        context: Mapping[str, object],
    ) -> PolicyDecision: ...


class ApprovalConsumer(Protocol):
    def consume(
        self, approval_id: str, action_context: Mapping[str, object]
    ) -> object: ...


EventSink = Callable[[EventRecord], None]
Clock = Callable[[], datetime]


class ToolBroker:
    """Runs registered Tools through identity, Policy, Approval and verification."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        identity: IdentityResolver | None,
        policy: PolicyEvaluator | None,
        approvals: ApprovalConsumer | None,
        event_sink: EventSink,
        idempotency_store: IdempotencyStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not callable(event_sink):
            raise TypeError("event_sink must be callable")
        self._registry = registry
        self._identity = identity
        self._policy = policy
        self._approvals = approvals
        self._event_sink = event_sink
        self._idempotency_store = idempotency_store
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        tool_id: str,
        args: Mapping[str, object],
        actor: Mapping[str, object],
        context: Mapping[str, object],
        *,
        idempotency_key: str | None,
    ) -> ToolResult:
        try:
            definition = self._registry.get(tool_id)
        except ToolRegistryError as error:
            raise ToolExecutionError(error.code, str(error)) from error

        self._validate_request(definition, args, actor, context)
        principal = self._resolve_identity(actor)
        if principal.organization_id != context["organization_id"]:
            raise ToolExecutionError(
                "IDENTITY_SCOPE_MISMATCH", "Actor and request organization do not match"
            )
        resource = context.get("resource")
        if isinstance(resource, Mapping):
            resource_organization = resource.get("organization_id")
            if (
                resource_organization is not None
                and resource_organization != principal.organization_id
            ):
                raise ToolExecutionError(
                    "IDENTITY_SCOPE_MISMATCH", "Resource is outside actor organization"
                )
        if not set(definition.permissions).issubset(principal.permissions):
            raise ToolExecutionError(
                "IDENTITY_PERMISSION_DENIED", "Actor lacks a Tool permission"
            )

        decision = self._evaluate_policy(definition, principal, resource, context)
        if decision.decision is PolicyOutcome.DENY:
            self._emit_decision_event(
                definition,
                principal,
                context,
                decision,
                "TOOL_EXECUTION_DENIED",
                "POLICY_DENIED",
            )
            raise ToolExecutionError("POLICY_DENIED", "Policy denied this Tool call")
        if (
            definition.risk_level is RiskLevel.CRITICAL
            and decision.decision is not PolicyOutcome.REQUIRE_APPROVAL
        ):
            self._emit_decision_event(
                definition,
                principal,
                context,
                decision,
                "TOOL_EXECUTION_DENIED",
                "POLICY_APPROVAL_REQUIRED",
            )
            raise ToolExecutionError(
                "POLICY_APPROVAL_REQUIRED", "Critical Tools require Approval"
            )

        approval_id = _optional_text(context.get("approval_id"))
        approval_context: dict[str, object] | None = None
        if decision.decision is PolicyOutcome.REQUIRE_APPROVAL:
            if approval_id is None:
                self._emit_decision_event(
                    definition,
                    principal,
                    context,
                    decision,
                    "TOOL_APPROVAL_REQUIRED",
                    "APPROVAL_REQUIRED",
                )
                raise ToolExecutionError(
                    "APPROVAL_REQUIRED", "An approved action context is required"
                )
            if self._approvals is None:
                self._emit_decision_event(
                    definition,
                    principal,
                    context,
                    decision,
                    "TOOL_APPROVAL_REQUIRED",
                    "APPROVAL_UNAVAILABLE",
                )
                raise ToolExecutionError(
                    "APPROVAL_UNAVAILABLE", "Approval service is unavailable"
                )
            approval_context = _approval_context(definition, args, principal, context)

        request_digest = _request_digest(definition, args, principal, context)
        claim = self._begin_idempotency(
            definition, principal, idempotency_key, request_digest
        )
        if claim is not None:
            if claim.status is IdempotencyClaimStatus.COMPLETED:
                if not isinstance(claim.result, ToolResult):
                    raise ToolExecutionError(
                        "IDEMPOTENCY_RESULT_INVALID", "Stored Tool result is invalid"
                    )
                return claim.result
            if claim.status is IdempotencyClaimStatus.CONFLICT:
                raise ToolExecutionError(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "Idempotency key was already used for a different request",
                )
            if claim.status is IdempotencyClaimStatus.IN_PROGRESS:
                raise ToolExecutionError(
                    "IDEMPOTENCY_IN_PROGRESS",
                    "An identical Tool call is still in progress",
                )

        if decision.decision is PolicyOutcome.REQUIRE_APPROVAL:
            assert approval_id is not None
            assert self._approvals is not None
            assert approval_context is not None
            try:
                self._approvals.consume(approval_id, approval_context)
            except ApprovalError as error:
                self._release_idempotency(
                    definition, principal, idempotency_key, request_digest
                )
                raise ToolExecutionError(error.code, str(error)) from error
            except Exception as error:
                self._release_idempotency(
                    definition, principal, idempotency_key, request_digest
                )
                raise ToolExecutionError(
                    "APPROVAL_UNAVAILABLE", "Approval service failed closed"
                ) from error

        executor = self._registry._executor(tool_id)
        safe_args = dict(args)
        safe_context = dict(context)
        try:
            output = executor.execute(tool_id, safe_args, safe_context, idempotency_key)
        except Exception as error:
            self._emit_execution_error_event(
                definition,
                principal,
                context,
                decision,
                "TOOL_EXECUTION_UNKNOWN",
            )
            raise ToolExecutionError(
                "TOOL_EXECUTION_UNKNOWN",
                "Executor failed; the side-effect state requires reconciliation",
            ) from error

        output_valid = _is_json_value(output) and self._registry.validate_output(
            tool_id, output
        )
        try:
            verified = (
                output_valid
                and executor.verify(tool_id, safe_args, output, safe_context) is True
            )
        except Exception:  # noqa: BLE001 - verifier failures must fail closed
            verified = False
        result = ToolResult(
            tool_id=definition.name,
            tool_version=definition.version,
            execution_status=ToolExecutionStatus.SUCCEEDED,
            verification_status=(
                VerificationStatus.VERIFIED if verified else VerificationStatus.FAILED
            ),
            output=output,
            error_code=(
                None
                if verified
                else "TOOL_OUTPUT_INVALID"
                if not output_valid
                else "TOOL_RESULT_UNVERIFIED"
            ),
            policy_decision=decision.decision,
            policy_version=decision.policy_version,
            policy_rule_id=decision.rule_id,
        )
        event_type = (
            "TOOL_EXECUTION_COMPLETED" if verified else "TOOL_EXECUTION_UNVERIFIED"
        )
        try:
            self._event_sink(
                _tool_event(
                    definition,
                    principal,
                    context,
                    decision,
                    result,
                    event_type,
                    self._clock(),
                )
            )
        except Exception as error:
            raise ToolExecutionError(
                "TOOL_AUDIT_FAILED",
                "Execution result could not be recorded; do not retry automatically",
            ) from error
        self._complete_idempotency(
            definition, principal, idempotency_key, request_digest, result
        )
        return result

    def _validate_request(
        self,
        definition: ToolDefinition,
        args: Mapping[str, object],
        actor: Mapping[str, object],
        context: Mapping[str, object],
    ) -> None:
        if not isinstance(args, Mapping) or not _is_json_value(args):
            raise ToolExecutionError(
                "TOOL_INPUT_INVALID", "Tool input must be a JSON object"
            )
        if not self._registry.validate_input(definition.name, args):
            raise ToolExecutionError(
                "TOOL_INPUT_INVALID", "Tool input does not match the registered schema"
            )
        if not isinstance(actor, Mapping):
            raise ToolExecutionError("IDENTITY_REQUIRED", "Actor identity is required")
        if not isinstance(context, Mapping):
            raise ToolExecutionError(
                "TOOL_CONTEXT_INVALID", "Tool context must be an object"
            )
        if (
            not _non_empty(context.get("organization_id"))
            or not _non_empty(context.get("trace_id"))
            or not _non_empty(context.get("privacy"))
        ):
            raise ToolExecutionError(
                "TOOL_CONTEXT_INVALID", "Organization, trace and privacy are required"
            )
        resource = context.get("resource")
        if resource is not None and not isinstance(resource, Mapping):
            raise ToolExecutionError(
                "TOOL_CONTEXT_INVALID", "Resource must be an object"
            )
        if resource is not None and not _is_json_value(resource):
            raise ToolExecutionError(
                "TOOL_CONTEXT_INVALID", "Resource must contain JSON values"
            )

    def _resolve_identity(self, actor: Mapping[str, object]) -> IdentityPrincipal:
        if self._identity is None:
            raise ToolExecutionError(
                "IDENTITY_UNAVAILABLE", "Identity provider is unavailable"
            )
        try:
            principal = self._identity.resolve(actor)
        except Exception as error:
            raise ToolExecutionError(
                "IDENTITY_UNAVAILABLE", "Identity provider failed closed"
            ) from error
        if (
            not isinstance(principal, IdentityPrincipal)
            or not _non_empty(principal.subject_id)
            or not _non_empty(principal.organization_id)
            or not isinstance(principal.permissions, tuple)
            or any(not _non_empty(permission) for permission in principal.permissions)
        ):
            raise ToolExecutionError(
                "IDENTITY_REQUIRED", "A verified identity is required"
            )
        return principal

    def _evaluate_policy(
        self,
        definition: ToolDefinition,
        principal: IdentityPrincipal,
        resource: object,
        context: Mapping[str, object],
    ) -> PolicyDecision:
        if self._policy is None:
            raise ToolExecutionError(
                "POLICY_UNAVAILABLE", "Policy engine is unavailable"
            )
        try:
            decision = self._policy.evaluate(
                subject={
                    "subject_id": principal.subject_id,
                    "organization_id": principal.organization_id,
                    "permissions": list(principal.permissions),
                },
                action={
                    "action_id": definition.name,
                    "risk": definition.risk_level.value,
                },
                resource=resource,
                context={
                    "organization_id": principal.organization_id,
                    "trace_id": context["trace_id"],
                    "privacy": context["privacy"],
                },
            )
        except Exception as error:
            raise ToolExecutionError(
                "POLICY_UNAVAILABLE", "Policy evaluation failed closed"
            ) from error
        if (
            not isinstance(decision, PolicyDecision)
            or not isinstance(decision.decision, PolicyOutcome)
            or decision.risk is not definition.risk_level
            or decision.trace_id != context.get("trace_id")
            or not _non_empty(decision.policy_version)
        ):
            raise ToolExecutionError(
                "POLICY_DECISION_INVALID", "Policy returned an invalid decision"
            )
        return decision

    def _begin_idempotency(
        self,
        definition: ToolDefinition,
        principal: IdentityPrincipal,
        key: str | None,
        request_digest: str,
    ) -> IdempotencyClaim | None:
        if not definition.idempotency_required:
            return None
        if not _non_empty(key):
            raise ToolExecutionError(
                "IDEMPOTENCY_KEY_REQUIRED", "This Tool requires an idempotency key"
            )
        if self._idempotency_store is None:
            raise ToolExecutionError(
                "IDEMPOTENCY_UNAVAILABLE",
                "A durable idempotency store is required for this Tool",
            )
        assert key is not None
        try:
            claim = self._idempotency_store.begin(
                scope=(
                    principal.organization_id,
                    principal.subject_id,
                    definition.name,
                ),
                key=key,
                request_digest=request_digest,
            )
        except Exception as error:
            raise ToolExecutionError(
                "IDEMPOTENCY_UNAVAILABLE", "Idempotency store failed closed"
            ) from error
        if not isinstance(claim, IdempotencyClaim):
            raise ToolExecutionError(
                "IDEMPOTENCY_UNAVAILABLE", "Idempotency store returned an invalid claim"
            )
        return claim

    def _release_idempotency(
        self,
        definition: ToolDefinition,
        principal: IdentityPrincipal,
        key: str | None,
        request_digest: str,
    ) -> None:
        if not definition.idempotency_required or key is None:
            return
        if self._idempotency_store is None:
            raise ToolExecutionError(
                "IDEMPOTENCY_UNAVAILABLE", "Idempotency store is unavailable"
            )
        try:
            self._idempotency_store.release(
                scope=(
                    principal.organization_id,
                    principal.subject_id,
                    definition.name,
                ),
                key=key,
                request_digest=request_digest,
            )
        except Exception as error:
            raise ToolExecutionError(
                "IDEMPOTENCY_UNAVAILABLE", "Idempotency claim could not be released"
            ) from error

    def _complete_idempotency(
        self,
        definition: ToolDefinition,
        principal: IdentityPrincipal,
        key: str | None,
        request_digest: str,
        result: ToolResult,
    ) -> None:
        if not definition.idempotency_required or key is None:
            return
        if self._idempotency_store is None:
            raise ToolExecutionError(
                "IDEMPOTENCY_UNAVAILABLE", "Idempotency store is unavailable"
            )
        try:
            self._idempotency_store.complete(
                scope=(
                    principal.organization_id,
                    principal.subject_id,
                    definition.name,
                ),
                key=key,
                request_digest=request_digest,
                result=result,
            )
        except Exception as error:
            raise ToolExecutionError(
                "IDEMPOTENCY_UNAVAILABLE",
                "Execution completed but idempotency result was not recorded",
            ) from error

    def _emit_decision_event(
        self,
        definition: ToolDefinition,
        principal: IdentityPrincipal,
        context: Mapping[str, object],
        decision: PolicyDecision,
        event_type: str,
        error_code: str,
    ) -> None:
        timestamp = self._event_timestamp()
        try:
            self._event_sink(
                EventRecord(
                    event_id=str(uuid4()),
                    schema_version=1,
                    timestamp=timestamp.isoformat(),
                    organization_id=principal.organization_id,
                    trace_id=str(context["trace_id"]),
                    event_type=event_type,
                    payload={
                        "tool_id": definition.name,
                        "tool_version": definition.version,
                        "risk": definition.risk_level.value,
                        "policy_decision": decision.decision.value,
                        "policy_version": decision.policy_version,
                        "policy_rule_id": decision.rule_id,
                        "error_code": error_code,
                    },
                )
            )
        except Exception as error:
            raise ToolExecutionError(
                "TOOL_AUDIT_FAILED", "Policy result could not be recorded"
            ) from error

    def _emit_execution_error_event(
        self,
        definition: ToolDefinition,
        principal: IdentityPrincipal,
        context: Mapping[str, object],
        decision: PolicyDecision,
        error_code: str,
    ) -> None:
        try:
            self._event_sink(
                EventRecord(
                    event_id=str(uuid4()),
                    schema_version=1,
                    timestamp=self._event_timestamp().isoformat(),
                    organization_id=principal.organization_id,
                    trace_id=str(context["trace_id"]),
                    event_type="TOOL_EXECUTION_UNKNOWN",
                    payload={
                        "tool_id": definition.name,
                        "tool_version": definition.version,
                        "risk": definition.risk_level.value,
                        "policy_decision": decision.decision.value,
                        "policy_version": decision.policy_version,
                        "policy_rule_id": decision.rule_id,
                        "error_code": error_code,
                    },
                )
            )
        except Exception as error:
            raise ToolExecutionError(
                "TOOL_AUDIT_FAILED", "Uncertain execution could not be recorded"
            ) from error

    def _event_timestamp(self) -> datetime:
        timestamp = self._clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ToolExecutionError(
                "TOOL_CLOCK_INVALID", "Event clock must be timezone-aware"
            )
        return timestamp.astimezone(UTC)


def _approval_context(
    definition: ToolDefinition,
    args: Mapping[str, object],
    principal: IdentityPrincipal,
    context: Mapping[str, object],
) -> dict[str, object]:
    target_approver_id = _optional_text(context.get("target_approver_id"))
    action_summary = _optional_text(context.get("action_summary"))
    if target_approver_id is None or action_summary is None:
        raise ToolExecutionError(
            "APPROVAL_CONTEXT_INVALID",
            "Target approver and action summary are required",
        )
    resource = context.get("resource", {})
    assert isinstance(resource, Mapping)
    return {
        "organization_id": principal.organization_id,
        "requester_id": principal.subject_id,
        "target_approver_id": target_approver_id,
        "action_id": definition.name,
        "action_summary": action_summary,
        "resource": dict(resource),
        "risk": definition.risk_level.value,
        "action_params": dict(args),
        "run_id": _optional_text(context.get("run_id")),
        "trace_id": str(context["trace_id"]),
    }


def _request_digest(
    definition: ToolDefinition,
    args: Mapping[str, object],
    principal: IdentityPrincipal,
    context: Mapping[str, object],
) -> str:
    value = {
        "tool_id": definition.name,
        "tool_version": definition.version,
        "organization_id": principal.organization_id,
        "subject_id": principal.subject_id,
        "args": args,
        "resource": context.get("resource"),
        "privacy": context.get("privacy"),
    }
    try:
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ToolExecutionError(
            "TOOL_CONTEXT_INVALID", "Tool request cannot be canonicalized"
        ) from error
    return sha256(canonical.encode("utf-8")).hexdigest()


def _tool_event(
    definition: ToolDefinition,
    principal: IdentityPrincipal,
    context: Mapping[str, object],
    decision: PolicyDecision,
    result: ToolResult,
    event_type: str,
    timestamp: datetime,
) -> EventRecord:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ToolExecutionError(
            "TOOL_CLOCK_INVALID", "Event clock must be timezone-aware"
        )
    return EventRecord(
        event_id=str(uuid4()),
        schema_version=1,
        timestamp=timestamp.astimezone(UTC).isoformat(),
        organization_id=principal.organization_id,
        trace_id=str(context["trace_id"]),
        event_type=event_type,
        payload={
            "tool_id": definition.name,
            "tool_version": definition.version,
            "risk": definition.risk_level.value,
            "execution_status": result.execution_status.value,
            "verification_status": result.verification_status.value,
            "policy_decision": decision.decision.value,
            "policy_version": decision.policy_version,
            "policy_rule_id": decision.rule_id,
            "error_code": result.error_code,
        },
    )


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not _non_empty(value):
        raise ToolExecutionError(
            "TOOL_CONTEXT_INVALID", "Optional text values cannot be blank"
        )
    return value
