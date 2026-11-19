"""Foundation lifecycle fixture sidecar driven by the TypeScript E2E suite.

The sidecar composes the real, durable Foundation runtime chain -- Workflow
Engine -> Tool Broker -> Identity / Policy / Approval -- on top of a local
SQLite database with no external service. Fake components are limited to the
identity source and the Tool executor; policy evaluation, approvals, the
workflow engine and all persistence are the production implementations.

Protocol: one JSON request per stdin line, one JSON response per stdout line.

    request  = {"id": string, "action": string, "params": object}
    response = {"id": string, "ok": true, "result": object}
             | {"id": string, "ok": false, "error": {"code": string, "message": string}}

Supported actions:
    start                  run the Foundation demo lifecycle (mode: approval|direct)
    decide                 record an approval decision (approver_id, decision)
    resume                 resume a waiting run with its one-time token
    tamper                 apply a bad update to a run's stored context
    revoke_acl             drop every actor permission (fail-closed ACL check)
    set_executor_failures  make the next N executor dispatches crash
    get_run                return the persisted run summary
    events                 return the durable event + audit trail
    stats                  return executor dispatch / side-effect counters
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from shuliu_runtime.approval.service import ApprovalError, ApprovalService
from shuliu_runtime.persistence.outbox import OutboxRepository, canonical_json
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule
from shuliu_runtime.production.approvals import SQLiteApprovalRepository
from shuliu_runtime.tools.broker import (
    IdentityPrincipal,
    ToolBroker,
    ToolExecutionError,
)
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry
from shuliu_runtime.workflow.definition import WorkflowDefinition
from shuliu_runtime.workflow.engine import WorkflowEngine
from shuliu_runtime.workflow.sqlite_repository import SQLiteWorkflowRepository

from tests.fakes.fake_tool import FakeToolExecutor

ORGANIZATION_ID = "org-1"
ACTOR_ID = "agent-1"
APPROVER_ID = "approver-1"
PRIVACY = "INTERNAL"
HIGH_RISK_TOOL = "foundation.document.write.high"
LOW_RISK_TOOL = "foundation.document.write.low"

# Deny rule only matches when the request classification is RESTRICTED.
_POLICY_RULES = (
    PolicyRule(
        rule_id="allow-direct-write",
        action=LOW_RISK_TOOL,
        risk="LOW",
        decision="ALLOW",
    ),
    PolicyRule(
        rule_id="approval-gated-write",
        action=HIGH_RISK_TOOL,
        risk="HIGH",
        decision=None,
    ),
    PolicyRule(
        rule_id="deny-restricted-doc",
        action=LOW_RISK_TOOL,
        risk="LOW",
        decision="DENY",
        classification="RESTRICTED",
    ),
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"document_id": {"type": "string"}},
    "required": ["document_id"],
    "additionalProperties": False,
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean", "enum": [True]},
        "evidence_ref": {
            "type": "string",
            "minLength": 1,
            "maxLength": 256,
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])",
        },
    },
    "required": ["ok", "evidence_ref"],
    "additionalProperties": False,
}


def _definition(workflow_id: str, tool_id: str) -> WorkflowDefinition:
    return WorkflowDefinition.from_dict(
        {
            "workflow_id": workflow_id,
            "workflow_version_id": "v1",
            "input_schema": _INPUT_SCHEMA,
            "steps": [
                {
                    "id": "write-doc",
                    "type": "tool",
                    "tool_id": tool_id,
                    "arguments": {"document_id": {"$ref": "input.document_id"}},
                }
            ],
        }
    )


class StaticIdentity:
    """Local identity source whose permissions the fixture can revoke."""

    def __init__(self) -> None:
        self.revoked = False

    def resolve(self, actor: Mapping[str, object]) -> IdentityPrincipal | None:
        if actor.get("subject_id") != ACTOR_ID:
            return None
        permissions: tuple[str, ...] = ()
        if not self.revoked:
            permissions = ("foundation.document.write",)
        return IdentityPrincipal(
            subject_id=ACTOR_ID,
            organization_id=ORGANIZATION_ID,
            permissions=permissions,
        )


class LifecycleRuntime:
    def __init__(self, state_dir: Path) -> None:
        self._database = SQLiteDatabase(state_dir / "foundation.sqlite")
        self._database.migrate()
        self._workflow_repository = SQLiteWorkflowRepository(self._database)
        self._approvals = ApprovalService(
            repository=SQLiteApprovalRepository(self._database),
            authorize_approver=_authorize_approver,
            approval_ttl=timedelta(minutes=10),
        )
        self._identity = StaticIdentity()
        self._policy = PolicyEngine(_POLICY_RULES, policy_version="e2e-policy-v1")
        self._executor = FakeToolExecutor()
        registry = ToolRegistry()
        for tool_id, risk in (
            (HIGH_RISK_TOOL, "HIGH"),
            (LOW_RISK_TOOL, "LOW"),
        ):
            registry.register(
                ToolDefinition(
                    name=tool_id,
                    version="1.0.0",
                    input_schema=_INPUT_SCHEMA,
                    output_schema=_OUTPUT_SCHEMA,
                    permissions=("foundation.document.write",),
                    risk_level=risk,
                    idempotency_required=True,
                ),
                self._executor,
            )
        outbox = OutboxRepository(self._database)

        def emit_tool_event(event) -> None:
            from shuliu_runtime.events.models import event_to_dict

            outbox.enqueue(
                event.event_id,
                f"workflow-tool-event:{event.event_id}",
                event_to_dict(event),
            )

        self._broker = ToolBroker(
            registry=registry,
            identity=self._identity,
            policy=self._policy,
            approvals=self._approvals,
            event_sink=emit_tool_event,
            idempotency_store=SQLiteIdempotencyStore(self._database),
        )
        self._engine = WorkflowEngine(
            repository=self._workflow_repository,
            tool_broker=self._broker,
        )

    # -- actions -----------------------------------------------------------

    def start(self, params: Mapping[str, Any]) -> dict[str, object]:
        mode = params.get("mode", "approval")
        if mode not in ("approval", "direct"):
            raise _FixtureError("REQUEST_INVALID", "mode must be approval or direct")
        document_id = params.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise _FixtureError("REQUEST_INVALID", "document_id is required")
        classification = params.get("classification", "INTERNAL")
        if mode == "approval":
            tool_id = HIGH_RISK_TOOL
            workflow = _definition("foundation.doc-write-approval", HIGH_RISK_TOOL)
        else:
            tool_id = LOW_RISK_TOOL
            workflow = _definition("foundation.doc-write-direct", LOW_RISK_TOOL)

        trace_id = f"trace-{uuid4().hex[:12]}"
        resource = {
            "organization_id": ORGANIZATION_ID,
            "resource_type": "document",
            "resource_id": document_id,
            "classification": classification,
        }
        input_payload = {"document_id": document_id}
        context = _run_context(trace_id, resource)
        args = input_payload

        approval_id: str | None = None
        denied_code: str | None = None
        try:
            approval_context, decision = self._broker.prepare_approval_context(
                tool_id, args, context["actor"], context
            )
        except ToolExecutionError as error:
            if error.code == "APPROVAL_NOT_REQUIRED":
                approval_context = None
            else:
                # Policy or identity denied the request. The engine still runs
                # so the durable record shows the fail-closed outcome.
                approval_context = None
                denied_code = error.code
        else:
            approval = self._approvals.request_approval(approval_context, decision)
            approval_id = approval.approval_id

        if approval_id is not None:
            context = {**context, "approval_id": approval_id}
        run = self._engine.start_workflow(workflow, input_payload, context)
        result: dict[str, object] = _run_summary(run)
        if approval_id is not None:
            result["approval_id"] = approval_id
        if denied_code is not None:
            result["error_code"] = denied_code
        return result

    def decide(self, params: Mapping[str, Any]) -> dict[str, object]:
        approval_id = params.get("approval_id")
        approver_id = params.get("approver_id")
        decision = params.get("decision")
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise _FixtureError("REQUEST_INVALID", "approval_id is required")
        if not isinstance(approver_id, str) or not approver_id.strip():
            raise _FixtureError("REQUEST_INVALID", "approver_id is required")
        if decision not in ("APPROVE", "REJECT"):
            raise _FixtureError("REQUEST_INVALID", "decision must be APPROVE or REJECT")
        approval = self._approvals.decide(
            approval_id, approver_id=approver_id, decision=decision
        )
        return {
            "approval_id": approval.approval_id,
            "status": approval.status.value,
            "decision_by": approval.decision_by,
        }

    def resume(self, params: Mapping[str, Any]) -> dict[str, object]:
        run_id = params.get("run_id")
        resume_token = params.get("resume_token")
        if not isinstance(run_id, str) or not run_id.strip():
            raise _FixtureError("REQUEST_INVALID", "run_id is required")
        if not isinstance(resume_token, str) or not resume_token.strip():
            raise _FixtureError(
                "WORKFLOW_RESUME_REJECTED", "resume_token is required"
            )
        run = self._engine.resume_workflow(run_id, resume_token)
        return _run_summary(run)

    def tamper(self, params: Mapping[str, Any]) -> dict[str, object]:
        run_id = params.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise _FixtureError("REQUEST_INVALID", "run_id is required")
        run = self._workflow_repository.get(run_id)
        if run is None:
            raise _FixtureError("WORKFLOW_RUN_NOT_FOUND", f"run {run_id} not found")
        context = json.loads(run.run_context_json)
        resource = context.get("resource")
        if not isinstance(resource, dict):
            raise _FixtureError("REQUEST_INVALID", "run context has no resource")
        resource["resource_id"] = "doc-tampered"
        now = datetime.now(UTC)
        from shuliu_runtime.events.models import event_from_dict

        event = event_from_dict(
            {
                "event_id": str(uuid4()),
                "schema_version": 1,
                "timestamp": now.isoformat(),
                "organization_id": run.organization_id,
                "trace_id": run.trace_id,
                "event_type": "WORKFLOW_RUN_CONTEXT_UPDATED",
                "payload": {"run_id": run.run_id, "updated_by": "bad-update"},
            }
        )
        tampered = replace(
            run,
            run_context_json=canonical_json(context),
            revision=run.revision + 1,
            updated_at=now,
        )
        applied = self._workflow_repository.compare_and_set(
            run_id=run.run_id,
            expected_revision=run.revision,
            updated_run=tampered,
            event=event,
        )
        if not applied:
            raise _FixtureError("WORKFLOW_STATE_CONFLICT", "tamper write lost the race")
        return _run_summary(self._workflow_repository.get(run_id))

    def revoke_acl(self, params: Mapping[str, Any]) -> dict[str, object]:
        self._identity.revoked = bool(params.get("revoked", True))
        return {"revoked": self._identity.revoked}

    def set_executor_failures(self, params: Mapping[str, Any]) -> dict[str, object]:
        times = params.get("times", 1)
        if not isinstance(times, int) or isinstance(times, bool) or times < 0:
            raise _FixtureError("REQUEST_INVALID", "times must be a non-negative int")
        self._executor.fail_next(times)
        return {"failures_remaining": self._executor.failures_remaining}

    def get_run(self, params: Mapping[str, Any]) -> dict[str, object]:
        run_id = params.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise _FixtureError("REQUEST_INVALID", "run_id is required")
        run = self._workflow_repository.get(run_id)
        if run is None:
            raise _FixtureError("WORKFLOW_RUN_NOT_FOUND", f"run {run_id} not found")
        return _run_summary(run)

    def events(self, params: Mapping[str, Any]) -> dict[str, object]:
        del params
        records: list[dict[str, object]] = []
        with self._database.connect() as connection:
            outbox_rows = connection.execute(
                "SELECT event_id, payload_json FROM outbox ORDER BY rowid"
            ).fetchall()
            audit_rows = connection.execute(
                "SELECT event_id, event_type, payload_json "
                "FROM approval_audit_events ORDER BY rowid"
            ).fetchall()
        for row in outbox_rows:
            payload = json.loads(row["payload_json"])
            records.append(
                {
                    "source": "outbox",
                    "event_id": row["event_id"],
                    "event_type": payload.get("event_type"),
                    "payload": payload.get("payload"),
                }
            )
        for row in audit_rows:
            records.append(
                {
                    "source": "approval_audit",
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload_json"]),
                }
            )
        return {"events": records}

    def stats(self, params: Mapping[str, Any]) -> dict[str, object]:
        del params
        return {
            "executor_calls": len(self._executor.calls),
            "side_effects": len(self._executor.side_effects),
        }


class _FixtureError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _authorize_approver(
    *, organization_id: str, approver_id: str, permission: str, resource: Mapping[str, object]
) -> bool:
    del permission, resource
    return organization_id == ORGANIZATION_ID and approver_id == APPROVER_ID


def _run_context(trace_id: str, resource: dict[str, object]) -> dict[str, object]:
    return {
        "organization_id": ORGANIZATION_ID,
        "trace_id": trace_id,
        "actor": {"subject_id": ACTOR_ID, "organization_id": ORGANIZATION_ID},
        "privacy": PRIVACY,
        "resource": resource,
        "target_approver_id": APPROVER_ID,
        "action_summary": "Write foundation document",
    }


def _run_summary(run) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "workflow_id": run.workflow_id,
        "status": run.status.value,
        "current_step_id": run.current_step_id,
        "wait_reason": run.wait_reason.value if run.wait_reason else None,
        "resume_token": run.resume_token,
        "revision": run.revision,
        "steps": [
            {
                "step_id": step.step_id,
                "status": step.status.value,
                "error_code": step.error_code,
                "wait_reason": step.wait_reason.value if step.wait_reason else None,
                "attempts": step.attempts,
            }
            for step in run.step_runs
        ],
    }


_ERROR_CODES = {"APPROVAL_NOT_CONSUMABLE"}


def _error_payload(error: Exception) -> dict[str, object]:
    if isinstance(error, _FixtureError):
        return {"code": error.code, "message": str(error)}
    if isinstance(error, ApprovalError):
        return {"code": error.code, "message": str(error)}
    if isinstance(error, ToolExecutionError):
        return {"code": error.code, "message": str(error)}
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return {"code": code, "message": str(error)}
    return {"code": "SIDECAR_INTERNAL", "message": str(error)}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: lifecycle_sidecar.py <state_dir>", file=sys.stderr)
        return 2
    state_dir = Path(sys.argv[1])
    state_dir.mkdir(parents=True, exist_ok=True)
    runtime = LifecycleRuntime(state_dir)

    handlers = {
        "start": runtime.start,
        "decide": runtime.decide,
        "resume": runtime.resume,
        "tamper": runtime.tamper,
        "revoke_acl": runtime.revoke_acl,
        "set_executor_failures": runtime.set_executor_failures,
        "get_run": runtime.get_run,
        "events": runtime.events,
        "stats": runtime.stats,
    }

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            request_id = request.get("id")
            action = request.get("action")
            handler = handlers.get(action)
            if handler is None:
                raise _FixtureError(
                    "REQUEST_INVALID", f"unknown action {action!r}"
                )
            result = handler(request.get("params") or {})
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as error:  # noqa: BLE001 - one bad request must not kill the sidecar
            response = {
                "id": request.get("id") if isinstance(request, dict) else None,
                "ok": False,
                "error": _error_payload(error),
            }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
