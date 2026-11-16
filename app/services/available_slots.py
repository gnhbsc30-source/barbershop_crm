"""List bookable candidates from the existing staff-day free windows."""

from datetime import timedelta

from app.db import repositories
from app.db.database import read_snapshot
from app.services.appointments import AppointmentBookingError
from app.services.availability import AvailabilityEngine, ScheduleBudgetExceeded, generate_candidate_start_times
from app.services.booking_confirmation import BookingConfigurationError


MAX_BLOCKERS = 4096
MAX_SLOTS = 2880


def available_slots(customer, day, staff_id, service_ids):
    connection = customer.connection
    business_id = customer.business_id
    with read_snapshot(connection):
        settings = repositories.get_business_settings(connection, business_id) or {}
        interval = settings.get("booking_interval_minutes", 30)
        if type(interval) is not int or not 1 <= interval <= 1440:
            raise BookingConfigurationError()
        try:
            now, earliest, latest = customer.appointments.booking_policy_bounds(business_id, settings=settings)
        except (TypeError, ValueError, OverflowError):
            raise BookingConfigurationError() from None
        if day < now.date():
            raise AppointmentBookingError("PAST_DATETIME")
        if day > latest.date():
            raise AppointmentBookingError("BOOKING_WINDOW_EXCEEDED")
        try:
            items = customer.appointments.resolve_staff_services(
                business_id=business_id, staff_id=staff_id, service_ids=service_ids,
            )
        except AppointmentBookingError as error:
            if error.code == "SERVICE_NOT_SUPPORTED":
                raise AppointmentBookingError("STAFF_NOT_ELIGIBLE") from None
            raise
        if any(type(item.duration_minutes) is not int or item.duration_minutes <= 0 for item in items):
            raise BookingConfigurationError()
        duration = sum(item.duration_minutes for item in items)
        starts = set()
        if duration <= 1440 and earliest.date() <= day:
            try:
                windows = AvailabilityEngine(connection).get_staff_free_windows(
                    business_id, staff_id, day, row_budget=MAX_BLOCKERS,
                )
            except ScheduleBudgetExceeded:
                raise BookingConfigurationError() from None
            for first, last in windows:
                starts.update(
                    start for start in generate_candidate_start_times(max(first, earliest), last, duration, interval)
                    if start <= latest and (start + timedelta(minutes=duration)).date() == day
                )
                if len(starts) > MAX_SLOTS:
                    raise BookingConfigurationError()
        return {
            "date": day.isoformat(), "business_timezone": "Asia/Jerusalem", "staff_id": staff_id,
            "service_ids": service_ids, "slot_interval_minutes": interval,
            "slots": [{"start_datetime": start.isoformat(),
                       "end_datetime": (start + timedelta(minutes=duration)).isoformat()}
                      for start in sorted(starts)],
        }
