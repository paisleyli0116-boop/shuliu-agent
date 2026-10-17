from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable
from uuid import uuid4


class AuthenticationError(RuntimeError):
    """Raised when a provider cannot authenticate supplied credentials."""


@dataclass(frozen=True)
class Session:
    session_id: str
    user_id: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not self.user_id.strip():
            raise ValueError("session_id and user_id are required")
        if (
            self.issued_at.tzinfo is None
            or self.issued_at.utcoffset() is None
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("session timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("session expiry must be after issue time")


@runtime_checkable
class IdentityProvider(Protocol):
    def authenticate(self, credentials: Mapping[str, str]) -> Session: ...


class FakeIdentityProvider:
    """Deterministic test provider; production credentials belong to a real provider."""

    def __init__(
        self,
        *,
        credential_users: Mapping[str, str],
        session_ttl: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be positive")
        if any(not credential or not user_id for credential, user_id in credential_users.items()):
            raise ValueError("fake credentials and user IDs must be non-empty")
        self._credential_users = dict(credential_users)
        self._session_ttl = session_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def authenticate(self, credentials: Mapping[str, str]) -> Session:
        if not isinstance(credentials, Mapping):
            raise AuthenticationError("credentials are invalid")
        token = credentials.get("token")
        if not isinstance(token, str) or not token:
            raise AuthenticationError("credentials are invalid")
        user_id = self._credential_users.get(token)
        if user_id is None:
            raise AuthenticationError("credentials are invalid")

        issued_at = self._clock()
        if issued_at.tzinfo is None or issued_at.utcoffset() is None:
            raise AuthenticationError("identity provider clock must be timezone-aware")
        return Session(
            session_id=str(uuid4()),
            user_id=user_id,
            issued_at=issued_at,
            expires_at=issued_at + self._session_ttl,
        )
