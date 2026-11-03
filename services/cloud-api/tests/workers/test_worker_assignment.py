from __future__ import annotations

import pytest
from app.persistence.postgres import tasks
from app.workers.leases import WorkerCoordinatorError
from sqlalchemy import insert, update


def test_worker_cannot_lease_ready_task_without_explicit_cloud_assignment(worker_harness):
    coordinator = worker_harness['coordinator']
    worker = worker_harness['principals']['worker-old']
    with worker_harness['engine'].begin() as connection:
        connection.execute(
            update(tasks)
            .where(tasks.c.task_id == 'task-1')
            .values(payload={
                'required_capabilities': ['worker.runtime'],
                'tool_call': {
                    'tool_id': 'foundation.test',
                    'arguments': {'value': 'safe'},
                },
            })
        )

    with pytest.raises(WorkerCoordinatorError) as error:
        coordinator.lease('task-1', worker, idempotency_key='task-1-effect')

    assert error.value.code == 'WORKER_TASK_NOT_ASSIGNED'
    assert worker_harness['transport'].messages == []


def test_worker_cannot_lease_task_assigned_to_a_different_worker(worker_harness):
    coordinator = worker_harness['coordinator']
    worker = worker_harness['principals']['worker-old']
    with worker_harness['engine'].begin() as connection:
        connection.execute(
            update(tasks)
            .where(tasks.c.task_id == 'task-1')
            .values(payload={
                'required_capabilities': ['worker.runtime'],
                '_worker_assignment': {'worker_id': 'worker-new'},
                'tool_call': {
                    'tool_id': 'foundation.test',
                    'arguments': {'value': 'safe'},
                },
            })
        )

    with pytest.raises(WorkerCoordinatorError) as error:
        coordinator.lease('task-1', worker, idempotency_key='task-1-effect')

    assert error.value.code == 'WORKER_TASK_NOT_ASSIGNED'
    assert worker_harness['transport'].messages == []


def test_cloud_assigned_worker_can_lease_its_assigned_task(worker_harness):
    coordinator = worker_harness['coordinator']
    worker = worker_harness['principals']['worker-old']
    with worker_harness['engine'].begin() as connection:
        connection.execute(
            update(tasks)
            .where(tasks.c.task_id == 'task-1')
            .values(payload={
                'required_capabilities': ['worker.runtime'],
                '_worker_assignment': {'worker_id': worker.worker_id},
                'tool_call': {
                    'tool_id': 'foundation.test',
                    'arguments': {'value': 'safe'},
                },
            })
        )

    lease = coordinator.lease('task-1', worker, idempotency_key='task-1-effect')

    assert lease.task_id == 'task-1'
    assert lease.worker_id == worker.worker_id


def test_cloud_assignment_poll_leases_only_the_authenticated_workers_task(worker_harness):
    coordinator = worker_harness['coordinator']
    worker = worker_harness['principals']['worker-old']
    other_worker = worker_harness['principals']['worker-new']
    with worker_harness['engine'].begin() as connection:
        connection.execute(
            update(tasks)
            .where(tasks.c.task_id == 'task-1')
            .values(payload={
                'required_capabilities': ['worker.runtime'],
                '_worker_assignment': {'worker_id': other_worker.worker_id},
                'tool_call': {
                    'tool_id': 'foundation.test',
                    'arguments': {'value': 'other'},
                },
            })
        )
        connection.execute(
            insert(tasks).values(
                task_id='task-2',
                organization_id='org-1',
                status='READY',
                payload={
                    'required_capabilities': ['worker.runtime'],
                    '_worker_assignment': {'worker_id': worker.worker_id},
                    'tool_call': {
                        'tool_id': 'foundation.test',
                        'arguments': {'value': 'safe'},
                    },
                },
                created_at=worker_harness['clock'](),
            )
        )

    lease = coordinator.lease_next_assigned(
        worker, idempotency_key='poll-1'
    )

    assert lease is not None
    assert lease.task_id == 'task-2'
    assert lease.worker_id == worker.worker_id
    assert worker_harness['transport'].messages[-1][1]['message_type'] == 'lease'
