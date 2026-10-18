from fastapi.testclient import TestClient
from sqlalchemy import select
from app.persistence.postgres import tasks


def test_task_create_and_read_require_identity_and_org_permissions(cloud_api):
    app, engine = cloud_api
    client = TestClient(app)

    unauthenticated = client.post("/v1/tasks", json={"payload": {"kind": "foundation.task"}})
    assert unauthenticated.status_code == 401

    created = client.post(
        "/v1/tasks",
        headers={"Authorization": "Bearer user-1-token", "X-Organization-ID": "org-1"},
        json={"payload": {"required_capabilities": ["worker.runtime"]}},
    )
    assert created.status_code == 201
    task_id = created.json()["task_id"]
    assert created.json()["status"] == "READY"

    read = client.get(
        f"/v1/tasks/{task_id}",
        headers={"Authorization": "Bearer user-1-token", "X-Organization-ID": "org-1"},
    )
    assert read.status_code == 200
    assert read.json()["task_id"] == task_id

    cross_organization = client.get(
        f"/v1/tasks/{task_id}",
        headers={"Authorization": "Bearer user-2-token", "X-Organization-ID": "org-1"},
    )
    assert cross_organization.status_code == 403
    with engine.connect() as connection:
        assert connection.execute(select(tasks.c.task_id)).all() == [(task_id,)]


def test_task_payload_must_be_generic_object_and_missing_task_is_not_found(cloud_api):
    app, _ = cloud_api
    client = TestClient(app)
    headers = {"Authorization": "Bearer user-1-token", "X-Organization-ID": "org-1"}

    invalid = client.post("/v1/tasks", headers=headers, json={"payload": "invalid"})
    missing = client.get("/v1/tasks/task_missing", headers=headers)

    assert invalid.status_code == 422
    assert missing.status_code == 404
