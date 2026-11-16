from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from shuliu_runtime.worker.credentials import (
    RegistrationGrant,
    WorkerSessionGrant,
)
from shuliu_runtime.worker.service import (
    WORKER_STATE_FILENAME,
    WorkerServiceConfiguration,
    WorkerServiceError,
    configuration_from_environment,
    open_worker_session,
)

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, reference) -> str:
        key = getattr(reference, "name", reference)
        if key not in self.values:
            raise KeyError(key)
        return self.values[key]

    def put(self, reference, value) -> None:
        self.values[getattr(reference, "name", reference)] = value

    def delete(self, reference) -> None:
        self.values.pop(getattr(reference, "name", reference), None)


class FakeCloud:
    def __init__(self) -> None:
        self.registered: list[dict[str, object]] = []
        self.renewed: list[dict[str, object]] = []

    def register(
        self,
        registration_token,
        device_public_key,
        device_signature,
        *,
        device_key_algorithm,
        worker_version,
        capabilities,
    ) -> RegistrationGrant:
        self.registered.append(
            {
                "token": registration_token,
                "algorithm": device_key_algorithm,
                "version": worker_version,
                "capabilities": capabilities,
            }
        )
        return RegistrationGrant(
            worker_id="worker-1",
            session_credential="session-secret-value",
            expires_at=NOW + timedelta(hours=1),
        )

    def renew_session(self, *, worker_id, device_key_ref, secret_store) -> WorkerSessionGrant:
        self.renewed.append({"worker_id": worker_id, "device_key_ref": device_key_ref})
        return WorkerSessionGrant(
            session_credential="renewed-session-secret", expires_at=NOW + timedelta(hours=1)
        )


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "SHULIU_CLOUD_API_URL": "https://cloud.example.test",
        "SHULIU_CLOUD_WSS_URL": "wss://cloud.example.test/v1/workers/socket",
        "SHULIU_WORKER_VERSION": "0.1.0",
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def test_a_complete_environment_describes_the_service(tmp_path) -> None:
    configuration = configuration_from_environment(environment(), data_directory=tmp_path)

    assert configuration.api_base_url == "https://cloud.example.test"
    assert configuration.websocket_url == "wss://cloud.example.test/v1/workers/socket"
    assert configuration.worker_version == "0.1.0"
    assert configuration.capabilities == ()


@pytest.mark.parametrize("missing", ["SHULIU_CLOUD_API_URL", "SHULIU_WORKER_VERSION"])
def test_a_missing_endpoint_keeps_the_worker_unconfigured(tmp_path, missing: str) -> None:
    with pytest.raises(WorkerServiceError) as error:
        configuration_from_environment(environment(**{missing: None}), data_directory=tmp_path)

    assert error.value.code == "WORKER_CONFIGURATION_INCOMPLETE"


def test_the_socket_endpoint_is_derived_from_the_cloud_route(tmp_path) -> None:
    # Cloud 在 /v1/workers/ws 提供 Worker socket，默认不需要单独配置。
    configuration = configuration_from_environment(
        environment(SHULIU_CLOUD_WSS_URL=None), data_directory=tmp_path
    )

    assert configuration.websocket_url == "wss://cloud.example.test/v1/workers/ws"


def test_an_explicit_socket_endpoint_wins_over_the_derived_one(tmp_path) -> None:
    configuration = configuration_from_environment(
        environment(SHULIU_CLOUD_WSS_URL="wss://edge.example.test/worker/socket"),
        data_directory=tmp_path,
    )

    assert configuration.websocket_url == "wss://edge.example.test/worker/socket"


def test_tool_permissions_are_declared_by_the_deployment(tmp_path) -> None:
    configuration = configuration_from_environment(
        environment(SHULIU_WORKER_TOOL_PERMISSIONS="foundation.tool.invoke"),
        data_directory=tmp_path,
    )

    assert configuration.tool_permissions == ("foundation.tool.invoke",)


def test_a_worker_without_tool_permissions_is_allowed_to_run_nothing(tmp_path) -> None:
    configuration = configuration_from_environment(environment(), data_directory=tmp_path)

    assert configuration.tool_permissions == ()


def test_plaintext_endpoints_are_refused(tmp_path) -> None:
    with pytest.raises(WorkerServiceError) as error:
        configuration_from_environment(
            environment(SHULIU_CLOUD_API_URL="http://cloud.example.test"),
            data_directory=tmp_path,
        )

    assert error.value.code == "WORKER_ENDPOINT_INSECURE"


def test_an_insecure_websocket_endpoint_is_refused(tmp_path) -> None:
    with pytest.raises(WorkerServiceError) as error:
        configuration_from_environment(
            environment(SHULIU_CLOUD_WSS_URL="ws://cloud.example.test/socket"),
            data_directory=tmp_path,
        )

    assert error.value.code == "WORKER_ENDPOINT_INSECURE"


def test_capabilities_are_parsed_and_deduplicated(tmp_path) -> None:
    configuration = configuration_from_environment(
        environment(SHULIU_WORKER_CAPABILITIES="tool.dispatch, knowledge.search"),
        data_directory=tmp_path,
    )

    assert configuration.capabilities == ("tool.dispatch", "knowledge.search")


def test_duplicate_capabilities_are_refused(tmp_path) -> None:
    with pytest.raises(WorkerServiceError) as error:
        configuration_from_environment(
            environment(SHULIU_WORKER_CAPABILITIES="tool.dispatch,tool.dispatch"),
            data_directory=tmp_path,
        )

    assert error.value.code == "WORKER_CAPABILITIES_INVALID"


def test_the_first_run_registers_with_the_one_time_token(tmp_path) -> None:
    cloud = FakeCloud()
    secrets = FakeSecrets()
    configuration = configuration_from_environment(
        environment(SHULIU_WORKER_REGISTRATION_TOKEN="one-time-token"),
        data_directory=tmp_path,
    )

    session = open_worker_session(
        configuration, cloud=cloud, secret_store=secrets, environment=environment(
            SHULIU_WORKER_REGISTRATION_TOKEN="one-time-token"
        )
    )

    assert session.worker_id == "worker-1"
    assert cloud.registered[0]["token"] == "one-time-token"
    assert cloud.renewed == []


def test_the_registration_state_never_stores_the_session_credential(tmp_path) -> None:
    secrets = FakeSecrets()
    configuration = configuration_from_environment(
        environment(), data_directory=tmp_path
    )

    open_worker_session(
        configuration,
        cloud=FakeCloud(),
        secret_store=secrets,
        environment=environment(SHULIU_WORKER_REGISTRATION_TOKEN="one-time-token"),
    )

    state = json.loads((tmp_path / WORKER_STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["worker_id"] == "worker-1"
    assert "session-secret-value" not in (
        tmp_path / WORKER_STATE_FILENAME
    ).read_text(encoding="utf-8")


def test_a_registered_worker_renews_its_session_instead_of_registering(tmp_path) -> None:
    secrets = FakeSecrets()
    configuration = configuration_from_environment(
        environment(), data_directory=tmp_path
    )
    open_worker_session(
        configuration,
        cloud=FakeCloud(),
        secret_store=secrets,
        environment=environment(SHULIU_WORKER_REGISTRATION_TOKEN="one-time-token"),
    )
    cloud = FakeCloud()

    session = open_worker_session(
        configuration, cloud=cloud, secret_store=secrets, environment=environment()
    )

    assert session.worker_id == "worker-1"
    assert session.session_credential == "renewed-session-secret"
    assert cloud.registered == []
    assert cloud.renewed[0]["worker_id"] == "worker-1"


def test_an_unregistered_worker_without_a_token_is_refused(tmp_path) -> None:
    configuration = configuration_from_environment(
        environment(), data_directory=tmp_path
    )

    with pytest.raises(WorkerServiceError) as error:
        open_worker_session(
            configuration,
            cloud=FakeCloud(),
            secret_store=FakeSecrets(),
            environment=environment(),
        )

    assert error.value.code == "WORKER_REGISTRATION_TOKEN_REQUIRED"


def test_an_idle_poll_interval_outside_the_supported_range_is_refused(tmp_path) -> None:
    with pytest.raises(WorkerServiceError) as error:
        configuration_from_environment(
            environment(SHULIU_WORKER_IDLE_POLL_SECONDS="0"), data_directory=tmp_path
        )

    assert error.value.code == "WORKER_IDLE_POLL_INVALID"


def test_the_configuration_exposes_a_path_independent_of_the_source_mapping(
    tmp_path,
) -> None:
    configuration = configuration_from_environment(environment(), data_directory=tmp_path)

    assert isinstance(configuration, WorkerServiceConfiguration)
    assert configuration.state_path == tmp_path / WORKER_STATE_FILENAME


class TestWorkerIdentity:
    def test_the_session_actor_becomes_a_principal_with_declared_permissions(
        self,
    ) -> None:
        from shuliu_runtime.worker.service import WorkerIdentityResolver

        principal = WorkerIdentityResolver(("foundation.tool.invoke",)).resolve(
            {"subject_id": "worker-1", "organization_id": "org-foundation"}
        )

        assert principal is not None
        assert principal.subject_id == "worker-1"
        assert principal.permissions == ("foundation.tool.invoke",)

    def test_an_actor_carrying_its_own_permissions_is_refused(self) -> None:
        # 任务载荷若能自带 permissions，就等于可以自己提权。
        from shuliu_runtime.worker.service import WorkerIdentityResolver

        assert (
            WorkerIdentityResolver(("foundation.tool.invoke",)).resolve(
                {
                    "subject_id": "worker-1",
                    "organization_id": "org-foundation",
                    "permissions": ["foundation.approval.decide"],
                }
            )
            is None
        )

    def test_an_incomplete_actor_is_refused(self) -> None:
        from shuliu_runtime.worker.service import WorkerIdentityResolver

        resolver = WorkerIdentityResolver(())
        assert resolver.resolve({}) is None
        assert resolver.resolve({"subject_id": "worker-1"}) is None
        assert resolver.resolve({"subject_id": " ", "organization_id": "org"}) is None
