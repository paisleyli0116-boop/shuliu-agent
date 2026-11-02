from __future__ import annotations

import ipaddress
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
from app.persistence.postgres import metadata, tasks, worker_leases
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
from shuliu_runtime.worker.task_runner import WorkerTaskRunner
from shuliu_runtime.worker.transport import HttpWorkerRegistrationCloud, WorkerWebSocketConnection


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
            if not expect_reconciliation:
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
        timeout_seconds=5,
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
        if expect_reconciliation and error.code == "TASK_NOT_READY":
            return
        raise


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
            deadline = time.monotonic() + 10
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
            with engine.begin() as db:
                db.execute(
                    insert(tasks).values(
                        task_id="task-worker-crash",
                        organization_id="org-crash",
                        status="READY",
                        payload={
                            "required_capabilities": ["worker.runtime"],
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
                kwargs={"expect_reconciliation": False},
            )
            process.start()
            effect_deadline = time.monotonic() + 15
            while not effect_path.exists() and process.is_alive() and time.monotonic() < effect_deadline:
                time.sleep(0.02)
            assert effect_path.exists(), "Tool Broker side effect did not begin"
            process.terminate()
            process.join(timeout=10)
            assert process.exitcode is not None and process.exitcode != 0

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
                kwargs={"expect_reconciliation": True},
            )
            restarted.start()
            restarted.join(timeout=15)
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
