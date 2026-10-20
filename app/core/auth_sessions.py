"""Single-process development sessions; callers must revalidate DB identity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from threading import RLock
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from app.services.authentication import Principal


class SessionError(ValueError):
    def __init__(self, code: str = "UNAUTHENTICATED"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SessionContext:
    principal: Principal
    credential_fingerprint: str = field(repr=False)
    csrf_token: str = field(repr=False)
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class IssuedSession:
    token: str = field(repr=False)
    context: SessionContext


class InMemorySessionStore:
    """One-hour absolute sessions, bounded to 1024 live records by default."""

    def __init__(
        self,
        now_provider: Callable[[], datetime] | None = None,
        *,
        max_sessions: int = 1024,
    ):
        if type(max_sessions) is not int or max_sessions <= 0:
            raise ValueError("INVALID_SESSION_CAPACITY")
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._max_sessions = max_sessions
        self._sessions: dict[str, SessionContext] = {}
        self._lock = RLock()

    @staticmethod
    def _digest(token: str) -> str:
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            raise SessionError()
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def _time(self) -> datetime:
        now = self._now()
        if now.utcoffset() is None:
            raise ValueError("SESSION_CLOCK_MUST_BE_AWARE")
        return now

    def _cleanup(self, now: datetime) -> None:
        for digest, context in list(self._sessions.items()):
            if now >= context.expires_at:
                del self._sessions[digest]

    def _lookup(self, token: str) -> SessionContext:
        self._cleanup(self._time())
        context = self._sessions.get(self._digest(token))
        if context is None:
            raise SessionError()
        return context

    def create(
        self,
        principal: Principal,
        credential_fingerprint: str,
        *,
        previous_token: str | None = None,
    ) -> IssuedSession:
        """Call only after successful credential verification, never on failure."""
        if (
            not isinstance(credential_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", credential_fingerprint) is None
        ):
            raise ValueError("INVALID_CREDENTIAL_FINGERPRINT")
        with self._lock:
            now = self._time()
            self._cleanup(now)
            previous_digest = None
            if previous_token is not None:
                try:
                    previous_digest = self._digest(previous_token)
                except SessionError:
                    pass
            replacing = previous_digest in self._sessions
            if len(self._sessions) - int(replacing) >= self._max_sessions:
                raise SessionError("SESSION_CAPACITY_REACHED")
            token = secrets.token_urlsafe(32)
            while self._digest(token) in self._sessions:
                token = secrets.token_urlsafe(32)
            context = SessionContext(
                principal, credential_fingerprint, secrets.token_urlsafe(32),
                now, now + timedelta(hours=1),
            )
            if replacing:
                del self._sessions[previous_digest]
            self._sessions[self._digest(token)] = context
            return IssuedSession(token, context)

    def resolve(self, token: str) -> SessionContext:
        """Check token/TTL only; this is not fresh database authorization."""
        with self._lock:
            return self._lookup(token)

    def validate_identity(
        self, token: str, principal: Principal, credential_fingerprint: str,
    ) -> SessionContext:
        """Compare with freshly revalidated identity supplied by the caller."""
        with self._lock:
            context = self._lookup(token)
            if (
                context.principal != principal
                or not isinstance(credential_fingerprint, str)
                or not credential_fingerprint.isascii()
                or not hmac.compare_digest(context.credential_fingerprint, credential_fingerprint)
            ):
                del self._sessions[self._digest(token)]
                raise SessionError()
            return context

    def verify_csrf(self, token: str, csrf_token: str) -> None:
        with self._lock:
            context = self._lookup(token)
            if (
                not isinstance(csrf_token, str)
                or not csrf_token.isascii()
                or not hmac.compare_digest(context.csrf_token, csrf_token)
            ):
                raise SessionError("CSRF_FAILED")

    def invalidate(self, token: str) -> None:
        with self._lock:
            self._cleanup(self._time())
            try:
                digest = self._digest(token)
            except SessionError:
                return
            self._sessions.pop(digest, None)
