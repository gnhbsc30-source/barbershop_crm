"""Exact, loopback-only HTTP guards for the supported one-process dev server."""

from ipaddress import ip_address
import os
from pathlib import Path

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from app.dev_data import DevelopmentDataError, validate_development_data


DEVELOPMENT_ORIGIN = "http://127.0.0.1:8000"
DEVELOPMENT_HOST = "127.0.0.1:8000"
LOGIN_PATH = "/api/v1/auth/dev-session"


def validate_local_startup(path: Path, environment: str):
    if environment != "development":
        raise DevelopmentDataError("DEVELOPMENT_ONLY")
    if os.environ.get("WEB_CONCURRENCY", "1") != "1":
        raise DevelopmentDataError("SINGLE_WORKER_REQUIRED")
    return validate_development_data(path, environment=environment)


class LoopbackSecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        headers = Headers(scope=scope)
        code, status = None, 403
        try:
            loopback = ip_address(scope["client"][0]).is_loopback
        except (ValueError, KeyError, TypeError, IndexError):
            loopback = False
        if headers.getlist("host") != [DEVELOPMENT_HOST]:
            code, status = "INVALID_HOST", 400
        elif not loopback or scope.get("scheme") != "http":
            code = "LOCAL_ACCESS_ONLY"
        elif headers.getlist("origin") and headers.getlist("origin") != [DEVELOPMENT_ORIGIN]:
            code = "ORIGIN_REJECTED"
        elif scope["method"] in ("POST", "PUT", "PATCH", "DELETE"):
            if headers.getlist("origin") != [DEVELOPMENT_ORIGIN]:
                code = "ORIGIN_REJECTED"
            elif scope["path"] == LOGIN_PATH:
                if headers.getlist("x-csrf-protection") != ["1"]:
                    code = "CSRF_FAILED"
                elif len(headers.getlist("content-type")) != 1 or headers["content-type"].split(";")[0].strip().lower() != "application/json":
                    code, status = "UNSUPPORTED_MEDIA_TYPE", 415
        if code is not None:
            await JSONResponse({"error": {"code": code}}, status_code=status)(scope, receive, secure_send)
            return
        # Do not let an unexpected exception containing credential material reach
        # the server's default traceback logger. These endpoints do not stream.
        started = False

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await secure_send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception:
            if started:
                raise RuntimeError("AUTH_SERVICE_UNAVAILABLE") from None
            await JSONResponse({"error": {"code": "AUTH_SERVICE_UNAVAILABLE"}}, status_code=503)(scope, receive, secure_send)
