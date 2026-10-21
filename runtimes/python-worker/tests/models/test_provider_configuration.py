import json

import pytest
import shuliu_runtime.models as models


def _load_profiles(raw: str):
    loader = getattr(models, "load_provider_profiles", None)
    assert callable(loader), "Task 28 must expose the validated provider profile loader"
    return loader(raw)


def _profile(**overrides):
    value = {
        "provider_id": "openai-compatible",
        "model": "openai/gpt-4.1-mini",
        "capabilities": ["fast_chat"],
        "priority": 10,
        "is_local": False,
        "secret_ref": "secret://os/providers/openai-compatible/credential",
        "enabled": True,
    }
    value.update(overrides)
    return value


def test_loads_provider_profile_with_secret_reference_but_no_plaintext_credential():
    profile, = _load_profiles(json.dumps([_profile()]))

    assert profile.provider_id == "openai-compatible"
    assert profile.model == "openai/gpt-4.1-mini"
    assert profile.capabilities == frozenset({"fast_chat"})
    assert profile.secret_ref == "secret://os/providers/openai-compatible/credential"
    assert profile.enabled is True
    assert not hasattr(profile, "api_key")
    assert "api_key" not in repr(profile).lower()


@pytest.mark.parametrize(
    "profiles",
    [
        [_profile(), _profile(model="openai/gpt-4.1")],
        [_profile(capabilities=["unknown_capability"])],
        [_profile(api_key="sk-do-not-accept")],
        [_profile(secret_ref=None)],
    ],
)
def test_rejects_duplicate_unknown_secret_bearing_or_missing_secret_profiles(profiles):
    with pytest.raises(ValueError):
        _load_profiles(json.dumps(profiles))


@pytest.mark.parametrize(
    "provider_id",
    [
        "AIzaSyD1234567890abcdefghijklmnoPQRSTUV",
        "sk-credential-marker-12345",
        "xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "glpat-abcdefghijklmnopqrst",
        "provider-token:credential-marker",
        "provider-sk-credential-marker",
    ],
)
def test_rejects_secret_shaped_provider_ids(provider_id):
    with pytest.raises(ValueError):
        _load_profiles(json.dumps([_profile(provider_id=provider_id)]))


def test_rejects_non_json_or_non_array_provider_configuration():
    for raw in ("not-json", json.dumps({"providers": [_profile()]})):
        with pytest.raises(ValueError):
            _load_profiles(raw)


@pytest.mark.parametrize(
    "model",
    [
        "sk-credential-marker-12345",
        "AIzaSyD1234567890abcdefghijklmnoPQRSTUV",
        "ya29.A0ARrdaM91Klq0f4tW5xY7zB8cD3eF6gH9",
        "b8D4F2a9C1e7G6h3J5k0M2n8P4r1S9v5",
        "xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "provider/eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "api_key:credential-marker",
        "provider/api_key:credential-marker",
        "glpat-abcdefghijklmnopqrst",
        "provider/sk-credential-marker",
        "provider/secret://os/key",
        "provider/https://example.test/path",
        "https://provider.example/model",
        "secret://os/provider/key",
        "model-name\ncredential-marker",
    ],
)
def test_rejects_secret_shaped_or_non_identifier_model_values(model):
    with pytest.raises(ValueError):
        _load_profiles(json.dumps([_profile(model=model)]))


def test_accepts_custom_model_with_dot_separated_words():
    profile, = _load_profiles(json.dumps([_profile(model="provider/custom-model.version-one.preview-model")]))

    assert profile.model == "provider/custom-model.version-one.preview-model"
