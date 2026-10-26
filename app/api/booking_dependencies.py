"""Customer HTTP security/serialization boundary; SQLite stays in one worker."""

from contextlib import closing
import sqlite3

from fastapi import Depends, Request

from app.api.auth_dependencies import AuthHTTPError, require_authenticated_session, require_customer
from app.db import database
from app.services.appointments import AppointmentBookingError
from app.services.alternative_selection import AlternativeSelectionError
from app.services.booking_confirmation import BookingConfigurationError
from app.services.customer_booking import CustomerBooking


def query_keys(request: Request, allowed=(), repeated=()):
    if any(key not in allowed or (key not in repeated and len(request.query_params.getlist(key)) != 1)
           for key in request.query_params):
        raise AuthHTTPError(422, "INVALID_REQUEST")


def positive_query(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) <= 2**63 - 1:
        raise AuthHTTPError(422, "INVALID_REQUEST")
    return int(value)


def execute_booking(request, context, operation):
    try:
        fresh = require_authenticated_session(request)
        require_customer(fresh)
        if fresh != context:
            raise AuthHTTPError(401, "UNAUTHENTICATED", clear_cookie=True)
        with closing(database.open_development_database(request.app.state.auth_database_path)) as connection:
            return operation(CustomerBooking(connection, fresh.principal))
    except AuthHTTPError:
        raise
    except BookingConfigurationError:
        raise AuthHTTPError(503, "BOOKING_CONFIGURATION_INVALID") from None
    except (AppointmentBookingError, AlternativeSelectionError) as error:
        code = error.code
        if code in ("STAFF_NOT_FOUND", "STAFF_INACTIVE", "SERVICE_NOT_FOUND", "ALTERNATIVE_OPTION_NOT_FOUND"):
            status = 404
        elif code == "ALTERNATIVE_SEARCH_EXPIRED":
            status = 410
        elif code in ("APPOINTMENT_UNAVAILABLE", "SELECTED_ALTERNATIVE_NO_LONGER_AVAILABLE", "ALTERNATIVE_SELECTION_IN_PROGRESS", "BOOKING_QUOTE_CHANGED"):
            status = 409
        else:
            status = 422
        raise AuthHTTPError(status, code) from None
    except sqlite3.OperationalError as error:
        busy = getattr(error, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
        raise AuthHTTPError(503 if busy else 500, "BOOKING_SERVICE_UNAVAILABLE" if busy else "INTERNAL_ERROR",
                            headers={"Retry-After": "1"} if busy else None) from None
    except Exception:
        raise AuthHTTPError(500, "INTERNAL_ERROR") from None
