from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.persistence.postgres import create_postgres_engine
from shuliu_runtime.tools.idempotency import (
    IdempotencyClaimStatus,
    IdempotencyRecordStatus,
)
from shuliu_runtime.tools.postgres_idempotency import PostgresIdempotencyStore


REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.postgres


def _store():
    database_url = _test_database_url()
    engine = create_postgres_engine(database_url)
    config = Config(str(REPO_ROOT / "services/cloud-api/alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return engine, PostgresIdempotencyStore(engine)


def _test_database_url() -> str:
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        if os.environ.get("CI", "").strip().lower() in {"true", "1", "yes"}:
            raise RuntimeError("CI requires a PostgreSQL test database")
        pytest.skip("SHULIU_DATABASE_URL is not configured")
    try:
        parsed = make_url(database_url)
    except Exception:
        raise ValueError("SHULIU_DATABASE_URL is invalid") from None
    if (
        parsed.get_backend_name() != "postgresql"
        or not (parsed.database or "").endswith("_test")
    ):
        raise ValueError(
            "SHULIU_DATABASE_URL must target an isolated PostgreSQL test database"
        )
    return database_url


def test_postgres_store_refuses_non_test_database_before_connect(monkeypatch):
    monkeypatch.setenv(
        "SHULIU_DATABASE_URL",
        "postgresql+psycopg://localhost/shuliu_production",
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "create_postgres_engine",
        lambda _url: pytest.fail("unsafe database URL reached the engine factory"),
    )

    with pytest.raises(ValueError, match="must target an isolated PostgreSQL test database"):
        _store()


def test_postgres_store_requires_database_when_running_in_ci(monkeypatch):
    monkeypatch.delenv("SHULIU_DATABASE_URL", raising=False)
    monkeypatch.setenv("CI", "true")

    with pytest.raises(BaseException) as error:
        _test_database_url()
    assert isinstance(error.value, RuntimeError)
    assert "CI requires a PostgreSQL test database" in str(error.value)


def _delete_claim(engine, scope, key):
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM tool_idempotency WHERE organization_id = :org "
                "AND tool_id = :tool "
                "AND operation_id = :operation AND idempotency_key = :key"
            ),
            {
                "org": scope[0],
                "tool": scope[1],
                "operation": scope[2],
                "key": key,
            },
        )


@pytest.mark.integration
def test_postgres_result_and_request_digest_survive_store_restart():
    engine, store = _store()
    scope = ("org-" + str(uuid4()), "foundation.document.write", "run-step-1")
    key = "idem-" + str(uuid4())
    digest = "a" * 64
    result = {
        "tool_id": "foundation.document.write",
        "tool_version": "1.0.0",
        "execution_status": "SUCCEEDED",
        "verification_status": "VERIFIED",
        "output": {
            "ok": True,
            "api_key": "must-not-persist",
            "evidence_ref": "evidence-pg-1",
            "page_text": "sensitive page text must not persist",
        },
        "error_code": None,
        "policy_decision": "ALLOW",
        "policy_version": "policy-v1",
        "policy_rule_id": "rule-1",
    }
    try:
        assert store.begin(scope=scope, key=key, request_digest=digest).status is IdempotencyClaimStatus.NEW
        store.mark_dispatched(scope=scope, key=key, request_digest=digest)
        store.complete(scope=scope, key=key, request_digest=digest, result=result)

        reopened = PostgresIdempotencyStore(engine)
        replay = reopened.begin(scope=scope, key=key, request_digest=digest)
        conflict = reopened.begin(scope=scope, key=key, request_digest="b" * 64)
        with engine.connect() as connection:
            stored_result = connection.execute(
                text(
                    "SELECT result_json FROM tool_idempotency "
                    "WHERE organization_id = :org AND tool_id = :tool "
                    "AND operation_id = :operation "
                    "AND idempotency_key = :key"
                ),
                {
                    "org": scope[0],
                    "tool": scope[1],
                    "operation": scope[2],
                    "key": key,
                },
            ).scalar_one()

        assert replay.status is IdempotencyClaimStatus.COMPLETED
        assert replay.result == {
            **result,
            "output": {"ok": True, "evidence_ref": "evidence-pg-1"},
        }
        assert "must-not-persist" not in str(stored_result)
        assert "sensitive page text" not in str(stored_result)
        assert conflict.status is IdempotencyClaimStatus.CONFLICT
    finally:
        _delete_claim(engine, scope, key)
        engine.dispose()


@pytest.mark.integration
def test_postgres_concurrent_begin_creates_one_dispatchable_claim():
    engine, store = _store()
    scope = ("org-" + str(uuid4()), "foundation.document.write", "run-step-2")
    key = "idem-" + str(uuid4())
    digest = "c" * 64
    barrier = Barrier(2)

    def begin_once():
        local_store = PostgresIdempotencyStore(engine)
        barrier.wait(timeout=5)
        return local_store.begin(scope=scope, key=key, request_digest=digest).status

    try:
        with ThreadPoolExecutor(max_workers=2) as executor_pool:
            statuses = list(executor_pool.map(lambda _: begin_once(), range(2)))
        assert statuses.count(IdempotencyClaimStatus.NEW) == 1
        assert statuses.count(IdempotencyClaimStatus.IN_PROGRESS) == 1
    finally:
        _delete_claim(engine, scope, key)
        engine.dispose()


@pytest.mark.integration
def test_postgres_only_releases_claim_before_dispatch():
    engine, store = _store()
    scope = ("org-" + str(uuid4()), "foundation.document.write", "run-step-3")
    key = "idem-" + str(uuid4())
    digest = "e" * 64

    try:
        assert store.begin(scope=scope, key=key, request_digest=digest).status is IdempotencyClaimStatus.NEW
        store.release_before_dispatch(scope=scope, key=key, request_digest=digest)
        assert store.begin(scope=scope, key=key, request_digest=digest).status is IdempotencyClaimStatus.NEW

        store.mark_dispatched(scope=scope, key=key, request_digest=digest)
        store.release_before_dispatch(scope=scope, key=key, request_digest=digest)
        assert store.begin(scope=scope, key=key, request_digest=digest).status is IdempotencyClaimStatus.RECONCILIATION_REQUIRED
    finally:
        _delete_claim(engine, scope, key)
        engine.dispose()


@pytest.mark.integration
def test_postgres_recovery_inspection_is_read_only_and_returns_safe_result():
    engine, store = _store()
    scope = ("org-" + str(uuid4()), "foundation.document.write", "run-step-recovery")
    key = "idem-" + str(uuid4())
    indeterminate_key = key + "-unknown"
    digest = "f" * 64

    try:
        assert store.inspect(scope=scope, key=key, request_digest=digest).status is IdempotencyRecordStatus.ABSENT
        assert store.begin(scope=scope, key=key, request_digest=digest).status is IdempotencyClaimStatus.NEW
        assert store.inspect(scope=scope, key=key, request_digest=digest).status is IdempotencyRecordStatus.CLAIMED

        store.mark_dispatched(scope=scope, key=key, request_digest=digest)
        assert store.inspect(scope=scope, key=key, request_digest=digest).status is IdempotencyRecordStatus.DISPATCHED
        assert store.inspect(scope=scope, key=key, request_digest="0" * 64).status is IdempotencyRecordStatus.CONFLICT

        store.begin(scope=scope, key=indeterminate_key, request_digest=digest)
        store.mark_dispatched(scope=scope, key=indeterminate_key, request_digest=digest)
        store.mark_indeterminate(scope=scope, key=indeterminate_key, request_digest=digest)
        assert store.inspect(scope=scope, key=indeterminate_key, request_digest=digest).status is IdempotencyRecordStatus.INDETERMINATE

        store.complete(
            scope=scope,
            key=key,
            request_digest=digest,
            result={
                "tool_id": scope[1],
                "tool_version": "1.0.0",
                "execution_status": "SUCCEEDED",
                "verification_status": "VERIFIED",
                "output": {
                    "evidence_ref": "evidence-recovery-1",
                    "document_text": "must-not-be-persisted",
                },
                "error_code": None,
                "policy_decision": "ALLOW",
                "policy_version": "policy-v1",
                "policy_rule_id": "rule-1",
            },
        )
        snapshot = store.inspect(scope=scope, key=key, request_digest=digest)
        assert snapshot.status is IdempotencyRecordStatus.COMPLETED
        assert snapshot.result["verification_status"] == "VERIFIED"
        assert snapshot.result["output"] == {"evidence_ref": "evidence-recovery-1"}
        assert store.inspect(scope=scope, key=key, request_digest="0" * 64).result is None
    finally:
        _delete_claim(engine, scope, key)
        _delete_claim(engine, scope, indeterminate_key)
        engine.dispose()
