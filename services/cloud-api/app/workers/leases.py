from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.persistence.postgres import events, tasks, worker_leases, workers
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


class WorkerCoordinatorError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkerPrincipal:
    """Identity established by the authenticated Cloud connection layer."""

    worker_id: str
    organization_id: str


@dataclass(frozen=True)
class LeaseConfig:
    lease_duration: timedelta
    heartbeat_interval: timedelta
    grace_period: timedelta
    max_attempts: int

    def __post_init__(self) -> None:
        if self.lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if self.heartbeat_interval <= timedelta(0):
            raise ValueError("heartbeat_interval must be positive")
        if self.grace_period < timedelta(0):
            raise ValueError("grace_period cannot be negative")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


class RecoveryDecision(StrEnum):
    RETRY_SAFE = "RETRY_SAFE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass(frozen=True)
class LeaseGrant:
    lease_id: str
    task_id: str
    worker_id: str
    organization_id: str
    expires_at: datetime
    fencing_token: int
    idempotency_key: str
    task_payload: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True)
class HeartbeatReceipt:
    worker_id: str
    received_at: datetime


@dataclass(frozen=True)
class WorkerResultAck:
    event_id: str
    task_id: str
    lease_id: str
    idempotency_key: str
    duplicate: bool


@dataclass(frozen=True)
class WorkerDispatchPermit:
    task_id: str
    lease_id: str
    worker_id: str
    fencing_token: int
    idempotency_key: str
    action_digest: str
    expires_at: datetime


@dataclass(frozen=True)
class ReconciliationResult:
    lease_id: str
    task_id: str
    status: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class PostgresLeaseRepository:
    """Transactional lease and result-ACK persistence over Cloud PostgreSQL."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record_authenticated_worker(
        self,
        principal: WorkerPrincipal,
        *,
        worker_version: str,
        protocol_version: int,
        capabilities: tuple[str, ...],
        now: datetime,
    ) -> None:
        if not principal.worker_id or not principal.organization_id:
            raise ValueError("authenticated Worker identity is required")
        if (
            not worker_version.strip()
            or len(worker_version) > 64
            or type(protocol_version) is not int
            or protocol_version != 1
        ):
            raise WorkerCoordinatorError(
                "WORKER_PROTOCOL_UNSUPPORTED", "worker protocol is not supported"
            )
        if any(not item.strip() for item in capabilities) or len(
            set(capabilities)
        ) != len(capabilities):
            raise ValueError("worker capabilities must be non-empty and unique")

        values = {
            "status": "ONLINE",
            "worker_version": worker_version,
            "protocol_version": protocol_version,
            "capabilities": list(capabilities),
            "last_heartbeat_at": now,
        }
        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    select(workers).where(workers.c.worker_id == principal.worker_id)
                )
                .mappings()
                .first()
            )
            if existing is None:
                connection.execute(
                    insert(workers).values(
                        worker_id=principal.worker_id,
                        organization_id=principal.organization_id,
                        created_at=now,
                        **values,
                    )
                )
                return
            if existing["organization_id"] != principal.organization_id:
                raise WorkerCoordinatorError(
                    "WORKER_ORGANIZATION_MISMATCH",
                    "authenticated Worker belongs to a different organization",
                )
            if existing["status"] in {"REVOKED", "DISABLED"}:
                raise WorkerCoordinatorError(
                    "WORKER_NOT_ACTIVE", "revoked or disabled Worker cannot reconnect"
                )
            connection.execute(
                update(workers)
                .where(workers.c.worker_id == principal.worker_id)
                .values(**values)
            )

    def heartbeat(
        self, principal: WorkerPrincipal, *, now: datetime
    ) -> HeartbeatReceipt:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(workers.c.status).where(
                    workers.c.worker_id == principal.worker_id,
                    workers.c.organization_id == principal.organization_id,
                )
            ).first()
            if row is None:
                raise WorkerCoordinatorError(
                    "WORKER_NOT_REGISTERED", "authenticated Worker is not registered"
                )
            if row.status in {"REVOKED", "DISABLED"}:
                raise WorkerCoordinatorError(
                    "WORKER_NOT_ACTIVE",
                    "revoked or disabled Worker cannot send heartbeat",
                )
            connection.execute(
                update(workers)
                .where(
                    workers.c.worker_id == principal.worker_id,
                    workers.c.organization_id == principal.organization_id,
                )
                .values(status="ONLINE", last_heartbeat_at=now)
            )
        return HeartbeatReceipt(principal.worker_id, now)

    def disconnected(self, principal: WorkerPrincipal) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(workers)
                .where(
                    workers.c.worker_id == principal.worker_id,
                    workers.c.organization_id == principal.organization_id,
                    workers.c.status.not_in(["REVOKED", "DISABLED"]),
                )
                .values(status="DISCONNECTED")
            )

    def issue_lease(
        self,
        task_id: str,
        principal: WorkerPrincipal,
        *,
        idempotency_key: str,
        config: LeaseConfig,
        now: datetime,
    ) -> LeaseGrant:
        if not idempotency_key.strip() or len(idempotency_key) > 256:
            raise ValueError("idempotency_key is required")
        failure: WorkerCoordinatorError | None = None
        grant: LeaseGrant | None = None
        with self._engine.begin() as connection:
            task = (
                connection.execute(
                    select(tasks)
                    .where(
                        tasks.c.task_id == task_id,
                        tasks.c.organization_id == principal.organization_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if task is None:
                raise WorkerCoordinatorError("TASK_NOT_FOUND", "task was not found")
            if task["status"] not in {"READY", "QUEUED"}:
                raise WorkerCoordinatorError(
                    "TASK_NOT_READY", "task is not ready for leasing"
                )

            worker = (
                connection.execute(
                    select(workers).where(
                        workers.c.worker_id == principal.worker_id,
                        workers.c.organization_id == principal.organization_id,
                    )
                )
                .mappings()
                .first()
            )
            if worker is None:
                raise WorkerCoordinatorError(
                    "WORKER_NOT_REGISTERED", "authenticated Worker is not registered"
                )
            if worker["status"] != "ONLINE":
                raise WorkerCoordinatorError(
                    "WORKER_NOT_ONLINE", "Worker is not online"
                )
            last_heartbeat_at = worker["last_heartbeat_at"]
            if last_heartbeat_at is None or _utc(now) - _utc(last_heartbeat_at) > (
                config.heartbeat_interval + config.grace_period
            ):
                raise WorkerCoordinatorError(
                    "WORKER_HEARTBEAT_STALE",
                    "Worker heartbeat is outside its grace window",
                )

            payload = task["payload"]
            if not isinstance(payload, Mapping):
                raise WorkerCoordinatorError(
                    "TASK_PAYLOAD_INVALID", "task payload is invalid"
                )
            assignment = payload.get("_worker_assignment")
            if (
                not isinstance(assignment, Mapping)
                or set(assignment) != {"worker_id"}
                or assignment.get("worker_id") != principal.worker_id
            ):
                raise WorkerCoordinatorError(
                    "WORKER_TASK_NOT_ASSIGNED",
                    "task is not assigned to the authenticated Worker",
                )
            required = payload.get("required_capabilities", [])
            available = worker["capabilities"]
            if (
                not isinstance(required, list)
                or any(
                    not isinstance(item, str) or not item.strip() for item in required
                )
                or not isinstance(available, list)
                or not set(required).issubset(set(available))
            ):
                raise WorkerCoordinatorError(
                    "WORKER_CAPABILITY_MISMATCH",
                    "Worker does not satisfy task capabilities",
                )

            attempts = connection.execute(
                select(func.count())
                .select_from(worker_leases)
                .where(
                    worker_leases.c.organization_id == principal.organization_id,
                    worker_leases.c.task_id == task_id,
                )
            ).scalar_one()
            if attempts >= config.max_attempts:
                connection.execute(
                    update(tasks)
                    .where(tasks.c.task_id == task_id)
                    .values(status="DEAD_LETTER")
                )
                failure = WorkerCoordinatorError(
                    "TASK_DEAD_LETTER", "task exhausted its configured lease attempts"
                )
            else:
                latest_token = connection.execute(
                    select(worker_leases.c.fencing_token)
                    .where(
                        worker_leases.c.organization_id == principal.organization_id,
                        worker_leases.c.task_id == task_id,
                    )
                    .order_by(worker_leases.c.fencing_token.desc())
                    .limit(1)
                ).scalar_one_or_none()
                token = (latest_token or 0) + 1
                lease_id = str(uuid4())
                expires_at = now + config.lease_duration
                connection.execute(
                    insert(worker_leases).values(
                        lease_id=lease_id,
                        organization_id=principal.organization_id,
                        worker_id=principal.worker_id,
                        task_id=task_id,
                        fencing_token=token,
                        status="ACTIVE",
                        expires_at=expires_at,
                        idempotency_key=idempotency_key,
                    )
                )
                connection.execute(
                    update(tasks)
                    .where(tasks.c.task_id == task_id)
                    .values(status="LEASED")
                )
                grant = LeaseGrant(
                    lease_id=lease_id,
                    task_id=task_id,
                    worker_id=principal.worker_id,
                    organization_id=principal.organization_id,
                    expires_at=expires_at,
                    fencing_token=token,
                    idempotency_key=idempotency_key,
                    task_payload=dict(payload),
                )
        if failure is not None:
            raise failure
        assert grant is not None
        return grant

    def next_assigned_task_id(self, principal: WorkerPrincipal) -> str | None:
        """Return the oldest ready task explicitly assigned to this Worker."""
        statement = (
            select(tasks.c.task_id)
            .where(
                tasks.c.organization_id == principal.organization_id,
                tasks.c.status.in_(("READY", "QUEUED")),
                tasks.c.payload["_worker_assignment"]["worker_id"].as_string()
                == principal.worker_id,
            )
            .order_by(tasks.c.created_at, tasks.c.task_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        with self._engine.connect() as connection:
            return connection.execute(statement).scalar_one_or_none()

    def renew(
        self,
        lease_id: str,
        principal: WorkerPrincipal,
        *,
        fencing_token: int,
        config: LeaseConfig,
        now: datetime,
    ) -> LeaseGrant:
        renewed: LeaseGrant | None = None
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    select(worker_leases)
                    .where(
                        worker_leases.c.lease_id == lease_id,
                        worker_leases.c.organization_id == principal.organization_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None or row["worker_id"] != principal.worker_id:
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_NOT_FOUND", "lease was not found"
                )
            if row["fencing_token"] != fencing_token or row["status"] != "ACTIVE":
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_FENCED", "lease token is stale"
                )
            if _utc(row["expires_at"]) <= _utc(now):
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_EXPIRED", "lease has expired"
                )
            task = (
                connection.execute(
                    select(tasks).where(
                        tasks.c.task_id == row["task_id"],
                        tasks.c.organization_id == principal.organization_id,
                    )
                )
                .mappings()
                .one()
            )
            expires_at = now + config.lease_duration
            connection.execute(
                update(worker_leases)
                .where(worker_leases.c.lease_id == lease_id)
                .values(expires_at=expires_at)
            )
            connection.execute(
                update(workers)
                .where(workers.c.worker_id == principal.worker_id)
                .values(status="ONLINE", last_heartbeat_at=now)
            )
            renewed = LeaseGrant(
                lease_id=lease_id,
                task_id=row["task_id"],
                worker_id=principal.worker_id,
                organization_id=principal.organization_id,
                expires_at=expires_at,
                fencing_token=fencing_token,
                idempotency_key=row["idempotency_key"] or "",
                task_payload=dict(task["payload"]),
            )
        assert renewed is not None
        return renewed

    def authorize_tool_dispatch(
        self,
        principal: WorkerPrincipal,
        *,
        task_id: str,
        lease_id: str,
        fencing_token: int,
        idempotency_key: str,
        action_digest: str,
        config: LeaseConfig,
        now: datetime,
    ) -> WorkerDispatchPermit:
        """Revalidate the active lease immediately before a Worker dispatches a Tool."""
        if (
            not task_id.strip()
            or not lease_id.strip()
            or not idempotency_key.strip()
            or isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
            or fencing_token < 1
        ):
            raise WorkerCoordinatorError(
                "WORKER_LEASE_FENCED", "Worker lease dispatch authorization is invalid"
            )
        with self._engine.begin() as connection:
            initial = (
                connection.execute(
                    select(worker_leases).where(
                        worker_leases.c.lease_id == lease_id,
                        worker_leases.c.organization_id == principal.organization_id,
                    )
                )
                .mappings()
                .first()
            )
            if initial is None or initial["worker_id"] != principal.worker_id:
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_NOT_FOUND", "lease was not found"
                )
            if initial["task_id"] != task_id:
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_FENCED", "lease does not authorize this task"
                )
            task = (
                connection.execute(
                    select(tasks)
                    .where(
                        tasks.c.task_id == task_id,
                        tasks.c.organization_id == principal.organization_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            lease = (
                connection.execute(
                    select(worker_leases)
                    .where(worker_leases.c.lease_id == lease_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if task is None or lease is None:
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_NOT_FOUND", "lease was not found"
                )
            expected_action_digest = _task_action_digest(task["payload"])
            if (
                not isinstance(action_digest, str)
                or len(action_digest) != 64
                or any(character not in "0123456789abcdef" for character in action_digest)
                or action_digest != expected_action_digest
            ):
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_ACTION_MISMATCH",
                    "Tool action does not match the leased task",
                )
            if (
                lease["worker_id"] != principal.worker_id
                or lease["task_id"] != task_id
                or lease["fencing_token"] != fencing_token
                or lease["idempotency_key"] != idempotency_key
                or lease["status"] != "ACTIVE"
            ):
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_FENCED", "lease token is stale"
                )
            if _utc(lease["expires_at"]) <= _utc(now):
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_EXPIRED", "lease has expired"
                )
            if task["status"] != "LEASED":
                raise WorkerCoordinatorError(
                    "TASK_NOT_LEASED", "task is not currently leased"
                )
            latest_fencing_token = connection.execute(
                select(func.max(worker_leases.c.fencing_token)).where(
                    worker_leases.c.organization_id == principal.organization_id,
                    worker_leases.c.task_id == task_id,
                )
            ).scalar_one_or_none()
            if latest_fencing_token != fencing_token:
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_FENCED", "lease generation is stale"
                )
            worker = (
                connection.execute(
                    select(workers).where(
                        workers.c.worker_id == principal.worker_id,
                        workers.c.organization_id == principal.organization_id,
                    )
                )
                .mappings()
                .first()
            )
            if worker is None or worker["status"] in {"REVOKED", "DISABLED"}:
                raise WorkerCoordinatorError(
                    "WORKER_NOT_ACTIVE", "Worker cannot dispatch Tools"
                )
            last_heartbeat = worker["last_heartbeat_at"]
            if (
                worker["status"] != "ONLINE"
                or last_heartbeat is None
                or _utc(now) - _utc(last_heartbeat)
                > config.heartbeat_interval + config.grace_period
            ):
                raise WorkerCoordinatorError(
                    "WORKER_HEARTBEAT_STALE", "Worker heartbeat is outside its grace window"
                )
            return WorkerDispatchPermit(
                task_id=task_id,
                lease_id=lease_id,
                worker_id=principal.worker_id,
                fencing_token=fencing_token,
                idempotency_key=idempotency_key,
                action_digest=expected_action_digest,
                expires_at=_utc(lease["expires_at"]),
            )

    def reconcile_expired(
        self,
        lease_id: str,
        *,
        decision: RecoveryDecision,
        config: LeaseConfig,
        now: datetime,
    ) -> ReconciliationResult:
        with self._engine.begin() as connection:
            initial = (
                connection.execute(
                    select(worker_leases).where(worker_leases.c.lease_id == lease_id)
                )
                .mappings()
                .first()
            )
            if initial is None:
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_NOT_FOUND", "lease was not found"
                )
            connection.execute(
                select(tasks)
                .where(
                    tasks.c.task_id == initial["task_id"],
                    tasks.c.organization_id == initial["organization_id"],
                )
                .with_for_update()
            ).mappings().one()
            lease = (
                connection.execute(
                    select(worker_leases)
                    .where(worker_leases.c.lease_id == lease_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if lease["status"] != "ACTIVE":
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_NOT_ACTIVE", "only active leases can be reconciled"
                )
            expires_at = _utc(lease["expires_at"])
            if _utc(now) < expires_at + config.grace_period:
                raise WorkerCoordinatorError(
                    "WORKER_LEASE_GRACE_PERIOD_ACTIVE",
                    "lease grace period has not elapsed",
                )

            if decision == RecoveryDecision.OUTCOME_UNKNOWN:
                lease_status = "INDETERMINATE"
                task_status = "RECONCILIATION_REQUIRED"
                result_status = "RECONCILIATION_REQUIRED"
            elif decision == RecoveryDecision.DEAD_LETTER:
                lease_status = "DEAD_LETTER"
                task_status = "DEAD_LETTER"
                result_status = "DEAD_LETTER"
            elif decision == RecoveryDecision.RETRY_SAFE:
                attempts = connection.execute(
                    select(func.count())
                    .select_from(worker_leases)
                    .where(
                        worker_leases.c.organization_id == lease["organization_id"],
                        worker_leases.c.task_id == lease["task_id"],
                    )
                ).scalar_one()
                lease_status = "EXPIRED"
                if attempts >= config.max_attempts:
                    task_status = "DEAD_LETTER"
                    result_status = "DEAD_LETTER"
                else:
                    task_status = "READY"
                    result_status = "REQUEUED"
            else:
                raise ValueError("unsupported recovery decision")
            connection.execute(
                update(worker_leases)
                .where(worker_leases.c.lease_id == lease_id)
                .values(status=lease_status)
            )
            connection.execute(
                update(tasks)
                .where(tasks.c.task_id == lease["task_id"])
                .values(status=task_status)
            )
            return ReconciliationResult(lease_id, lease["task_id"], result_status)

    def commit_result(
        self,
        principal: WorkerPrincipal,
        *,
        lease_id: str,
        fencing_token: int,
        idempotency_key: str,
        result: Mapping[str, Any],
        now: datetime,
    ) -> WorkerResultAck:
        if (
            not idempotency_key.strip()
            or len(idempotency_key) > 256
            or not isinstance(result, Mapping)
        ):
            raise ValueError("idempotency_key and result object are required")
        status = result.get("status")
        if status not in ("SUCCEEDED", "FAILED", "CANCELLED"):
            raise WorkerCoordinatorError(
                "WORKER_RESULT_INVALID", "result status is invalid"
            )
        event_payload = {
            "kind": "worker.result",
            "task_id": None,
            "worker_id": principal.worker_id,
            "lease_id": lease_id,
            "fencing_token": fencing_token,
            "result": dict(result),
        }
        failure: WorkerCoordinatorError | None = None
        ack: WorkerResultAck | None = None
        try:
            with self._engine.begin() as connection:
                initial = (
                    connection.execute(
                        select(worker_leases).where(
                            worker_leases.c.lease_id == lease_id,
                            worker_leases.c.organization_id
                            == principal.organization_id,
                        )
                    )
                    .mappings()
                    .first()
                )
                if initial is None or initial["worker_id"] != principal.worker_id:
                    raise WorkerCoordinatorError(
                        "WORKER_LEASE_NOT_FOUND", "lease was not found"
                    )
                if (
                    initial["fencing_token"] != fencing_token
                    or initial["idempotency_key"] != idempotency_key
                ):
                    raise WorkerCoordinatorError(
                        "WORKER_LEASE_FENCED", "lease token is stale"
                    )
                event_payload["task_id"] = initial["task_id"]
                existing = (
                    connection.execute(
                        select(events).where(
                            events.c.organization_id == principal.organization_id,
                            events.c.idempotency_key == idempotency_key,
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing is not None:
                    previous = dict(existing["payload"])
                    if previous != event_payload:
                        failure = WorkerCoordinatorError(
                            "IDEMPOTENCY_CONFLICT",
                            "idempotency key is already bound to another result",
                        )
                    else:
                        ack = WorkerResultAck(
                            existing["event_id"],
                            event_payload["task_id"],
                            lease_id,
                            idempotency_key,
                            True,
                        )
                else:
                    initial = (
                        connection.execute(
                            select(worker_leases).where(
                                worker_leases.c.lease_id == lease_id,
                                worker_leases.c.organization_id
                                == principal.organization_id,
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if initial is None or initial["worker_id"] != principal.worker_id:
                        raise WorkerCoordinatorError(
                            "WORKER_LEASE_NOT_FOUND", "lease was not found"
                        )
                    task = (
                        connection.execute(
                            select(tasks)
                            .where(
                                tasks.c.task_id == initial["task_id"],
                                tasks.c.organization_id == principal.organization_id,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one()
                    )
                    existing_after_lock = (
                        connection.execute(
                            select(events).where(
                                events.c.organization_id == principal.organization_id,
                                events.c.idempotency_key == idempotency_key,
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if existing_after_lock is not None:
                        if dict(existing_after_lock["payload"]) != event_payload:
                            raise WorkerCoordinatorError(
                                "IDEMPOTENCY_CONFLICT",
                                "idempotency key is already bound to another result",
                            )
                        return WorkerResultAck(
                            existing_after_lock["event_id"],
                            task["task_id"],
                            lease_id,
                            idempotency_key,
                            True,
                        )
                    lease = (
                        connection.execute(
                            select(worker_leases)
                            .where(worker_leases.c.lease_id == lease_id)
                            .with_for_update()
                        )
                        .mappings()
                        .one()
                    )
                    event_payload["task_id"] = lease["task_id"]
                    if (
                        lease["worker_id"] != principal.worker_id
                        or lease["fencing_token"] != fencing_token
                        or lease["idempotency_key"] != idempotency_key
                        or lease["status"] != "ACTIVE"
                    ):
                        raise WorkerCoordinatorError(
                            "WORKER_LEASE_FENCED", "lease token is stale"
                        )
                    if _utc(lease["expires_at"]) <= _utc(now):
                        raise WorkerCoordinatorError(
                            "WORKER_LEASE_EXPIRED", "lease has expired"
                        )
                    worker_status = connection.execute(
                        select(workers.c.status).where(
                            workers.c.worker_id == principal.worker_id,
                            workers.c.organization_id == principal.organization_id,
                        )
                    ).scalar_one_or_none()
                    if (
                        worker_status in {"REVOKED", "DISABLED"}
                        or worker_status is None
                    ):
                        raise WorkerCoordinatorError(
                            "WORKER_NOT_ACTIVE", "Worker cannot submit results"
                        )

                    event_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"shuliu-worker-result:{principal.organization_id}:{idempotency_key}",
                        )
                    )
                    connection.execute(
                        insert(events).values(
                            event_id=event_id,
                            organization_id=principal.organization_id,
                            idempotency_key=idempotency_key,
                            payload=event_payload,
                            received_at=now,
                        )
                    )
                    terminal_lease_status = {
                        "SUCCEEDED": "COMPLETED",
                        "FAILED": "FAILED",
                        "CANCELLED": "CANCELLED",
                    }[status]
                    connection.execute(
                        update(worker_leases)
                        .where(worker_leases.c.lease_id == lease_id)
                        .values(status=terminal_lease_status)
                    )
                    connection.execute(
                        update(tasks)
                        .where(tasks.c.task_id == task["task_id"])
                        .values(status=status)
                    )
                    ack = WorkerResultAck(
                        event_id, task["task_id"], lease_id, idempotency_key, False
                    )
        except IntegrityError:
            return self._ack_existing_result(
                principal,
                lease_id=lease_id,
                idempotency_key=idempotency_key,
                expected=event_payload,
            )
        if failure is not None:
            raise failure
        assert ack is not None
        return ack

    def _ack_existing_result(
        self,
        principal: WorkerPrincipal,
        *,
        lease_id: str,
        idempotency_key: str,
        expected: Mapping[str, Any],
    ) -> WorkerResultAck:
        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    select(events).where(
                        events.c.organization_id == principal.organization_id,
                        events.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .first()
            )
            if existing is None or dict(existing["payload"]) != dict(expected):
                raise WorkerCoordinatorError(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency key is already bound to another result",
                )
            payload = existing["payload"]
            return WorkerResultAck(
                existing["event_id"],
                payload["task_id"],
                lease_id,
                idempotency_key,
                True,
            )


def _task_action_digest(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise WorkerCoordinatorError(
            "WORKER_TASK_ACTION_INVALID", "Task does not contain an authorized Tool action"
        )
    call = payload.get("tool_call")
    if (
        not isinstance(call, Mapping)
        or set(call) != {"tool_id", "arguments"}
        or not isinstance(call.get("tool_id"), str)
        or not call["tool_id"].strip()
        or not isinstance(call.get("arguments"), Mapping)
    ):
        raise WorkerCoordinatorError(
            "WORKER_TASK_ACTION_INVALID", "Task does not contain an authorized Tool action"
        )
    try:
        canonical = json.dumps(
            {"tool_id": call["tool_id"], "arguments": dict(call["arguments"])},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise WorkerCoordinatorError(
            "WORKER_TASK_ACTION_INVALID", "Task Tool action is invalid"
        ) from None
    return sha256(canonical.encode("utf-8")).hexdigest()
