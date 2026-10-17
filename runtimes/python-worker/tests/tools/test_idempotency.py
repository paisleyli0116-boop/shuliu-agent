from __future__ import annotations

import pytest
from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.tools.broker import ToolExecutionError
from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore

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
