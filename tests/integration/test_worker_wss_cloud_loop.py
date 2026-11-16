from __future__ import annotations

import socket
import ssl
import threading
import time
import ipaddress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
import uvicorn
from app.main import create_app
from app.persistence.postgres import events, metadata, tasks, worker_leases
from app.workers.leases import LeaseConfig, RecoveryDecision
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.x509.oid import NameOID
from shuliu_runtime.security.secret_ref import SecretRef
from shuliu_runtime.security.secret_store import SecretStore
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyOutcome, PolicyRule
from shuliu_runtime.tools.broker import IdentityPrincipal, ToolBroker
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry
from shuliu_runtime.worker.registration import WorkerRegistrationClient
from shuliu_runtime.worker.credentials import WorkerAuthenticationError

# Wider than the product default so a loaded runner is not mistaken for a refusal.
WSS_CONNECT_TIMEOUT_SECONDS = 30
from shuliu_runtime.worker.task_runner import WorkerTaskRunner
from shuliu_runtime.worker.transport import HttpWorkerRegistrationCloud
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from sqlalchemy import create_engine, insert, select


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 18, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class InMemorySecretStore(SecretStore):
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, reference: str | SecretRef) -> str:
        return self.values[str(reference)]

    def put(self, reference: str | SecretRef, value: str) -> None:
        self.values[str(reference)] = value

    def delete(self, reference: str | SecretRef) -> None:
        self.values.pop(str(reference), None)


def _certificate(directory: Path) -> tuple[Path, Path]:
    private_key = generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC).replace(tzinfo=None)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
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
        .sign(private_key, hashes.SHA256())
    )
    key_path = directory / "wss-test-key.pem"
    cert_path = directory / "wss-test-cert.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


@pytest.mark.integration
def test_real_wss_registration_heartbeat_lease_result_reconnect_and_unknown_lease():
    with TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        database_path = temp / "cloud.sqlite"
        engine = create_engine(f"sqlite+pysqlite:///{database_path}")
        metadata.create_all(engine)
        clock = MutableClock()
        lease_duration = timedelta(seconds=5)
        grace_period = timedelta(seconds=2)
        app = create_app(
            engine=engine,
            session_secret_resolver=lambda: b"test-only-worker-session-key-32bytes!",
            clock=clock,
            lease_config=LeaseConfig(
                lease_duration=lease_duration,
                heartbeat_interval=timedelta(seconds=1),
                grace_period=grace_period,
                max_attempts=3,
            ),
        )
        certificate_path, key_path = _certificate(temp)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                ssl_certfile=str(certificate_path),
                ssl_keyfile=str(key_path),
                log_level="critical",
                access_log=False,
                ws="auto",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started, "TLS WebSocket server did not start"

            tls = ssl.create_default_context(cafile=str(certificate_path))
            base_url = f"https://127.0.0.1:{port}"
            ws_url = f"wss://127.0.0.1:{port}/v1/workers/ws"
            registration_token = app.state.worker_auth.issue_registration_token(
                organization_id="org-1", expires_at=clock() + timedelta(minutes=1)
            )
            cloud = HttpWorkerRegistrationCloud(
                api_base_url=base_url, ssl_context=tls, timeout_seconds=5
            )
            secret_store = InMemorySecretStore()
            enrolled = WorkerRegistrationClient(
                cloud=cloud,
                secret_store=secret_store,
                worker_version="0.1.0",
                capabilities=("worker.runtime",),
            ).register(registration_token)
            worker_id = enrolled.worker_id
            assert worker_id.startswith("worker_")

            task_ids = ("task-wss-result", "task-wss-unknown")
            with engine.begin() as connection:
                for task_id in task_ids:
                    connection.execute(
                        insert(tasks).values(
                            task_id=task_id,
                            organization_id="org-1",
                            status="READY",
                            payload={
                                "required_capabilities": ["worker.runtime"],
                                "_worker_assignment": {"worker_id": worker_id},
                                "tool_call": {
                                    "tool_id": "foundation.wss_fixture",
                                    "arguments": {"task_id": task_id},
                                },
                            },
                            created_at=clock(),
                        )
                    )

            with cloud.connect(
                websocket_url=ws_url,
                worker_id=worker_id,
                session_credential=enrolled.session_credential,
                ssl_context=tls,
                timeout_seconds=WSS_CONNECT_TIMEOUT_SECONDS,
            ) as worker:
                heartbeat = worker.send("heartbeat", {"worker_id": "worker_forged", "sent_at": clock().isoformat()})
                assert heartbeat.message_type == "heartbeat"
                assert heartbeat.payload["worker_id"] == worker_id

                with pytest.raises(WorkerAuthenticationError) as error:
                    worker.send(
                        "lease_authorize",
                        {
                            "task_id": task_ids[0],
                            "lease_id": "legacy-unbound-lease",
                            "fencing_token": 1,
                            "idempotency_key": "legacy-unbound-effect",
                        },
                    )
                assert error.value.code == "WORKER_LEASE_ACTION_BINDING_REQUIRED"

                worker_database = SQLiteDatabase(temp / "worker.sqlite")
                worker_database.migrate()
                effect_calls = []

                class Identity:
                    def resolve(self, actor):
                        if actor != {"subject_id": worker_id, "organization_id": "org-1"}:
                            return None
                        return IdentityPrincipal(worker_id, "org-1", ("worker.execute",))

                class Executor:
                    def execute(self, tool_id, args, context, idempotency_key):
                        effect_calls.append((tool_id, dict(args), idempotency_key))
                        return {"ok": True, "evidence_ref": "wss-result-verified"}

                    def verify(self, tool_id, args, output, context):
                        return output.get("ok") is True

                registry = ToolRegistry()
                registry.register(
                    ToolDefinition(
                        "foundation.wss_fixture",
                        "1.0.0",
                        {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False},
                        {"type": "object", "properties": {"ok": {"type": "boolean"}, "evidence_ref": {"type": "string", "minLength": 1, "maxLength": 256, "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])"}}, "required": ["ok", "evidence_ref"], "additionalProperties": False},
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
                                "wss-fixture-allow",
                                "foundation.wss_fixture",
                                RiskLevel.MEDIUM,
                                PolicyOutcome.ALLOW,
                                organization_id="org-1",
                                permission="worker.execute",
                                resource_type="task",
                                privacy="INTERNAL",
                            )
                        ],
                        policy_version="wss-fixture-v1",
                    ),
                    approvals=None,
                    event_sink=lambda _event: None,
                    idempotency_store=SQLiteIdempotencyStore(worker_database),
                    clock=clock,
                )
                ack = WorkerTaskRunner(
                    connection=worker,
                    tool_broker=broker,
                    clock=clock,
                ).run_next(task_id=task_ids[0], idempotency_key="idem-wss-result")
                assert ack.message_type == "ack"
                assert ack.payload["task_id"] == task_ids[0]
                assert len(effect_calls) == 1
                assert effect_calls[0][0:2] == (
                    "foundation.wss_fixture",
                    {"task_id": task_ids[0]},
                )

                unknown_lease = worker.send(
                    "lease_request", {"task_id": task_ids[1], "idempotency_key": "idem-wss-unknown"}
                )
                assert unknown_lease.message_type == "lease"
                unknown_lease_id = unknown_lease.payload["lease_id"]

            refreshed = cloud.renew_session(
                worker_id=worker_id,
                device_key_ref=enrolled.device_key_ref,
                secret_store=secret_store,
            )
            with cloud.connect(
                websocket_url=ws_url,
                worker_id=worker_id,
                session_credential=refreshed.session_credential,
                ssl_context=tls,
                timeout_seconds=WSS_CONNECT_TIMEOUT_SECONDS,
            ) as worker:
                assert worker.send("heartbeat", {"worker_id": worker_id, "sent_at": clock().isoformat()}).message_type == "heartbeat"

            clock.advance(lease_duration + grace_period + timedelta(seconds=1))
            reconciliation = app.state.worker_coordinator.reconcile_expired(
                unknown_lease_id, decision=RecoveryDecision.OUTCOME_UNKNOWN
            )
            assert reconciliation.status == "RECONCILIATION_REQUIRED"
            with engine.connect() as connection:
                task_status = connection.execute(
                    select(tasks.c.status).where(tasks.c.task_id == task_ids[1])
                ).scalar_one()
                successful_events = connection.execute(
                    select(events.c.event_id).where(
                        events.c.idempotency_key == "idem-wss-result"
                    )
                ).all()
                lease_status = connection.execute(
                    select(worker_leases.c.status).where(
                        worker_leases.c.lease_id == unknown_lease_id
                    )
                ).scalar_one()
            assert task_status == "RECONCILIATION_REQUIRED"
            assert len(successful_events) == 1
            assert lease_status == "INDETERMINATE"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            engine.dispose()
