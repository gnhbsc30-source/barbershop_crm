"""Tenant-scoped owner appointment reads and commands."""

from datetime import datetime
import hashlib
import json

from app.db import repositories
from app.db.database import read_snapshot
from app.services.appointments import AppointmentBookingError, ResolvedAppointmentItem
from app.services.customer_booking import item_summary


class OwnerAppointmentError(AppointmentBookingError):
    """Owner-specific domain rejection."""


def appointment_etag(business_id, revision, detail):
    encoded = json.dumps([business_id, revision, detail], sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode()
    return '"' + hashlib.sha256(encoded).hexdigest() + '"'


class OwnerAppointments:
    def __init__(self, connection, principal):
        self.connection = connection
        self.business_id = principal.business_id

    def calendar(self, *, start, end, staff_id, limit, after):
        with read_snapshot(self.connection):
            business = repositories.get_booking_business(self.connection, self.business_id)
            if business is None:
                raise OwnerAppointmentError("UNAUTHENTICATED")
            if staff_id is not None and repositories.get_staff(self.connection, self.business_id, staff_id) is None:
                raise OwnerAppointmentError("STAFF_NOT_FOUND")
            rows = repositories.get_owner_appointment_rows(
                self.connection, self.business_id, start=start, end=end, staff_id=staff_id,
                limit=limit + 1, after=after)
            appointments = self._with_items(rows[:limit])
            next_key = (appointments[-1]["start_datetime"], appointments[-1]["appointment_id"]) if len(rows) > limit else None
            return business["timezone"], appointments, next_key

    def detail(self, appointment_id):
        with read_snapshot(self.connection):
            return self._detail(appointment_id)

    def _detail(self, appointment_id):
        rows = repositories.get_owner_appointment_rows(
            self.connection, self.business_id, appointment_id=appointment_id)
        if not rows:
            raise OwnerAppointmentError("APPOINTMENT_NOT_FOUND")
        detail, = self._with_items(rows)
        revision = detail.pop("updated_at")
        parse_revision(revision)
        return detail, appointment_etag(self.business_id, revision, detail)

    def _with_items(self, rows):
        grouped = {row["appointment_id"]: [] for row in rows}
        for item in repositories.get_owner_appointment_items(self.connection, self.business_id, list(grouped)):
            grouped[item["appointment_id"]].append(ResolvedAppointmentItem(
                item["service_id"], item["service_name"], item["duration_minutes"], item["price"]))
        return [{**row, **item_summary(grouped[row["appointment_id"]])} for row in rows]


def parse_revision(value):
    from app.services.booking_confirmation import BookingConfigurationError

    try:
        revision = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise BookingConfigurationError() from None
    if revision.tzinfo is not None:
        raise BookingConfigurationError()
    return revision
