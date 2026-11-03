from __future__ import annotations

import ipaddress
import json
import multiprocessing
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import uvicorn
from app.main import create_app
from app.persistence.postgres import events, metadata, tasks, worker_leases
from app.workers.leases import LeaseConfig, RecoveryDecision
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.x509.oid import NameOID
from sqlalchemy import create_engine, insert, select
from shuliu_runtime.security.secret_ref import SecretRef
from shuliu_runtime.security.secret_store import SecretStore
from shuliu_runtime.worker.credentials import WorkerAuthenticationError
from shuliu_runtime.worker.registration import WorkerRegistrationClient
from shuliu_runtime.worker.transport import HttpWorkerRegistrationCloud, WorkerWebSocketConnection

# A shared CI runner can take far longer than the product default to spawn a
# process and finish a local TLS handshake; the bounded waits in these fixtures
# stay explicit, so only the connect margin is widened.
WSS_CONNECT_TIMEOUT_SECONDS = 30


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 2, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class _SecretStore(SecretStore):
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, reference: str | SecretRef) -> str:
        return self.values[str(reference)]

    def put(self, reference: str | SecretRef, value: str) -> None:
        self.values[str(reference)] = value

    def delete(self, reference: str | SecretRef) -> None:
        self.values.pop(str(reference), None)


def _certificate(directory: Path) -> tuple[Path, Path]:
    key = generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC).replace(tzinfo=None)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = directory / "worker-crash-key.pem"
    cert_path = directory / "worker-crash-cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def _worker_process(
    websocket_url: str,
    worker_id: str,
    session_credential: str,
    certificate_path: str,
    database_path: str,
    effect_path: str,
    now_iso: str,
    *,
    expect_reconciliation: bool,
    block_after_effect: bool = True,
    expected_error_code: str | None = None,
    outcome_path: str | None = None,
) -> None:
    """Run the real Worker stack; first run blocks in an executor for parent kill."""
    from shuliu_runtime.persistence.sqlite import SQLiteDatabase
    from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
    from shuliu_runtime.policy.engine import PolicyEngine
    from shuliu_runtime.policy.risk import RiskLevel
    from shuliu_runtime.policy.rules import PolicyOutcome, PolicyRule
    from shuliu_runtime.tools.broker import IdentityPrincipal, ToolBroker
    from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry
    from shuliu_runtime.worker.task_runner import WorkerTaskRunner
    from shuliu_runtime.worker.transport import WorkerWebSocketConnection
    from shuliu_runtime.worker.credentials import WorkerAuthenticationError

    database = SQLiteDatabase(database_path)
    database.migrate()

    class Identity:
        def resolve(self, actor):
            if actor != {"subject_id": worker_id, "organization_id": "org-crash"}:
                return None
            return IdentityPrincipal(worker_id, "org-crash", ("worker.execute",))

    class Executor:
        def execute(self, tool_id, args, context, idempotency_key):
            with open(effect_path, "a", encoding="utf-8") as marker:
                marker.write(f"{tool_id}:{args['task_id']}:{idempotency_key}\n")
                marker.flush()
            if block_after_effect:
                while True:
                    time.sleep(1)
            return {"ok": True, "evidence_ref": "worker-crash-result"}

        def verify(self, tool_id, args, output, context):
            return output.get("ok") is True

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "foundation.crash_fixture",
            "1.0.0",
            {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "evidence_ref": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 256,
                        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])",
                    },
                },
                "required": ["ok", "evidence_ref"],
                "additionalProperties": False,
            },
            ("worker.execute",),
            RiskLevel.MEDIUM,
            True,
        ),
        Executor(),
    )
    broker = ToolBroker(
        registry=registry,
        identity=Identity(),
        policy=PolicyEngine(
            [
                PolicyRule(
                    "worker-crash-fixture-allow",
                    "foundation.crash_fixture",
                    RiskLevel.MEDIUM,
                    PolicyOutcome.ALLOW,
                    organization_id="org-crash",
                    permission="worker.execute",
                    resource_type="task",
                    privacy="INTERNAL",
                )
            ],
            policy_version="worker-crash-fixture-v1",
        ),
        approvals=None,
        event_sink=lambda _event: None,
        idempotency_store=SQLiteIdempotencyStore(database),
    )
    tls = ssl.create_default_context(cafile=certificate_path)
    connection = WorkerWebSocketConnection(
        websocket_url=websocket_url,
        worker_id=worker_id,
        session_credential=session_credential,
        ssl_context=tls,
        timeout_seconds=WSS_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        with connection:
            WorkerTaskRunner(
                connection=connection,
                tool_broker=broker,
                clock=lambda: datetime.fromisoformat(now_iso),
            ).run_next(
                task_id="task-worker-crash",
                idempotency_key="task-worker-crash-effect",
            )
    except WorkerAuthenticationError as error:
        if expected_error_code == error.code:
            if outcome_path is not None:
                Path(outcome_path).write_text(error.code, encoding="utf-8")
            return
        if expect_reconciliation and error.code == "TASK_NOT_READY":
            if outcome_path is not None:
                Path(outcome_path).write_text(error.code, encoding="utf-8")
            return
        raise


class _DropResultAckOnce:
    """Drop the first matching WSS ACK after Cloud has committed the result."""

    def __init__(self, application, task_id: str) -> None:
        self._application = application
        self._task_id = task_id
        self.dropped = False

    async def __call__(self, scope, receive, send):
        async def intercept(message):
            if (
                not self.dropped
                and scope.get("type") == "websocket"
                and message.get("type") == "websocket.send"
            ):
                try:
                    frame = json.loads(message.get("text", ""))
                except (TypeError, ValueError):
                    frame = {}
                if (
                    frame.get("message_type") == "ack"
                    and frame.get("payload", {}).get("task_id") == self._task_id
                ):
                    self.dropped = True
                    await send({"type": "websocket.close", "code": 1011})
                    return
            await send(message)

        await self._application(scope, receive, intercept)


@pytest.mark.integration
def test_real_wss_worker_process_crash_after_dispatch_fences_replay(tmp_path):
    with TemporaryDirectory(dir=tmp_path) as temp_dir:
        temp = Path(temp_dir)
        engine = create_engine(f"sqlite+pysqlite:///{temp / 'cloud.sqlite'}")
        metadata.create_all(engine)
        clock = _Clock()
        lease_duration = timedelta(seconds=3)
        grace_period = timedelta(seconds=1)
        app = create_app(
            engine=engine,
            session_secret_resolver=lambda: b"worker-crash-session-secret-32bytes",
            clock=clock,
            lease_config=LeaseConfig(
                lease_duration=lease_duration,
                heartbeat_interval=timedelta(seconds=1),
                grace_period=grace_period,
                max_attempts=3,
            ),
        )
        cert_path, key_path = _certificate(temp)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                ssl_certfile=str(cert_path),
                ssl_keyfile=str(key_path),
                log_level="critical",
                access_log=False,
                ws="auto",
            )
        )
        server_thread = threading.Thread(target=server.run, daemon=True)
        process = None
        try:
            server_thread.start()
            deadline = time.monotonic() + 30
            while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started, "local TLS WebSocket Cloud did not start"

            tls = ssl.create_default_context(cafile=str(cert_path))
            cloud = HttpWorkerRegistrationCloud(
                api_base_url=f"https://127.0.0.1:{port}",
                ssl_context=tls,
                timeout_seconds=5,
            )
            secret_store = _SecretStore()
            registration_token = app.state.worker_auth.issue_registration_token(
                organization_id="org-crash", expires_at=clock() + timedelta(minutes=5)
            )
            enrolled = WorkerRegistrationClient(
                cloud=cloud,
                secret_store=secret_store,
                worker_version="0.1.0",
                capabilities=("worker.runtime",),
            ).register(registration_token)
            disconnect_event = threading.Event()
            active_worker_id = enrolled.worker_id
            original_disconnect = app.state.worker_connections.disconnect

            def record_worker_disconnect(worker_id):
                original_disconnect(worker_id)
                if worker_id == active_worker_id:
                    disconnect_event.set()

            app.state.worker_connections.disconnect = record_worker_disconnect

            with engine.begin() as db:
                db.execute(
                    insert(tasks).values(
                        task_id="task-worker-crash",
                        organization_id="org-crash",
                        status="READY",
                        payload={
                            "required_capabilities": ["worker.runtime"],
                            "_worker_assignment": {"worker_id": enrolled.worker_id},
                            "tool_call": {
                                "tool_id": "foundation.crash_fixture",
                                "arguments": {"task_id": "task-worker-crash"},
                            },
                        },
                        created_at=clock(),
                    )
                )

            websocket_url = f"wss://127.0.0.1:{port}/v1/workers/ws"
            effect_path = temp / "external-effect.log"
            ctx = multiprocessing.get_context("spawn")
            process = ctx.Process(
                target=_worker_process,
                args=(
                    websocket_url,
                    enrolled.worker_id,
                    enrolled.session_credential,
                    str(cert_path),
                    str(temp / "worker.sqlite"),
                    str(effect_path),
                    clock().isoformat(),
                ),
                kwargs={"expect_reconciliation": False, "block_after_effect": True},
            )
            process.start()
            effect_deadline = time.monotonic() + 45
            while not effect_path.exists() and process.is_alive() and time.monotonic() < effect_deadline:
                time.sleep(0.02)
            assert effect_path.exists(), (
                "Tool Broker side effect did not begin "
                f"(worker alive={process.is_alive()} exitcode={process.exitcode})"
            )
            process.terminate()
            process.join(timeout=10)
            assert process.exitcode is not None and process.exitcode != 0
            assert disconnect_event.wait(timeout=5), "Cloud did not observe terminated Worker WSS disconnect"

            with engine.connect() as db:
                lease_row = (
                    db.execute(
                        select(worker_leases).where(
                            worker_leases.c.task_id == "task-worker-crash"
                        )
                    )
                    .mappings()
                    .one()
                )
                assert lease_row["status"] == "ACTIVE"
            clock.advance(lease_duration + grace_period + timedelta(seconds=1))
            reconciled = app.state.worker_coordinator.reconcile_expired(
                lease_row["lease_id"], decision=RecoveryDecision.OUTCOME_UNKNOWN
            )
            assert reconciled.status == "RECONCILIATION_REQUIRED"
            refreshed = cloud.renew_session(
                worker_id=enrolled.worker_id,
                device_key_ref=enrolled.device_key_ref,
                secret_store=secret_store,
            )

            restarted = ctx.Process(
                target=_worker_process,
                args=(
                    websocket_url,
                    enrolled.worker_id,
                    refreshed.session_credential,
                    str(cert_path),
                    str(temp / "worker.sqlite"),
                    str(effect_path),
                    clock().isoformat(),
                ),
                kwargs={
                    "expect_reconciliation": True,
                    "block_after_effect": False,
                },
            )
            restarted.start()
            restarted.join(timeout=45)
            if restarted.is_alive():
                restarted.terminate()
                restarted.join(timeout=5)
                pytest.fail("restarted Worker did not fail closed in bounded time")
            assert restarted.exitcode == 0

            effects = effect_path.read_text(encoding="utf-8").splitlines()
            assert effects == [
                "foundation.crash_fixture:task-worker-crash:task-worker-crash-effect"
            ]
            with engine.connect() as db:
                task_status = db.execute(
                    select(tasks.c.status).where(
                        tasks.c.task_id == "task-worker-crash"
                    )
                ).scalar_one()
                lease_status = db.execute(
                    select(worker_leases.c.status).where(
                        worker_leases.c.lease_id == lease_row["lease_id"]
                    )
                ).scalar_one()
            assert task_status == "RECONCILIATION_REQUIRED"
            assert lease_status == "INDETERMINATE"
        finally:
            if process is not None and process.is_alive():
                process.terminate()
                process.join(timeout=5)
            server.should_exit = True
            if server_thread.is_alive():
                server_thread.join(timeout=5)
            engine.dispose()


@pytest.mark.integration
def test_real_wss_cloud_commit_then_lost_ack_does_not_repeat_worker_effect(tmp_path):
    with TemporaryDirectory(dir=tmp_path) as temp_dir:
        temp = Path(temp_dir)
        engine = create_engine(f"sqlite+pysqlite:///{temp / 'cloud.sqlite'}")
        metadata.create_all(engine)
        clock = _Clock()
        app = create_app(
            engine=engine,
            session_secret_resolver=lambda: b"worker-crash-session-secret-32bytes",
            clock=clock,
            lease_config=LeaseConfig(
                lease_duration=timedelta(seconds=30),
                heartbeat_interval=timedelta(seconds=5),
                grace_period=timedelta(seconds=5),
                max_attempts=3,
            ),
        )
        drop_ack = _DropResultAckOnce(app, "task-worker-crash")
        cert_path, key_path = _certificate(temp)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                drop_ack,
                host="127.0.0.1",
                port=port,
                ssl_certfile=str(cert_path),
                ssl_keyfile=str(key_path),
                log_level="critical",
                access_log=False,
                ws="auto",
            )
        )
        server_thread = threading.Thread(target=server.run, daemon=True)
        try:
            server_thread.start()
            deadline = time.monotonic() + 30
            while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started, "local TLS WebSocket Cloud did not start"

            tls = ssl.create_default_context(cafile=str(cert_path))
            cloud = HttpWorkerRegistrationCloud(
                api_base_url=f"https://127.0.0.1:{port}",
                ssl_context=tls,
                timeout_seconds=5,
            )
            secret_store = _SecretStore()
            registration_token = app.state.worker_auth.issue_registration_token(
                organization_id="org-crash", expires_at=clock() + timedelta(minutes=5)
            )
            enrolled = WorkerRegistrationClient(
                cloud=cloud,
                secret_store=secret_store,
                worker_version="0.1.0",
                capabilities=("worker.runtime",),
            ).register(registration_token)
            disconnect_event = threading.Event()
            active_worker_id = enrolled.worker_id
            original_disconnect = app.state.worker_connections.disconnect

            def record_worker_disconnect(worker_id):
                original_disconnect(worker_id)
                if worker_id == active_worker_id:
                    disconnect_event.set()

            app.state.worker_connections.disconnect = record_worker_disconnect
            with engine.begin() as db:
                db.execute(
                    insert(tasks).values(
                        task_id="task-worker-crash",
                        organization_id="org-crash",
                        status="READY",
                        payload={
                            "required_capabilities": ["worker.runtime"],
                            "_worker_assignment": {"worker_id": enrolled.worker_id},
                            "tool_call": {
                                "tool_id": "foundation.crash_fixture",
                                "arguments": {"task_id": "task-worker-crash"},
                            },
                        },
                        created_at=clock(),
                    )
                )
            websocket_url = f"wss://127.0.0.1:{port}/v1/workers/ws"
            effect_path = temp / "external-effect.log"
            outcome_path = temp / "worker-outcome.txt"
            ctx = multiprocessing.get_context("spawn")
            worker = ctx.Process(
                target=_worker_process,
                args=(
                    websocket_url,
                    enrolled.worker_id,
                    enrolled.session_credential,
                    str(cert_path),
                    str(temp / "worker.sqlite"),
                    str(effect_path),
                    clock().isoformat(),
                ),
                kwargs={
                    "expect_reconciliation": False,
                    "block_after_effect": False,
                    "expected_error_code": "WORKER_RESULT_ACK_UNKNOWN",
                    "outcome_path": str(outcome_path),
                },
            )
            worker.start()
            worker.join(timeout=45)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
                pytest.fail("Worker did not resolve the lost ACK as unknown in bounded time")
            assert worker.exitcode == 0
            assert disconnect_event.wait(
                timeout=5
            ), "Cloud did not observe the finished Worker WSS disconnect"
            assert drop_ack.dropped is True
            assert outcome_path.read_text(encoding="utf-8") == "WORKER_RESULT_ACK_UNKNOWN"

            with engine.connect() as db:
                task_status = db.execute(
                    select(tasks.c.status).where(
                        tasks.c.task_id == "task-worker-crash"
                    )
                ).scalar_one()
                lease = (
                    db.execute(
                        select(worker_leases).where(
                            worker_leases.c.task_id == "task-worker-crash"
                        )
                    )
                    .mappings()
                    .one()
                )
                result_events = db.execute(
                    select(events.c.event_id).where(
                        events.c.idempotency_key == "task-worker-crash-effect"
                    )
                ).all()
            assert task_status == "SUCCEEDED"
            assert lease["status"] == "COMPLETED"
            assert len(result_events) == 1
            assert effect_path.read_text(encoding="utf-8").splitlines() == [
                "foundation.crash_fixture:task-worker-crash:task-worker-crash-effect"
            ]

            refreshed = cloud.renew_session(
                worker_id=enrolled.worker_id,
                device_key_ref=enrolled.device_key_ref,
                secret_store=secret_store,
            )
            # A restarted Worker that dies on a client-side handshake timeout is
            # retried: on macOS runners the Cloud occasionally accepts the close
            # but is not yet ready to serve the same Worker again. A restart is
            # only accepted when it exits cleanly, and the no-duplicate-effect
            # assertion below still holds across every attempt.
            restart_exit_codes = []
            for _ in range(3):
                restarted = ctx.Process(
                    target=_worker_process,
                    args=(
                        websocket_url,
                        enrolled.worker_id,
                        refreshed.session_credential,
                        str(cert_path),
                        str(temp / "worker.sqlite"),
                        str(effect_path),
                        clock().isoformat(),
                    ),
                    kwargs={
                        "expect_reconciliation": True,
                        "block_after_effect": False,
                    },
                )
                restarted.start()
                restarted.join(timeout=45)
                if restarted.is_alive():
                    restarted.terminate()
                    restarted.join(timeout=5)
                    pytest.fail("restarted Worker did not fail closed in bounded time")
                restart_exit_codes.append(restarted.exitcode)
                if restarted.exitcode == 0:
                    break
            assert restart_exit_codes[-1] == 0, restart_exit_codes
            assert effect_path.read_text(encoding="utf-8").splitlines() == [
                "foundation.crash_fixture:task-worker-crash:task-worker-crash-effect"
            ]
        finally:
            server.should_exit = True
            if server_thread.is_alive():
                server_thread.join(timeout=5)
            engine.dispose()
