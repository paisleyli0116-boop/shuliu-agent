from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shuliu_runtime.security.secret_store import SecretStore
from shuliu_runtime.tools.broker import IdentityPrincipal
from shuliu_runtime.worker.credentials import (
    WorkerAuthenticationError,
    WorkerRegistrationOutcomeUnknown,
)
from shuliu_runtime.worker.registration import WorkerRegistrationClient

logger = logging.getLogger("shuliu.worker.service")

WORKER_STATE_FILENAME = "worker-registration.json"

API_URL_ENVIRONMENT_KEY = "SHULIU_CLOUD_API_URL"
WSS_URL_ENVIRONMENT_KEY = "SHULIU_CLOUD_WSS_URL"
VERSION_ENVIRONMENT_KEY = "SHULIU_WORKER_VERSION"
CAPABILITIES_ENVIRONMENT_KEY = "SHULIU_WORKER_CAPABILITIES"
IDLE_POLL_ENVIRONMENT_KEY = "SHULIU_WORKER_IDLE_POLL_SECONDS"
REGISTRATION_TOKEN_ENVIRONMENT_KEY = "SHULIU_WORKER_REGISTRATION_TOKEN"
TOOL_PERMISSIONS_ENVIRONMENT_KEY = "SHULIU_WORKER_TOOL_PERMISSIONS"

# The Cloud serves the Worker socket at a fixed route under its own prefix, so the
# WSS endpoint is derived from the API endpoint unless a reverse proxy moves it.
WORKER_SOCKET_PATH = "/v1/workers/ws"

_MAX_IDLE_POLL_SECONDS = 60.0
_MIN_IDLE_POLL_SECONDS = 0.01


class WorkerServiceError(RuntimeError):
    """The Worker service cannot start from the configuration it was given."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WorkerServiceConfiguration:
    api_base_url: str
    websocket_url: str
    data_directory: Path
    worker_version: str
    capabilities: tuple[str, ...] = ()
    # Deployment-granted Tool permissions for this Worker. They are declared by
    # the operator, never by a Cloud task, so a task cannot widen its own rights.
    tool_permissions: tuple[str, ...] = ()
    idle_poll_seconds: float = 1.0

    @property
    def state_path(self) -> Path:
        return self.data_directory / WORKER_STATE_FILENAME


@dataclass(frozen=True)
class WorkerSession:
    worker_id: str
    session_credential: str
    expires_at: datetime


def configuration_from_environment(
    environment: Mapping[str, str], *, data_directory: str | Path
) -> WorkerServiceConfiguration:
    """Read the Worker deployment configuration; nothing is defaulted silently.

    Both endpoints must be declared and encrypted: a Worker talks to the Cloud
    over HTTPS to enroll and WSS to run, so an `http://` or `ws://` value is a
    misconfiguration, not a fallback opportunity.
    """
    api_base_url = _value(environment.get(API_URL_ENVIRONMENT_KEY))
    declared_websocket_url = _value(environment.get(WSS_URL_ENVIRONMENT_KEY))
    worker_version = _value(environment.get(VERSION_ENVIRONMENT_KEY))
    if api_base_url is None or worker_version is None:
        raise WorkerServiceError("WORKER_CONFIGURATION_INCOMPLETE")
    if not api_base_url.startswith("https://"):
        raise WorkerServiceError("WORKER_ENDPOINT_INSECURE")
    websocket_url = declared_websocket_url or _derive_socket_url(api_base_url)
    if not websocket_url.startswith("wss://"):
        raise WorkerServiceError("WORKER_ENDPOINT_INSECURE")

    capabilities = _capabilities(environment.get(CAPABILITIES_ENVIRONMENT_KEY))
    tool_permissions = _capabilities(
        environment.get(TOOL_PERMISSIONS_ENVIRONMENT_KEY)
    )
    idle_poll_seconds = _idle_poll(environment.get(IDLE_POLL_ENVIRONMENT_KEY))
    return WorkerServiceConfiguration(
        api_base_url=api_base_url.rstrip("/"),
        websocket_url=websocket_url,
        data_directory=Path(data_directory),
        worker_version=worker_version,
        capabilities=capabilities,
        tool_permissions=tool_permissions,
        idle_poll_seconds=idle_poll_seconds,
    )


def _derive_socket_url(api_base_url: str) -> str:
    host = api_base_url.removeprefix("https://").rstrip("/")
    return f"wss://{host}{WORKER_SOCKET_PATH}"


def open_worker_session(
    configuration: WorkerServiceConfiguration,
    *,
    cloud,
    secret_store: SecretStore,
    environment: Mapping[str, str],
) -> WorkerSession:
    """Enroll once with the one-time token, then authenticate with the device key.

    Only the worker identity and device key reference are persisted; the session
    credential stays in memory for the lifetime of the process.
    """
    state = _read_state(configuration.state_path)
    if state is not None:
        grant = cloud.renew_session(
            worker_id=state["worker_id"],
            device_key_ref=state["device_key_ref"],
            secret_store=secret_store,
        )
        return WorkerSession(
            worker_id=state["worker_id"],
            session_credential=grant.session_credential,
            expires_at=grant.expires_at,
        )

    token = _value(environment.get(REGISTRATION_TOKEN_ENVIRONMENT_KEY))
    if token is None:
        raise WorkerServiceError("WORKER_REGISTRATION_TOKEN_REQUIRED")
    client = WorkerRegistrationClient(
        cloud=cloud,
        secret_store=secret_store,
        worker_version=configuration.worker_version,
        capabilities=configuration.capabilities,
    )
    try:
        result = client.register(token)
    except WorkerRegistrationOutcomeUnknown:
        # The one-time token may already be consumed; never retry it blindly.
        raise WorkerServiceError("WORKER_REGISTRATION_OUTCOME_UNKNOWN") from None
    except WorkerAuthenticationError as error:
        raise WorkerServiceError(error.code) from None

    _write_state(configuration.state_path, result.as_safe_dict())
    logger.info("event=worker_registered code=WORKER_REGISTERED")
    return WorkerSession(
        worker_id=result.worker_id,
        session_credential=result.session_credential,
        expires_at=result.expires_at,
    )


def _read_state(path: Path) -> dict[str, str] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise WorkerServiceError("WORKER_STATE_UNREADABLE") from error
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkerServiceError("WORKER_STATE_INVALID") from error
    if not isinstance(payload, dict):
        raise WorkerServiceError("WORKER_STATE_INVALID")
    worker_id = payload.get("worker_id")
    device_key_ref = payload.get("device_key_ref")
    if not _non_empty(worker_id) or not _non_empty(device_key_ref):
        raise WorkerServiceError("WORKER_STATE_INVALID")
    return {"worker_id": worker_id, "device_key_ref": device_key_ref}


def _write_state(path: Path, state: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(state), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _capabilities(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, str):
        raise WorkerServiceError("WORKER_CAPABILITIES_INVALID")
    capabilities = tuple(part.strip() for part in raw.split(",") if part.strip())
    if len(set(capabilities)) != len(capabilities):
        raise WorkerServiceError("WORKER_CAPABILITIES_INVALID")
    return capabilities


def _idle_poll(raw: object) -> float:
    if raw is None:
        return 1.0
    if not isinstance(raw, str) or not raw.strip():
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        raise WorkerServiceError("WORKER_IDLE_POLL_INVALID") from None
    if not _MIN_IDLE_POLL_SECONDS <= value <= _MAX_IDLE_POLL_SECONDS:
        raise WorkerServiceError("WORKER_IDLE_POLL_INVALID")
    return value


def _value(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    return trimmed or None


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


class WorkerIdentityResolver:
    """Turn the authenticated Worker session into a Tool Broker principal.

    The Worker's actor carries only the session-verified subject and
    organization. Any extra field — most importantly `permissions` — makes the
    actor untrusted, so a task cannot widen what the Worker is allowed to run.
    Permissions themselves come from the deployment configuration.
    """

    _ALLOWED_FIELDS = frozenset({"subject_id", "organization_id"})

    def __init__(self, permissions: tuple[str, ...]) -> None:
        self._permissions = tuple(permissions)

    def resolve(self, actor: Mapping[str, object]) -> IdentityPrincipal | None:
        if not isinstance(actor, Mapping):
            return None
        if set(actor) - self._ALLOWED_FIELDS:
            return None
        subject_id = actor.get("subject_id")
        organization_id = actor.get("organization_id")
        if not _non_empty(subject_id) or not _non_empty(organization_id):
            return None
        return IdentityPrincipal(
            subject_id=subject_id,
            organization_id=organization_id,
            permissions=self._permissions,
        )


def build_worker_tool_broker(
    *,
    database,
    registry,
    policy,
    permissions: tuple[str, ...],
):
    """Build the single gated execution path a Worker task may use."""
    from shuliu_runtime.events.models import event_to_dict
    from shuliu_runtime.persistence.outbox import OutboxRepository
    from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
    from shuliu_runtime.tools.broker import ToolBroker

    outbox = OutboxRepository(database)

    def emit_tool_event(event) -> None:
        outbox.enqueue(
            event.event_id,
            f"worker-tool-event:{event.event_id}",
            event_to_dict(event),
        )

    return ToolBroker(
        registry=registry,
        identity=WorkerIdentityResolver(permissions),
        policy=policy,
        # A Worker never approves its own work; approvals are decided in the Cloud.
        approvals=None,
        event_sink=emit_tool_event,
        idempotency_store=SQLiteIdempotencyStore(database),
    )


def main(environment: Mapping[str, str] | None = None) -> int:
    """Run the Worker service until the process is stopped."""
    import logging
    import os
    import sys
    from threading import Event

    from shuliu_runtime.persistence.sqlite import SQLiteDatabase
    from shuliu_runtime.policy.engine import PolicyEngine
    from shuliu_runtime.production.policy import (
        PolicyBundleError,
        PolicyBundleUnavailable,
        load_policy_bundle,
    )
    from shuliu_runtime.production.tools import compose_tool_registries
    from shuliu_runtime.security.os_secret_store import OSSecretStore
    from shuliu_runtime.tools.registry import ToolRegistry
    from shuliu_runtime.worker.host import WorkerHost
    from shuliu_runtime.worker.transport import HttpWorkerRegistrationCloud

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    environ = dict(environment if environment is not None else os.environ)
    data_directory = _data_directory(environ)

    try:
        configuration = configuration_from_environment(
            environ, data_directory=data_directory
        )
    except WorkerServiceError as error:
        logger.error("event=worker_service_failed code=%s", error.code)
        return 1

    database = SQLiteDatabase(data_directory / "foundation.sqlite")
    database.migrate()

    bundle_path = _value(environ.get("SHULIU_POLICY_BUNDLE"))
    if bundle_path is None:
        logger.error("event=worker_service_failed code=WORKER_POLICY_BUNDLE_UNAVAILABLE")
        return 1
    try:
        policy: PolicyEngine = load_policy_bundle(bundle_path)
    except (PolicyBundleUnavailable, PolicyBundleError) as error:
        logger.error("event=worker_service_failed code=%s", error.code)
        return 1

    broker = build_worker_tool_broker(
        database=database,
        registry=compose_tool_registries([]),
        policy=policy,
        permissions=configuration.tool_permissions,
    )
    secret_store = OSSecretStore()
    cloud = HttpWorkerRegistrationCloud(api_base_url=configuration.api_base_url)
    try:
        session = open_worker_session(
            configuration,
            cloud=cloud,
            secret_store=secret_store,
            environment=environ,
        )
        connection = cloud.connect(
            websocket_url=configuration.websocket_url,
            worker_id=session.worker_id,
            session_credential=session.session_credential,
        )
        WorkerHost.with_tool_broker(
            connection=connection,
            tool_broker=broker,
            idle_poll_seconds=configuration.idle_poll_seconds,
        ).run(stop_event=Event())
    except WorkerServiceError as error:
        logger.error("event=worker_service_failed code=%s", error.code)
        return 1
    except WorkerAuthenticationError as error:
        logger.error("event=worker_service_failed code=%s", error.code)
        return 1
    return 0


def _data_directory(environment: Mapping[str, str]) -> Path:
    configured = _value(environment.get("SHULIU_DATA_DIR"))
    if configured is not None:
        return Path(configured)
    local_app_data = _value(environment.get("LOCALAPPDATA"))
    if local_app_data is not None:
        return Path(local_app_data) / "ShuLiu Worker"
    return Path.home() / ".local" / "share" / "shuliu-worker"


if __name__ == "__main__":
    raise SystemExit(main())
