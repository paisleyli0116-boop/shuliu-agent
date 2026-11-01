from __future__ import annotations

from dataclasses import dataclass

import pytest
from app.knowledge.embedding import (
    EmbeddingConfigurationError,
    EmbeddingProfile,
    GatewayEmbeddingAdapter,
    load_embedding_profile,
)


@dataclass(frozen=True)
class Result:
    output: object
    resolved_provider: str = "embed-provider"
    resolved_model: str = "embed-model-v1"


class Gateway:
    def __init__(self, result: Result | None = None) -> None:
        self.result = result or Result([[0.1, 0.2, 0.3]])
        self.calls: list[tuple[str, dict[str, object], str | None]] = []

    def complete_for_provider(self, provider_id: str, request, *, model: str | None = None):
        self.calls.append((provider_id, dict(request), model))
        return self.result


def _profile():
    return EmbeddingProfile(
        profile_id="knowledge-default-v1",
        provider_id="embed-provider",
        model="embed-model-v1",
        dimensions=3,
        privacy_levels=frozenset({"INTERNAL", "CONFIDENTIAL"}),
        index_version=1,
    )


def test_embedding_adapter_uses_configured_profile_through_gateway():
    gateway = Gateway()
    adapter = GatewayEmbeddingAdapter(gateway, _profile())

    vectors = adapter.embed(
        ["safe test text"],
        privacy="INTERNAL",
        budget_ref="budget://org-a/knowledge",
        trace_id="trace-embedding-1",
        organization_id="org-a",
        subject_id="user-a",
        permissions=("foundation.model.invoke", "knowledge.read"),
    )

    assert vectors == ((0.1, 0.2, 0.3),)
    assert len(gateway.calls) == 1
    provider_id, request, expected_model = gateway.calls[0]
    assert provider_id == "embed-provider"
    assert expected_model == "embed-model-v1"
    assert request["capability"] == "embedding"
    assert request["input"] == ["safe test text"]
    assert request["budget_ref"] == "budget://org-a/knowledge"
    assert request["dimensions"] == 3


def test_embedding_profile_rejects_invalid_dimensions_and_privacy():
    with pytest.raises(ValueError, match="profile"):
        EmbeddingProfile(
            profile_id="knowledge-default-v1",
            provider_id="embed-provider",
            model="embed-model-v1",
            dimensions=0,
            privacy_levels=frozenset({"INTERNAL"}),
            index_version=1,
        )
    with pytest.raises(ValueError, match="profile"):
        EmbeddingProfile(
            profile_id="knowledge-default-v1",
            provider_id="embed-provider",
            model="https://provider.example/api-key",
            dimensions=3,
            privacy_levels=frozenset({"INTERNAL"}),
            index_version=1,
        )
    with pytest.raises(ValueError, match="profile"):
        EmbeddingProfile(
            profile_id="knowledge-default-v1",
            provider_id="embed-provider",
            model="embed-model-v1",
            dimensions=3,
            privacy_levels=frozenset(),
            index_version=1,
        )


def test_embedding_profile_loader_is_explicit_and_rejects_secret_fields():
    profile = load_embedding_profile(
        '{"profile_id":"knowledge-default-v1","provider_id":"embed-provider",'
        '"model":"embed-model-v1","dimensions":3,"privacy_levels":["INTERNAL"],'
        '"index_version":1}'
    )
    assert profile.profile_id == "knowledge-default-v1"
    with pytest.raises(EmbeddingConfigurationError):
        load_embedding_profile(
            '{"profile_id":"knowledge-default-v1","provider_id":"embed-provider",'
            '"model":"embed-model-v1","dimensions":3,"privacy_levels":["INTERNAL"],'
            '"index_version":1,"api_key":"must-not-be-accepted"}'
        )


def test_embedding_adapter_fails_closed_on_privacy_or_profile_mismatch():
    gateway = Gateway(Result([[0.1, 0.2, 0.3]], resolved_model="wrong-model"))
    adapter = GatewayEmbeddingAdapter(gateway, _profile())
    arguments = {
        "budget_ref": "budget://org-a/knowledge",
        "trace_id": "trace-embedding-2",
        "organization_id": "org-a",
        "subject_id": "user-a",
        "permissions": ("foundation.model.invoke",),
    }

    with pytest.raises(EmbeddingConfigurationError):
        adapter.embed(["text"], privacy="LOCAL_ONLY", **arguments)
    assert gateway.calls == []

    with pytest.raises(EmbeddingConfigurationError):
        adapter.embed(["text"], privacy="INTERNAL", **arguments)
    assert len(gateway.calls) == 1


def test_embedding_adapter_rejects_wrong_vector_count_and_dimension():
    arguments = {
        "privacy": "INTERNAL",
        "budget_ref": "budget://org-a/knowledge",
        "trace_id": "trace-embedding-3",
        "organization_id": "org-a",
        "subject_id": "user-a",
        "permissions": ("foundation.model.invoke",),
    }
    for output in ([[0.1, 0.2]], [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]):
        adapter = GatewayEmbeddingAdapter(Gateway(Result(output)), _profile())
        with pytest.raises(EmbeddingConfigurationError):
            adapter.embed(["one"], **arguments)
