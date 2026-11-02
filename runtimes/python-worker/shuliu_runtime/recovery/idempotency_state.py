from __future__ import annotations

from collections.abc import Mapping

from shuliu_runtime.tools.idempotency import (
    IdempotencyInspector,
    IdempotencyRecord,
    IdempotencyRecordStatus,
    sanitize_idempotency_tool_output,
    validate_idempotency_values,
)

from .reconciler import (
    ActionReconciliation,
    ActionReconciliationStatus,
)


class IdempotencyExternalStateReader:
    """Reconcile an action only from its durable, sanitized Broker record.

    This reader is deliberately read-only. It never releases a claim or dispatches
    a Tool; process fencing and any safe claim transition remain the caller's job.
    """

    def __init__(self, inspector: IdempotencyInspector) -> None:
        if not callable(getattr(inspector, "inspect", None)):
            raise TypeError("idempotency inspector must provide inspect")
        self._inspector = inspector

    def inspect(
        self, run_id: str, pending_action: Mapping[str, object]
    ) -> ActionReconciliation:
        if (
            not isinstance(run_id, str)
            or not run_id.strip()
            or not isinstance(pending_action, Mapping)
        ):
            return _unknown()

        organization_id = pending_action.get("organization_id")
        tool_id = pending_action.get("tool_id")
        operation_id = pending_action.get("operation_id")
        workflow_run_id = pending_action.get("workflow_run_id")
        key = pending_action.get("idempotency_key")
        request_digest = pending_action.get("request_digest")
        if (
            not isinstance(organization_id, str)
            or not organization_id.strip()
            or not isinstance(tool_id, str)
            or not tool_id.strip()
            or not isinstance(operation_id, str)
            or not operation_id.strip()
            or workflow_run_id != run_id
            or not isinstance(key, str)
            or not isinstance(request_digest, str)
        ):
            return _unknown()

        scope = (organization_id, tool_id, operation_id)
        try:
            validate_idempotency_values(
                scope, key, request_digest, expected_scope_size=3
            )
            record = self._inspector.inspect(
                scope=scope, key=key, request_digest=request_digest
            )
        except Exception:
            # Recovery diagnostics retain only the safe WAIT decision, never a
            # backend exception that may contain paths, SQL, or request data.
            return _unknown()

        if not isinstance(record, IdempotencyRecord):
            return _unknown()

        if record.status is IdempotencyRecordStatus.ABSENT:
            return ActionReconciliation(ActionReconciliationStatus.NOT_APPLIED)
        if record.status is not IdempotencyRecordStatus.COMPLETED:
            return _unknown()

        result = record.result
        if not isinstance(result, Mapping):
            return _unknown()
        if (
            result.get("execution_status") != "SUCCEEDED"
            or result.get("verification_status") != "VERIFIED"
        ):
            return _unknown()
        output = result.get("output")
        if not isinstance(output, Mapping):
            return _unknown()
        safe_output = sanitize_idempotency_tool_output(output)
        if safe_output is None:
            return _unknown()

        return ActionReconciliation(
            ActionReconciliationStatus.APPLIED,
            verified_output=safe_output,
        )


def _unknown() -> ActionReconciliation:
    return ActionReconciliation(ActionReconciliationStatus.UNKNOWN)
