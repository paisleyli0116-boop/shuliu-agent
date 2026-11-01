from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.persistence.postgres import create_postgres_engine
from shuliu_runtime.tools.idempotency import IdempotencyClaimStatus
from shuliu_runtime.tools.postgres_idempotency import PostgresIdempotencyStore


REPO_ROOT = Path(__file__).resolve().parents[2]


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


@pytest.mark.integration
def test_process_crash_after_external_effect_never_replays_dispatched_tool():
    database_url = _test_database_url()
    engine = create_postgres_engine(database_url)
    config = Config(str(REPO_ROOT / "services/cloud-api/alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS task33_effect_probe "
                "(probe_id TEXT PRIMARY KEY, calls INTEGER NOT NULL)"
            )
        )

    scope = ("org-" + str(uuid4()), "foundation.document.write", "run-crash-1")
    key = "idem-" + str(uuid4())
    digest = "d" * 64
    probe_id = str(uuid4())
    child_code = r'''
import os
from sqlalchemy import text
from app.persistence.postgres import create_postgres_engine
from shuliu_runtime.tools.postgres_idempotency import PostgresIdempotencyStore

engine = create_postgres_engine(os.environ["SHULIU_DATABASE_URL"])
store = PostgresIdempotencyStore(engine)
scope = tuple(os.environ["TASK33_SCOPE"].split("|"))
key = os.environ["TASK33_KEY"]
digest = os.environ["TASK33_DIGEST"]
claim = store.begin(scope=scope, key=key, request_digest=digest)
if claim.status.value != "NEW":
    os._exit(41)
store.mark_dispatched(scope=scope, key=key, request_digest=digest)
with engine.begin() as connection:
    connection.execute(
        text("INSERT INTO task33_effect_probe (probe_id, calls) VALUES (:id, 1)"),
        {"id": os.environ["TASK33_PROBE_ID"]},
    )
os._exit(23)
'''
    environment = os.environ.copy()
    environment.update(
        {
            "TASK33_SCOPE": "|".join(scope),
            "TASK33_KEY": key,
            "TASK33_DIGEST": digest,
            "TASK33_PROBE_ID": probe_id,
        }
    )
    existing_pythonpath = environment.get("PYTHONPATH", "")
    source_paths = [
        str(REPO_ROOT / "runtimes/python-worker"),
        str(REPO_ROOT / "services/cloud-api"),
    ]
    if existing_pythonpath:
        source_paths.append(existing_pythonpath)
    environment["PYTHONPATH"] = os.pathsep.join(source_paths)

    try:
        result = subprocess.run(
            [sys.executable, "-c", child_code],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 23

        restarted = PostgresIdempotencyStore(engine)
        state = restarted.begin(scope=scope, key=key, request_digest=digest)
        with engine.connect() as connection:
            calls = connection.execute(
                text("SELECT calls FROM task33_effect_probe WHERE probe_id = :id"),
                {"id": probe_id},
            ).scalar_one()
        assert state.status is IdempotencyClaimStatus.RECONCILIATION_REQUIRED
        assert calls == 1
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM task33_effect_probe WHERE probe_id = :id"),
                {"id": probe_id},
            )
            connection.execute(
                text(
                    "DELETE FROM tool_idempotency WHERE organization_id = :org "
                    "AND tool_id = :tool AND operation_id = :op "
                    "AND idempotency_key = :key"
                ),
                {
                    "org": scope[0],
                    "tool": scope[1],
                    "op": scope[2],
                    "key": key,
                },
            )
        engine.dispose()


def test_process_crash_integration_refuses_non_test_database_before_connect(monkeypatch):
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
        test_process_crash_after_external_effect_never_replays_dispatched_tool()


def test_process_crash_integration_requires_database_when_running_in_ci(monkeypatch):
    monkeypatch.delenv("SHULIU_DATABASE_URL", raising=False)
    monkeypatch.setenv("CI", "true")

    with pytest.raises(BaseException) as error:
        _test_database_url()
    assert isinstance(error.value, RuntimeError)
    assert "CI requires a PostgreSQL test database" in str(error.value)
