"""HTTP identity composition. Every SQLite lifetime stays in one sync worker."""

from contextlib import asynccontextmanager, closing
from pathlib import Path
from types import MappingProxyType

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from app.core.auth_rate_limit import AuthenticationRateLimiter
from app.core.auth_sessions import InMemorySessionStore, SessionContext, SessionError
from app.core.config import settings
from app.core.local_security import validate_local_startup
from app.db import database
from app.services.authentication import (
    AuthenticationError, PrincipalRole, authenticate_access_code_with_fingerprint,
    resolve_identity_with_fingerprint,
)


SESSION_COOKIE = "barbershop_dev_session"


class AuthHTTPError(Exception):
    def __init__(self, status: int, code: str, *, clear_cookie: bool = False, headers=None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.clear_cookie = clear_cookie
        self.headers = headers


@asynccontextmanager
async def authentication_lifespan(app):
    path = Path(database.DATABASE_PATH).absolute()
    identities = await run_in_threadpool(validate_local_startup, path, settings.environment)
    app.state.auth_database_path = path.resolve()
    app.state.auth_identities = MappingProxyType(identities)
    app.state.auth_sessions = InMemorySessionStore()
    app.state.auth_limiter = AuthenticationRateLimiter()
    try:
        yield
    finally:
        for attribute in ("auth_database_path", "auth_identities", "auth_sessions", "auth_limiter"):
            delattr(app.state, attribute)


def login_verified_session(app, access_code: str, previous_token: str | None):
    try:
        with closing(database.open_development_database(app.state.auth_database_path)) as connection:
            principal, fingerprint = authenticate_access_code_with_fingerprint(
                connection, access_code, app.state.auth_identities,
            )
    except AuthenticationError:
        # A failed login must not clear or invalidate an existing valid session.
        raise AuthHTTPError(401, "INVALID_CREDENTIALS") from None
    try:
        return app.state.auth_sessions.create(principal, fingerprint, previous_token=previous_token)
    except SessionError as error:
        raise AuthHTTPError(503, error.code) from None


def require_authenticated_session(request: Request) -> SessionContext:
    token = request.cookies.get(SESSION_COOKIE)
    store = request.app.state.auth_sessions
    try:
        context = store.resolve(token)
        matches = [(selector, reference) for selector, reference in request.app.state.auth_identities.items()
                   if reference.business_id == context.principal.business_id
                   and f"{reference.account_kind}:{reference.account_id}" == context.principal.subject]
        if len(matches) != 1:
            raise AuthenticationError()
        selector, reference = matches[0]
        with closing(database.open_development_database(request.app.state.auth_database_path)) as connection:
            principal, fingerprint = resolve_identity_with_fingerprint(
                connection, reference, expected_selector=selector,
            )
        return store.validate_identity(token, principal, fingerprint)
    except (AuthenticationError, SessionError):
        store.invalidate(token)
        raise AuthHTTPError(401, "UNAUTHENTICATED", clear_cookie=True) from None


def require_csrf_session(
    request: Request, context: SessionContext = Depends(require_authenticated_session),
) -> SessionContext:
    try:
        tokens = request.headers.getlist("x-csrf-token")
        request.app.state.auth_sessions.verify_csrf(
            request.cookies.get(SESSION_COOKIE), tokens[0] if len(tokens) == 1 else None,
        )
    except SessionError as error:
        if error.code == "CSRF_FAILED":
            raise AuthHTTPError(403, "CSRF_FAILED") from None
        raise AuthHTTPError(401, "UNAUTHENTICATED", clear_cookie=True) from None
    return context


def require_owner(context: SessionContext = Depends(require_authenticated_session)) -> SessionContext:
    if context.principal.role is not PrincipalRole.OWNER:
        raise AuthHTTPError(403, "FORBIDDEN")
    return context


def require_customer(context: SessionContext = Depends(require_authenticated_session)) -> SessionContext:
    if context.principal.role is not PrincipalRole.CUSTOMER:
        raise AuthHTTPError(403, "FORBIDDEN")
    return context


def ensure_business_access(context: SessionContext, business_id: int) -> None:
    if type(business_id) is not int or business_id != context.principal.business_id:
        raise AuthHTTPError(404, "RESOURCE_NOT_FOUND")
