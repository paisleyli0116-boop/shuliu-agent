from datetime import datetime, timedelta, timezone

import pytest

from app.identity.provider import (
    AuthenticationError,
    FakeIdentityProvider,
    IdentityProvider,
    Session,
)


def test_fake_provider_satisfies_the_replaceable_identity_contract():
    provider = FakeIdentityProvider(
        credential_users={"test-token": "user-1"},
        session_ttl=timedelta(minutes=5),
    )

    assert isinstance(provider, IdentityProvider)
    session = provider.authenticate({"token": "test-token"})

    assert isinstance(session, Session)
    assert session.user_id == "user-1"
    assert session.expires_at > session.issued_at
    assert session.expires_at - session.issued_at == timedelta(minutes=5)


def test_provider_contract_rejects_an_incompatible_implementation():
    class IncompatibleProvider:
        def issue_session(self, credentials):
            return {"user": credentials.get("token")}

    assert not isinstance(IncompatibleProvider(), IdentityProvider)


def test_fake_provider_rejects_unknown_credentials():
    provider = FakeIdentityProvider(
        credential_users={"test-token": "user-1"},
        session_ttl=timedelta(minutes=5),
    )

    with pytest.raises(AuthenticationError):
        provider.authenticate({"token": "unknown-token"})


def test_session_requires_a_positive_lifetime_and_timezone_aware_timestamps():
    issued_at = datetime(2026, 10, 17, tzinfo=timezone.utc)

    with pytest.raises(ValueError):
        Session(
            session_id="session-1",
            user_id="user-1",
            issued_at=issued_at,
            expires_at=issued_at,
        )
