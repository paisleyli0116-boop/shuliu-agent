from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.tools.idempotency import IdempotencyClaimStatus


SCOPE = ("org-1", "foundation.document.write", "run-1")


def test_dispatched_claim_survives_database_reopen_as_reconciliation_required(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    store = SQLiteIdempotencyStore(database)

    first = store.begin(scope=SCOPE, key="idem-1", request_digest="digest-1")
    assert first.status is IdempotencyClaimStatus.NEW
    store.mark_dispatched(scope=SCOPE, key="idem-1", request_digest="digest-1")

    reopened = SQLiteIdempotencyStore(SQLiteDatabase(path))
    after_restart = reopened.begin(
        scope=SCOPE, key="idem-1", request_digest="digest-1"
    )

    assert after_restart.status is IdempotencyClaimStatus.RECONCILIATION_REQUIRED


def test_completed_result_survives_database_reopen_and_digest_conflicts(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    store = SQLiteIdempotencyStore(database)
    store.begin(scope=SCOPE, key="idem-2", request_digest="digest-2")
    store.mark_dispatched(scope=SCOPE, key="idem-2", request_digest="digest-2")
    result = {"result_ref": "evidence:verified-123", "verification": "VERIFIED"}
    store.complete(
        scope=SCOPE, key="idem-2", request_digest="digest-2", result=result
    )

    reopened = SQLiteIdempotencyStore(SQLiteDatabase(path))
    duplicate = reopened.begin(
        scope=SCOPE, key="idem-2", request_digest="digest-2"
    )
    conflict = reopened.begin(
        scope=SCOPE, key="idem-2", request_digest="different-digest"
    )

    assert duplicate.status is IdempotencyClaimStatus.COMPLETED
    assert duplicate.result == {"result_ref": "evidence:verified-123"}
    assert conflict.status is IdempotencyClaimStatus.CONFLICT


def test_release_before_dispatch_cannot_clear_a_dispatched_claim(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite3")
    database.migrate()
    store = SQLiteIdempotencyStore(database)
    store.begin(scope=SCOPE, key="idem-3", request_digest="digest-3")
    store.mark_dispatched(scope=SCOPE, key="idem-3", request_digest="digest-3")

    store.release_before_dispatch(
        scope=SCOPE, key="idem-3", request_digest="digest-3"
    )

    repeated = store.begin(scope=SCOPE, key="idem-3", request_digest="digest-3")
    assert repeated.status is IdempotencyClaimStatus.RECONCILIATION_REQUIRED


def test_completion_without_reference_stays_reconciliation_required(tmp_path):
    database = SQLiteDatabase(tmp_path / "missing-reference.sqlite3")
    database.migrate()
    store = SQLiteIdempotencyStore(database)
    store.begin(scope=SCOPE, key="idem-no-ref", request_digest="digest-no-ref")
    store.mark_dispatched(
        scope=SCOPE, key="idem-no-ref", request_digest="digest-no-ref"
    )

    with pytest.raises(ValueError, match="reference"):
        store.complete(
            scope=SCOPE,
            key="idem-no-ref",
            request_digest="digest-no-ref",
            result={"output": {"private": "not a reference"}},
        )

    repeated = store.begin(
        scope=SCOPE, key="idem-no-ref", request_digest="digest-no-ref"
    )
    assert repeated.status is IdempotencyClaimStatus.RECONCILIATION_REQUIRED


def test_dispatched_claim_is_reconciliation_required_in_a_new_process(tmp_path):
    path = tmp_path / "process-restart.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    store = SQLiteIdempotencyStore(database)
    store.begin(scope=SCOPE, key="idem-process", request_digest="digest-process")
    store.mark_dispatched(
        scope=SCOPE, key="idem-process", request_digest="digest-process"
    )
    source_root = os.path.abspath("runtimes/python-worker")
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, existing_pythonpath] if existing_pythonpath else [source_root]
    )
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "from shuliu_runtime.persistence.sqlite import SQLiteDatabase; "
            "from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore; "
            f"store=SQLiteIdempotencyStore(SQLiteDatabase({str(path)!r})); "
            f"claim=store.begin(scope={SCOPE!r}, key='idem-process', "
            "request_digest='digest-process'); print(claim.status.value)",
        ],
        cwd=os.path.abspath("."),
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert child.returncode == 0
    assert child.stdout.strip() == IdempotencyClaimStatus.RECONCILIATION_REQUIRED.value


def test_concurrent_sqlite_begin_grants_exactly_one_dispatchable_claim(tmp_path):
    database = SQLiteDatabase(tmp_path / "concurrent.sqlite3")
    database.migrate()
    barrier = Barrier(2)

    def begin_once():
        local_store = SQLiteIdempotencyStore(SQLiteDatabase(database.path))
        barrier.wait(timeout=5)
        return local_store.begin(
            scope=SCOPE, key="idem-concurrent", request_digest="digest-concurrent"
        ).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: begin_once(), range(2)))

    assert statuses.count(IdempotencyClaimStatus.NEW) == 1
    assert statuses.count(IdempotencyClaimStatus.IN_PROGRESS) == 1


@pytest.mark.parametrize(
    ("scope", "key", "request_digest"),
    [
        (("o" * 129, "tool", "run-1"), "key", "digest"),
        (SCOPE, "k" * 257, "digest"),
        (SCOPE, "key", "d" * 129),
    ],
)
def test_sqlite_store_rejects_values_outside_shared_backend_limits(
    tmp_path, scope, key, request_digest
):
    database = SQLiteDatabase(tmp_path / "limits.sqlite3")
    database.migrate()

    with pytest.raises(ValueError):
        SQLiteIdempotencyStore(database).begin(
            scope=scope, key=key, request_digest=request_digest
        )
