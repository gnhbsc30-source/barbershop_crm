"""Customer HTTP security/serialization boundary; SQLite stays in one worker."""

from contextlib import closing
import sqlite3
import json

from fastapi import Depends, Request

from app.api.auth_dependencies import AuthHTTPError, SESSION_COOKIE, require_authenticated_session, require_customer, require_csrf_session
from app.core.auth_sessions import SessionError
from app.core.booking_search_ownership import SearchAdmissionError, SearchOwner
from app.core import booking_clock
from app.db import database
from app.services.appointments import AppointmentBookingError
from app.services.alternative_selection import AlternativeSelectionError
from app.services.booking_confirmation import BookingConfigurationError
from app.services.customer_booking import CustomerBooking
from pydantic import ValidationError


def query_keys(request: Request, allowed=(), repeated=()):
    if any(key not in allowed or (key not in repeated and len(request.query_params.getlist(key)) != 1)
           for key in request.query_params):
        raise AuthHTTPError(422, "INVALID_REQUEST")


def positive_query(value: str) -> int:
    if not 1 <= len(value) <= 19 or not value.isascii() or not value.isdecimal() or not 0 < int(value) <= 2**63 - 1:
        raise AuthHTTPError(422, "INVALID_REQUEST")
    return int(value)


def require_customer_post(context=Depends(require_csrf_session)):
    return require_customer(context)


async def read_customer_body(request, model):
    query_keys(request)
    types = request.headers.getlist("content-type")
    if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
        raise AuthHTTPError(415, "UNSUPPORTED_MEDIA_TYPE")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 16384:
            raise AuthHTTPError(413, "REQUEST_TOO_LARGE")
        raw.extend(chunk)
    try:
        data = json.loads(raw)
        return model.model_validate(data)
    except ValidationError as error:
        messages = {entry["msg"] for entry in error.errors(include_input=False)}
        code = "ANY_STAFF_DISCOVERY_UNSUPPORTED" if any("ANY_STAFF_DISCOVERY_UNSUPPORTED" in msg for msg in messages) else (
            "INVALID_DATETIME" if any("INVALID_DATETIME" in msg for msg in messages) else "INVALID_REQUEST")
        raise AuthHTTPError(422, code) from None
    except (ValueError, UnicodeError, RecursionError):
        raise AuthHTTPError(422, "INVALID_REQUEST") from None


def search_owner(request, context):
    principal = context.principal
    binding = request.app.state.auth_sessions.binding_for(request.cookies.get(SESSION_COOKIE))
    return SearchOwner(principal.subject, principal.business_id, principal.customer_id, binding)


def execute_booking(request, context, operation, scheduling=False, response_model=None):
    try:
        fresh = require_authenticated_session(request)
        require_customer(fresh)
        if fresh != context:
            raise AuthHTTPError(401, "UNAUTHENTICATED", clear_cookie=True)
        with closing(database.open_development_database(request.app.state.auth_database_path)) as connection:
            if scheduling:
                from app.db.repositories import get_booking_business
                business = get_booking_business(connection, fresh.principal.business_id)
                if not business or not booking_clock.scheduling_clock_ready(business["timezone"]):
                    raise AuthHTTPError(503, "BOOKING_CLOCK_CONFIGURATION_INVALID")
            value = operation(CustomerBooking(connection, fresh.principal))
            if response_model is not None:
                try:
                    value = response_model.model_validate(value).model_dump(mode="json")
                except Exception:
                    if isinstance(value, dict) and isinstance(value.get("alternatives"), dict):
                        search_id = value["alternatives"].get("search_id")
                        if search_id:
                            request.app.state.booking_searches.discard_created(search_id)
                    raise BookingConfigurationError() from None
            return value
    except AuthHTTPError:
        raise
    except SessionError:
        raise AuthHTTPError(401, "UNAUTHENTICATED", clear_cookie=True) from None
    except SearchAdmissionError as error:
        raise AuthHTTPError(error.status, error.code, headers={"Retry-After": "1"}) from None
    except BookingConfigurationError:
        raise AuthHTTPError(503, "BOOKING_CONFIGURATION_INVALID") from None
    except (AppointmentBookingError, AlternativeSelectionError) as error:
        code = error.code
        if code == "CUSTOMER_NOT_FOUND":
            raise AuthHTTPError(401, "UNAUTHENTICATED", clear_cookie=True) from None
        if code in ("STAFF_NOT_FOUND", "STAFF_INACTIVE", "SERVICE_NOT_FOUND", "ALTERNATIVE_OPTION_NOT_FOUND", "APPOINTMENT_NOT_FOUND"):
            status = 404
        elif code == "ALTERNATIVE_SEARCH_EXPIRED":
            status = 410
        elif code in ("APPOINTMENT_UNAVAILABLE", "SELECTED_ALTERNATIVE_NO_LONGER_AVAILABLE", "ALTERNATIVE_SELECTION_IN_PROGRESS", "BOOKING_QUOTE_CHANGED"):
            status = 409
        else:
            status = 422
        raise AuthHTTPError(status, code) from None
    except sqlite3.OperationalError as error:
        error_code = getattr(error, "sqlite_errorcode", 0)
        busy = error_code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
        raise AuthHTTPError(503 if busy else 500, "BOOKING_SERVICE_UNAVAILABLE" if busy else "INTERNAL_ERROR",
                            headers={"Retry-After": "1"} if busy else None) from None
    except Exception:
        raise AuthHTTPError(500, "INTERNAL_ERROR") from None
