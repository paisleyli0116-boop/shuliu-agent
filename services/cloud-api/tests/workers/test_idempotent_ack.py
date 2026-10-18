from __future__ import annotations

import pytest
from app.workers.leases import WorkerCoordinatorError
from shuliu_runtime.worker.protocol import validate_worker_message


def test_result_ack_is_deduplicated_and_persisted(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")
    result = {"status": "SUCCEEDED", "value": "done"}

    first_ack = coordinator.commit_result(
        worker,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        idempotency_key="task-1-effect",
        result=result,
    )
    retry_ack = coordinator.commit_result(
        worker,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        idempotency_key="task-1-effect",
        result=result,
    )

    assert first_ack.duplicate is False
    assert retry_ack.duplicate is True
    assert retry_ack.event_id == first_ack.event_id
    ack_messages = [
        message
        for _, message in worker_harness["transport"].messages
        if message["message_type"] == "ack"
    ]
    assert len(ack_messages) == 2
    validate_worker_message(ack_messages[-1])


def test_same_idempotency_key_with_different_result_is_rejected(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")
    coordinator.commit_result(
        worker,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        idempotency_key="task-1-effect",
        result={"status": "SUCCEEDED", "value": "done"},
    )

    with pytest.raises(WorkerCoordinatorError) as error:
        coordinator.commit_result(
            worker,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            idempotency_key="task-1-effect",
            result={"status": "FAILED", "value": "different"},
        )

    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_lost_ack_can_be_retried_without_reapplying_result(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    transport = worker_harness["transport"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")
    result = {"status": "SUCCEEDED", "value": "done"}
    transport.fail_next_send = True

    with pytest.raises(WorkerCoordinatorError) as error:
        coordinator.commit_result(
            worker,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            idempotency_key="task-1-effect",
            result=result,
        )
    assert error.value.code == "WORKER_ACK_OUTCOME_UNKNOWN"

    retry_ack = coordinator.commit_result(
        worker,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        idempotency_key="task-1-effect",
        result=result,
    )

    assert retry_ack.duplicate is True
