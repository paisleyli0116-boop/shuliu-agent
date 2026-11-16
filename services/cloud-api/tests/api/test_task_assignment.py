from datetime import UTC, datetime

from fastapi.testclient import TestClient
from app.identity.rbac import RBACRepository
from app.persistence.postgres import tasks, workers
from sqlalchemy import insert, select


def _register_worker(engine, *, worker_id='worker-1', organization_id='org-1'):
    now = datetime(2026, 10, 18, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(workers).values(
                worker_id=worker_id,
                organization_id=organization_id,
                status='ONLINE',
                worker_version='0.1.0',
                protocol_version=1,
                capabilities=['worker.runtime'],
                last_heartbeat_at=now,
                created_at=now,
            )
        )


def test_task_payload_cannot_forge_cloud_worker_assignment(cloud_api):
    app, _ = cloud_api
    client = TestClient(app)
    response = client.post(
        '/v1/tasks',
        headers={'Authorization': 'Bearer user-1-token', 'X-Organization-ID': 'org-1'},
        json={'payload': {'_worker_assignment': {'worker_id': 'worker-1'}}},
    )

    assert response.status_code == 422


def test_task_assignment_requires_dedicated_permission_and_persists_cloud_owner(cloud_api):
    app, engine = cloud_api
    client = TestClient(app)
    _register_worker(engine)
    headers = {'Authorization': 'Bearer user-1-token', 'X-Organization-ID': 'org-1'}
    body = {
        'payload': {'required_capabilities': ['worker.runtime']},
        'assigned_worker_id': 'worker-1',
    }

    denied = client.post('/v1/tasks', headers=headers, json=body)
    assert denied.status_code == 403

    rbac = RBACRepository(engine)
    rbac.grant_permission(
        organization_id='org-1',
        role_id='role-org-1',
        permission_id='foundation.task.assign_worker',
    )
    created = client.post('/v1/tasks', headers=headers, json=body)

    assert created.status_code == 201
    task_id = created.json()['task_id']
    with engine.connect() as connection:
        payload = connection.execute(
            select(tasks.c.payload).where(tasks.c.task_id == task_id)
        ).scalar_one()
    assert payload['_worker_assignment'] == {'worker_id': 'worker-1'}


def test_task_cannot_be_assigned_to_worker_from_another_organization(cloud_api):
    app, engine = cloud_api
    client = TestClient(app)
    _register_worker(engine, worker_id='worker-2', organization_id='org-2')
    rbac = RBACRepository(engine)
    rbac.grant_permission(
        organization_id='org-1',
        role_id='role-org-1',
        permission_id='foundation.task.assign_worker',
    )

    response = client.post(
        '/v1/tasks',
        headers={'Authorization': 'Bearer user-1-token', 'X-Organization-ID': 'org-1'},
        json={
            'payload': {'required_capabilities': ['worker.runtime']},
            'assigned_worker_id': 'worker-2',
        },
    )

    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'WORKER_ASSIGNMENT_INVALID'
