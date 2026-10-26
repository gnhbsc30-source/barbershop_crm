"""Customer application orchestration, reusing deterministic domain services."""

from app.db import repositories
from app.services.appointments import AppointmentBookingError, AppointmentService
from app.services.availability import AvailabilityEngine
from app.services.booking_confirmation import BookingConfirmation, format_cents, real_to_cents


def item_summary(items):
    terms = BookingConfirmation.from_items(items)
    return {
        "items": [{"service_id": item.service_id, "service_name": item.service_name,
                   "duration_minutes": item.duration_minutes, "price": format_cents(term.price_cents)}
                  for item, term in zip(items, terms.items)],
        "total_duration_minutes": terms.total_duration_minutes,
        "total_price": format_cents(terms.total_price_cents), "currency": "ILS",
    }


class CustomerBooking:
    def __init__(self, connection, principal):
        self.connection = connection
        self.principal = principal
        self.business_id = principal.business_id
        self.appointments = AppointmentService(connection)

    def services(self):
        return {"services": [
            {"service_id": row["id"], "name": row["name"], "description": row["description"],
             "default_duration_minutes": row["default_duration_minutes"],
             "default_price": format_cents(real_to_cents(row["default_price"]))}
            for row in repositories.list_active_services(self.connection, self.business_id)
        ]}

    def validate_services(self, service_ids):
        if len(repositories.get_services_by_ids(self.connection, self.business_id, service_ids)) != len(service_ids):
            raise AppointmentBookingError("SERVICE_NOT_FOUND")

    def staff(self, service_ids):
        self.validate_services(service_ids)
        staff = AvailabilityEngine(self.connection).get_eligible_staff(self.business_id, service_ids)
        return {"service_ids": service_ids, "staff": [
            {"staff_id": row["id"], "staff_name": row["name"], **item_summary(
                self.appointments.resolve_staff_services(business_id=self.business_id, staff_id=row["id"], service_ids=service_ids))}
            for row in staff
        ]}
