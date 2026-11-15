"""Owner HTTP boundary for calendar and appointment management."""

from base64 import urlsafe_b64encode, b64decode
from contextlib import closing
from datetime import date, datetime
import json
import re
import sqlite3

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from app.api.auth_dependencies import AuthHTTPError, require_authenticated_session, require_owner
from app.api.booking_dependencies import positive_query, query_keys
from app.api.customer_booking_models import StrictModel
from app.api.owner_appointment_models import AppointmentDetail, CalendarAppointment
from app.core import booking_clock
from app.db import database, repositories
from app.services.appointments import AppointmentBookingError
from app.services.booking_confirmation import BookingConfigurationError
from app.services.owner_appointments import OwnerAppointments


router = APIRouter(prefix="/api/v1/owner")


class CalendarPage(StrictModel):
    business_timezone: str
    appointments: list[CalendarAppointment]
    next_cursor: str | None


def execute_owner(request, context, operation, *, scheduling=False):
    try:
        fresh = require_authenticated_session(request)
        require_owner(fresh)
        if fresh != context:
            raise AuthHTTPError(401, "UNAUTHENTICATED", clear_cookie=True)
        with closing(database.open_development_database(request.app.state.auth_database_path)) as connection:
            if scheduling:
                business = repositories.get_booking_business(connection, fresh.principal.business_id)
                if not business or not booking_clock.scheduling_clock_ready(business["timezone"]):
                    raise AuthHTTPError(503, "BOOKING_CLOCK_CONFIGURATION_INVALID")
            return operation(OwnerAppointments(connection, fresh.principal))
    except AuthHTTPError:
        raise
    except BookingConfigurationError:
        raise AuthHTTPError(503, "BOOKING_CONFIGURATION_INVALID") from None
    except AppointmentBookingError as error:
        statuses = {
            "UNAUTHENTICATED": 401,
            "APPOINTMENT_NOT_FOUND": 404, "CUSTOMER_NOT_FOUND": 404,
            "STAFF_NOT_FOUND": 404, "STAFF_INACTIVE": 404, "SERVICE_NOT_FOUND": 404,
            "APPOINTMENT_UNAVAILABLE": 409, "BOOKING_QUOTE_CHANGED": 409,
            "CUSTOMER_PHONE_ALREADY_EXISTS": 409, "APPOINTMENT_NOT_EDITABLE": 409,
            "BOOKED_SERVICE_INACTIVE": 409, "APPOINTMENT_VERSION_MISMATCH": 412,
            "BOOKING_OPTIONS_LIMIT_EXCEEDED": 503,
        }
        raise AuthHTTPError(statuses.get(error.code, 422), error.code) from None
    except sqlite3.OperationalError as error:
        busy = getattr(error, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
        if busy:
            raise AuthHTTPError(503, "BOOKING_SERVICE_UNAVAILABLE", headers={"Retry-After": "1"}) from None
        raise AuthHTTPError(500, "INTERNAL_ERROR") from None
    except Exception:
        raise AuthHTTPError(500, "INTERNAL_ERROR") from None


def calendar_date(value):
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value or ""):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise AuthHTTPError(422, "INVALID_REQUEST")


def encode_cursor(scope, key):
    return urlsafe_b64encode(json.dumps([scope, key], separators=(",", ":")).encode()).decode().rstrip("=")


def cursor_key(value, scope):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", value):
        raise AuthHTTPError(422, "INVALID_REQUEST")
    try:
        bound, key = json.loads(b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        start, identifier = key
        parsed = datetime.fromisoformat(start)
        if (bound != scope or parsed.tzinfo is not None or type(identifier) is not int
                or not 0 < identifier <= 2**63 - 1 or encode_cursor(scope, (start, identifier)) != value):
            raise ValueError
    except (TypeError, ValueError, RecursionError):
        raise AuthHTTPError(422, "INVALID_REQUEST") from None
    return start, identifier


def detail_response(detail, etag, *, status=200):
    value = AppointmentDetail.model_validate(detail).model_dump(mode="json")
    headers = {"ETag": etag}
    if status == 201:
        headers["Location"] = f"/api/v1/owner/appointments/{value['appointment_id']}"
    return JSONResponse(value, status_code=status, headers=headers)


@router.get("/appointments", response_model=CalendarPage)
def calendar(request: Request, context=Depends(require_owner)):
    query_keys(request, allowed=("from_date", "to_date_exclusive", "staff_id", "limit", "cursor"))
    query = request.query_params
    first = calendar_date(query.get("from_date"))
    last = calendar_date(query.get("to_date_exclusive"))
    limit = positive_query(query.get("limit", "100"))
    if not 1 <= (last - first).days <= 31 or limit > 200:
        raise AuthHTTPError(422, "INVALID_REQUEST")
    staff = positive_query(query["staff_id"]) if "staff_id" in query else None
    start, end = first.isoformat() + "T00:00:00", last.isoformat() + "T00:00:00"
    scope = [context.principal.business_id, start, end, staff]
    after = cursor_key(query["cursor"], scope) if "cursor" in query else None

    def read(service):
        timezone, appointments, next_key = service.calendar(
            start=start, end=end, staff_id=staff, limit=limit, after=after)
        return CalendarPage(business_timezone=timezone, appointments=appointments,
                            next_cursor=encode_cursor(scope, next_key) if next_key else None)

    return execute_owner(request, context, read)


@router.get("/appointments/{appointment_id}", response_model=AppointmentDetail)
def detail(appointment_id: str, request: Request, context=Depends(require_owner)):
    query_keys(request)
    identifier = positive_query(appointment_id)
    return execute_owner(request, context, lambda service: detail_response(*service.detail(identifier)))
