"""Customer application orchestration, reusing deterministic domain services."""

from app.db import repositories
from app.services.appointments import AppointmentBookingError, AppointmentService
from app.services.availability import AvailabilityEngine
from app.services.booking_confirmation import BookingConfirmation, format_cents, real_to_cents
from app.services.alternatives import AlternativeDiscoveryService
from datetime import datetime
from dataclasses import asdict


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

    def search(self, data, owner, contexts, ownership):
        with ownership.computation(owner, data.include_alternatives) as ticket:
            self.validate_services(data.service_ids)
            start = datetime.fromisoformat(data.start_datetime)
            self.appointments.validate_booking_policy(business_id=self.business_id, requested_start=start)
            selection = data.staff_selection.model_dump()
            if selection["type"] == "SPECIFIC":
                staff = repositories.get_staff(self.connection, self.business_id, selection["staff_id"])
                if staff is None:
                    raise AppointmentBookingError("STAFF_NOT_FOUND")
                if not staff["is_active"]:
                    raise AppointmentBookingError("STAFF_INACTIVE")
            availability = AvailabilityEngine(self.connection).check(self.business_id, data.service_ids, data.start_datetime, selection)
            quote = None
            if availability.available:
                quote = self.slot_quote(availability.staff_id, availability.start_datetime, availability.end_datetime, data.service_ids)
            result = {"business_timezone": "Asia/Jerusalem", "requested_availability": {
                "available": availability.available, "reason": availability.reason, "quote": quote}, "alternatives": None}
            if availability.available or not data.include_alternatives:
                return result
            search_id = None
            published = False
            try:
                discovery = AlternativeDiscoveryService(self.connection, contexts).discover(
                    business_id=self.business_id, requested_staff_id=selection["staff_id"],
                    requested_start_datetime=start, requested_service_ids=data.service_ids)
                search_id = discovery.search_id
                options = []
                for option in discovery.result.alternatives:
                    value = asdict(option)
                    value.update(item_summary(option.items))
                    value["start_datetime"] = option.start_datetime.isoformat()
                    value["end_datetime"] = option.end_datetime.isoformat()
                    options.append(value)
                expires = ownership.publish(ticket, search_id) if search_id else None
                result["alternatives"] = {"search_id": search_id, "expires_at": expires.isoformat() if expires else None,
                                          "options": options, "total_available": discovery.result.total_available,
                                          "has_more": discovery.result.has_more}
                published = True
                return result
            finally:
                if search_id and not published:
                    contexts.expire_search_context(search_id)

    def slot_quote(self, staff_id, start, end, service_ids):
        staff = repositories.get_staff(self.connection, self.business_id, staff_id)
        items = self.appointments.resolve_staff_services(business_id=self.business_id, staff_id=staff_id, service_ids=service_ids)
        return {"staff_id": staff_id, "staff_name": staff["name"], "start_datetime": start, "end_datetime": end,
                **item_summary(items)}
