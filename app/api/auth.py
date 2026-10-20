"""Three development-auth endpoints, with bounded/redacted request handling."""

from datetime import datetime
import json
import re

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.api.auth_dependencies import (
    AuthHTTPError, SESSION_COOKIE, login_verified_session,
    require_authenticated_session, require_csrf_session,
)
from app.core.auth_rate_limit import AuthenticationRateLimitError
from app.core.auth_sessions import SessionContext
from app.services.authentication import PrincipalRole


router = APIRouter(prefix="/api/v1/auth")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    access_code: str = Field(min_length=60, max_length=60, repr=False)


class PrincipalResponse(BaseModel):
    subject: str
    role: PrincipalRole
    business_id: int
    customer_id: int | None
    owner_user_id: int | None


class SessionResponse(BaseModel):
    principal: PrincipalResponse
    expires_at: datetime
    csrf_token: str = Field(repr=False)


def session_response(context: SessionContext) -> SessionResponse:
    return SessionResponse(
        principal=PrincipalResponse(**vars(context.principal)),
        expires_at=context.expires_at, csrf_token=context.csrf_token,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=False)


async def auth_error_handler(request: Request, error: AuthHTTPError):
    response = JSONResponse({"error": {"code": error.code}}, status_code=error.status, headers=error.headers)
    response.headers["Cache-Control"] = "no-store"
    if error.clear_cookie:
        clear_session_cookie(response)
    return response


async def validation_error_handler(request: Request, error: RequestValidationError):
    return JSONResponse({"error": {"code": "INVALID_REQUEST"}}, status_code=422, headers={"Cache-Control": "no-store"})


async def http_error_handler(request: Request, error: HTTPException):
    return JSONResponse({"error": {"code": "HTTP_ERROR"}}, status_code=error.status_code, headers={"Cache-Control": "no-store"})


async def unexpected_error_handler(request: Request, error: Exception):
    # Do not serialize exception text, validation inputs, request bodies or tokens.
    return JSONResponse({"error": {"code": "AUTH_SERVICE_UNAVAILABLE"}}, status_code=503, headers={"Cache-Control": "no-store"})


AUTH_EXCEPTION_HANDLERS = {
    AuthHTTPError: auth_error_handler,
    RequestValidationError: validation_error_handler,
    HTTPException: http_error_handler,
    Exception: unexpected_error_handler,
}


async def read_login_request(request: Request) -> LoginRequest:
    raw = bytearray()
    oversized = False
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 4096:
            oversized = True
            break
        raw.extend(chunk)
    try:
        data = json.loads(raw) if not oversized else None
    except (ValueError, UnicodeError, RecursionError):
        data = None
    code = data.get("access_code") if isinstance(data, dict) else None
    candidate = code.split(".", 1)[0] if isinstance(code, str) else ""
    selector = candidate if re.fullmatch(r"[A-Za-z0-9_-]{16}", candidate) else "_malformed"
    # Count malformed and unknown selectors before validation or Argon2 work.
    try:
        request.app.state.auth_limiter.check_and_record(selector)
    except AuthenticationRateLimitError as error:
        raise AuthHTTPError(429, error.code, headers={"Retry-After": str(error.retry_after_seconds)}) from None
    if oversized:
        raise AuthHTTPError(413, "REQUEST_TOO_LARGE")
    if request.query_params:
        raise AuthHTTPError(422, "INVALID_REQUEST")
    try:
        return LoginRequest.model_validate(data)
    except ValidationError:
        raise AuthHTTPError(422, "INVALID_REQUEST") from None


@router.post("/dev-session", response_model=SessionResponse)
async def create_session(request: Request, response: Response):
    data = await read_login_request(request)
    issued = await run_in_threadpool(
        login_verified_session, request.app, data.access_code, request.cookies.get(SESSION_COOKIE),
    )
    response.set_cookie(
        SESSION_COOKIE, issued.token, max_age=3600, path="/",
        httponly=True, samesite="strict", secure=False,
    )
    return session_response(issued.context)


@router.get("/me", response_model=SessionResponse)
def current_identity(context: SessionContext = Depends(require_authenticated_session)):
    return session_response(context)


@router.delete("/session", status_code=204)
def delete_session(request: Request, context: SessionContext = Depends(require_csrf_session)):
    request.app.state.auth_sessions.invalidate(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response
