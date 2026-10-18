from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.identity.provider import AuthenticationError, IdentityProvider
from fastapi import Header, HTTPException


def current_user_dependency(
    provider: IdentityProvider | None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Callable[..., str]:
    def current_user(authorization: str | None = Header(default=None)) -> str:
        if provider is None:
            raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED"})
        scheme, separator, token = (authorization or "").partition(" ")
        if not separator or scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED"})
        try:
            session = provider.authenticate({"token": token})
        except AuthenticationError:
            raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_INVALID"}) from None
        now = (clock or (lambda: datetime.now(UTC)))()
        if now >= session.expires_at:
            raise HTTPException(status_code=401, detail={"code": "SESSION_EXPIRED"})
        return session.user_id

    return current_user
