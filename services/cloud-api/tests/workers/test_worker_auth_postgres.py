from __future__ import annotations

import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.persistence.postgres import (
    worker_registration_tokens,
    workers,
)
from app.workers.auth import WorkerAuthenticationError, WorkerCredentialService
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from shuliu_runtime.worker.credentials import (
    registration_proof_bytes,
    session_proof_bytes,
)
from sqlalchemy import select

from app.persistence.postgres import create_postgres_engine


SERVICE_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_to_head(connection) -> None:
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@pytest.mark.integration
def test_postgres_worker_registration_and_session_replay_protection():
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.skip("SHULIU_DATABASE_URL is not configured")
    engine = create_postgres_engine(database_url)
    try:
        with engine.connect() as connection:
            _upgrade_to_head(connection)
            connection.commit()

        now = datetime(2026, 10, 18, tzinfo=UTC)
        service = WorkerCredentialService(
            engine=engine,
            session_secret_resolver=lambda: b"test-only-worker-session-key-32bytes!",
            clock=lambda: now,
        )
        organization_id = f"org-worker-auth-{uuid4().hex}"
        registration_token = service.issue_registration_token(
            organization_id=organization_id, expires_at=now + timedelta(minutes=1)
        )
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        payload = {
            "registration_token": registration_token,
            "worker_version": "0.1.0",
            "protocol_version": 1,
            "capabilities": ["worker.runtime"],
            "device_key_algorithm": "Ed25519",
            "device_public_key": _b64(public_key),
        }
        payload["device_signature"] = _b64(
            private_key.sign(registration_proof_bytes(payload))
        )

        registered = service.register(payload)

        assert registered.organization_id == organization_id
        identity = service.authenticate_session(registered.credential)
        assert identity[:2] == (
            registered.worker_id,
            organization_id,
        )
        with pytest.raises(WorkerAuthenticationError) as replay:
            service.authenticate_session(registered.credential)
        assert replay.value.code == "WORKER_SESSION_REPLAYED"

        challenge = service.issue_challenge(worker_id=registered.worker_id)
        proof = _b64(
            private_key.sign(
                session_proof_bytes(
                    worker_id=registered.worker_id,
                    challenge_id=str(challenge["challenge_id"]),
                    nonce=str(challenge["nonce"]),
                )
            )
        )
        refreshed = service.create_session(
            worker_id=registered.worker_id,
            challenge_id=str(challenge["challenge_id"]),
            device_signature=proof,
        )
        assert service.authenticate_session(refreshed.credential)[:2] == (
            registered.worker_id,
            organization_id,
        )
        with engine.connect() as connection:
            saved_token = connection.execute(
                select(worker_registration_tokens).where(
                    worker_registration_tokens.c.token_digest
                    == hashlib.sha256(registration_token.encode()).hexdigest()
                )
            ).mappings().one()
            saved_worker = connection.execute(
                select(workers).where(workers.c.worker_id == registered.worker_id)
            ).mappings().one()
        assert saved_token["token_digest"] != registration_token
        assert saved_token["consumed_at"] is not None
        assert saved_worker["device_public_key"] == public_key
    finally:
        engine.dispose()
