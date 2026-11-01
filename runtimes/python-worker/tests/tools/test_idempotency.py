from __future__ import annotations

import pytest
from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.tools.broker import ToolExecutionError
from shuliu_runtime.tools.idempotency import (
    IdempotencyClaimStatus,
    InMemoryIdempotencyStore,
    encode_idempotency_result,
)

from tests.tools.test_broker_policy_order import _actor, _broker, _context


def test_reusing_an_idempotency_key_returns_result_without_reexecuting():
    broker, executor, _, _, _ = _broker(decision=PolicyOutcome.ALLOW)
    args = {"document_id": "doc-1"}

    first = broker.execute(
        "foundation.document.write",
        args,
        _actor(),
        _context(),
        idempotency_key="idem-once",
    )
    second = broker.execute(
        "foundation.document.write",
        args,
        _actor(),
        _context(),
        idempotency_key="idem-once",
    )

    assert first == second
    assert len(executor.calls) == 1


def test_reusing_an_idempotency_key_for_different_input_is_rejected():
    broker, executor, _, _, _ = _broker(decision=PolicyOutcome.ALLOW)

    broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(),
        idempotency_key="idem-conflict",
    )
    with pytest.raises(ToolExecutionError) as conflict:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-2"},
            _actor(),
            _context(),
            idempotency_key="idem-conflict",
        )

    assert conflict.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert len(executor.calls) == 1


def test_store_keeps_same_key_claimed_until_terminal_result():
    store = InMemoryIdempotencyStore()
    claim = store.begin(
        scope=("org-1", "agent-1", "foundation.document.write"),
        key="idem-busy",
        request_digest="same-request",
    )
    assert claim.status == "NEW"
    duplicate = store.begin(
        scope=("org-1", "agent-1", "foundation.document.write"),
        key="idem-busy",
        request_digest="same-request",
    )
    assert duplicate.status == "IN_PROGRESS"


@pytest.mark.parametrize(
    ("scope", "key", "request_digest"),
    [
        (("o" * 129, "s", "tool", "op"), "key", "digest"),
        (("org", "s", "tool", "op"), "k" * 257, "digest"),
        (("org", "s", "tool", "op"), "key", "d" * 129),
    ],
)
def test_in_memory_store_rejects_values_that_cannot_be_persisted(
    scope, key, request_digest
):
    with pytest.raises(ValueError):
        InMemoryIdempotencyStore().begin(
            scope=scope, key=key, request_digest=request_digest
        )


class FailingExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, tool_id, arguments, context, idempotency_key):
        self.calls += 1
        raise RuntimeError("fixture failure after possible external effect")

    def verify(self, tool_id, arguments, output, context):
        return False


def test_executor_failure_requires_reconciliation_and_never_dispatches_twice():
    store = InMemoryIdempotencyStore()
    executor = FailingExecutor()
    broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.ALLOW,
        executor=executor,
        idempotency_store=store,
    )

    with pytest.raises(ToolExecutionError) as first:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="idem-unknown",
        )

    with pytest.raises(ToolExecutionError) as duplicate:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="idem-unknown",
        )

    assert first.value.code == "TOOL_EXECUTION_UNKNOWN"
    assert duplicate.value.code == "RECONCILIATION_REQUIRED"
    assert executor.calls == 1


def test_dispatched_claim_cannot_be_released_for_reexecution():
    store = InMemoryIdempotencyStore()
    scope = ("org-1", "agent-1", "foundation.document.write")
    store.begin(scope=scope, key="idem-dispatched", request_digest="digest")
    store.mark_dispatched(
        scope=scope, key="idem-dispatched", request_digest="digest"
    )

    store.release_before_dispatch(
        scope=scope, key="idem-dispatched", request_digest="digest"
    )

    repeated = store.begin(
        scope=scope, key="idem-dispatched", request_digest="digest"
    )
    assert repeated.status is IdempotencyClaimStatus.RECONCILIATION_REQUIRED


def test_durable_result_encoding_keeps_only_a_safe_result_reference():
    encoded = encode_idempotency_result(
        {
            "tool_id": "browser.click",
            "tool_version": "1.0.0",
            "execution_status": "SUCCEEDED",
            "verification_status": "VERIFIED",
            "output": {
                "evidence_ref": "evidence-1",
                "observation": "private page content",
            },
            "error_code": None,
            "policy_decision": "ALLOW",
            "policy_version": "policy-v1",
            "policy_rule_id": "rule-1",
        }
    )

    assert "private page content" not in encoded
    assert '"output":{"evidence_ref":"evidence-1"}' in encoded


@pytest.mark.parametrize("reference", ["C:\\private\\result.json", "https://example.invalid/result"])
def test_durable_result_encoding_rejects_path_or_url_references(reference):
    with pytest.raises(ValueError, match="reference"):
        encode_idempotency_result({"result_ref": reference, "content": "private"})
