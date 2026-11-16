"""Tenant-scoped owner appointment reads and commands."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from app.db import repositories
from app.db.database import read_snapshot, transaction
from app.exceptions.customer import CustomerAlreadyExistsError
from app.services.appointments import AppointmentBookingError, AppointmentService, ResolvedAppointmentItem
from app.services.availability import AvailabilityEngine
from app.services.booking_confirmation import BookingConfigurationError
from app.services.customer_booking import item_summary
from app.services.customers import create_customer


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

    def _detail(self, appointment_id, *, include_revision=False):
        rows = repositories.get_owner_appointment_rows(
            self.connection, self.business_id, appointment_id=appointment_id)
        if not rows:
            raise OwnerAppointmentError("APPOINTMENT_NOT_FOUND")
        detail, = self._with_items(rows)
        revision = detail.pop("updated_at")
        parsed = parse_revision(revision)
        etag = appointment_etag(self.business_id, revision, detail)
        return (detail, etag, parsed) if include_revision else (detail, etag)

    def customers(self, *, limit, after_id, name_prefix, phone):
        rows = repositories.list_owner_customers(
            self.connection, self.business_id, limit=limit + 1, after_id=after_id,
            name_prefix=name_prefix, phone=phone)
        return {"customers": rows[:limit],
                "next_after_id": rows[limit - 1]["customer_id"] if len(rows) > limit else None}

    def booking_options(self, service_ids):
        with read_snapshot(self.connection):
            services, staff = repositories.list_owner_booking_catalog(self.connection, self.business_id, 201)
            if len(services) > 200 or len(staff) > 200:
                raise OwnerAppointmentError("BOOKING_OPTIONS_LIMIT_EXCEEDED")
            if service_ids:
                if not set(service_ids).issubset(row["service_id"] for row in services):
                    raise OwnerAppointmentError("SERVICE_NOT_FOUND")
                eligible = AvailabilityEngine(self.connection).get_eligible_staff(self.business_id, service_ids)
                resolver = AppointmentService(self.connection)
                staff = [
                    {"staff_id": row["id"], "staff_name": row["name"], "quote": item_summary(
                        resolver.resolve_staff_services(business_id=self.business_id,
                                                        staff_id=row["id"], service_ids=service_ids))}
                    for row in sorted(eligible, key=lambda row: row["id"])
                ]
            return {"services": services, "staff": staff}

    def quote(self, data):
        with read_snapshot(self.connection):
            service = AppointmentService(self.connection)
            items = service.resolve_staff_services(
                business_id=self.business_id, staff_id=data.staff_id, service_ids=data.service_ids)
            service.validate_booking_policy(business_id=self.business_id,
                                             requested_start=datetime.fromisoformat(data.start_datetime))
            availability = AvailabilityEngine(self.connection).check(
                self.business_id, data.service_ids, data.start_datetime,
                {"type": "SPECIFIC", "staff_id": data.staff_id})
            if not availability.available:
                return {"available": False, "reason": availability.reason, "quote": None}
            terms = item_summary(items)
            for item in terms["items"]:
                del item["service_name"]
            return {"available": True, "reason": None, "quote": {
                "staff_id": data.staff_id, "start_datetime": availability.start_datetime,
                "end_datetime": availability.end_datetime, "confirmation": terms}}

    def book(self, data, prepare_response):
        response = None

        def before_commit(result):
            nonlocal response
            response = prepare_response(*self._detail(result.appointment_id))

        service = AppointmentService(self.connection)
        arguments = {
            "business_id": self.business_id, "start_datetime": data.start_datetime,
            "service_ids": data.service_ids, "confirmation": data.confirmation.domain(),
            "notes": data.notes, "before_commit": before_commit, "require_active_customer": True,
        }
        if data.customer.type == "EXISTING":
            service.book(customer_id=data.customer.customer_id,
                         staff_selection={"type": "SPECIFIC", "staff_id": data.staff_id}, **arguments)
        else:
            with transaction(self.connection):
                try:
                    customer_id = create_customer(
                        self.connection, self.business_id, name=data.customer.name, phone=data.customer.phone)
                except CustomerAlreadyExistsError:
                    raise OwnerAppointmentError("CUSTOMER_PHONE_ALREADY_EXISTS") from None
                service._book_in_transaction(customer_id=customer_id, staff_id=data.staff_id, **arguments)
        return response

    def edit(self, appointment_id, data, expected_etag, prepare_response):
        with transaction(self.connection):
            current, etag, revision = self._detail(appointment_id, include_revision=True)
            if etag != expected_etag:
                raise OwnerAppointmentError("APPOINTMENT_VERSION_MISMATCH")
            old_start = datetime.fromisoformat(current["start_datetime"])
            if current["status"] != "BOOKED" or old_start <= datetime.now():
                raise OwnerAppointmentError("APPOINTMENT_NOT_EDITABLE")

            staff_id = data.staff_id if "staff_id" in data.model_fields_set else current["staff_id"]
            start = datetime.fromisoformat(data.start_datetime) if "start_datetime" in data.model_fields_set else old_start
            if staff_id == current["staff_id"] and start == old_start:
                return prepare_response(current, etag)

            self._validate_destination(staff_id, current["items"])
            duration = timedelta(minutes=current["total_duration_minutes"])
            if not current["items"] or old_start + duration != datetime.fromisoformat(current["end_datetime"]):
                raise BookingConfigurationError()
            AppointmentService(self.connection).validate_booking_policy(
                business_id=self.business_id, requested_start=start)
            end = start + duration
            if not AvailabilityEngine(self.connection).can_move_appointment(
                business_id=self.business_id, staff_id=staff_id, start=start, end=end,
                appointment_id=appointment_id,
            ):
                raise OwnerAppointmentError("APPOINTMENT_UNAVAILABLE")

            utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                new_revision = max(utc_now, revision + timedelta(microseconds=1))
            except OverflowError:
                raise BookingConfigurationError() from None
            repositories.update_owner_appointment_schedule(
                self.connection, self.business_id, appointment_id, staff_id=staff_id,
                start=start.isoformat(), end=end.isoformat(),
                revision=new_revision.isoformat(sep=" ", timespec="microseconds"))
            return prepare_response(*self._detail(appointment_id))

    def _validate_destination(self, staff_id, items):
        staff = repositories.get_staff(self.connection, self.business_id, staff_id)
        if not staff or not staff["is_active"]:
            raise OwnerAppointmentError("STAFF_NOT_FOUND")
        service_ids = list(dict.fromkeys(item["service_id"] for item in items))
        if len(repositories.get_services_by_ids(self.connection, self.business_id, service_ids)) != len(service_ids):
            raise OwnerAppointmentError("BOOKED_SERVICE_INACTIVE")
        if not AvailabilityEngine(self.connection).staff_supports_services(
            business_id=self.business_id, staff_id=staff_id, service_ids=service_ids,
        ):
            raise OwnerAppointmentError("SERVICE_NOT_SUPPORTED")

    def _with_items(self, rows):
        grouped = {row["appointment_id"]: [] for row in rows}
        for item in repositories.get_owner_appointment_items(self.connection, self.business_id, list(grouped)):
            grouped[item["appointment_id"]].append(ResolvedAppointmentItem(
                item["service_id"], item["service_name"], item["duration_minutes"], item["price"]))
        return [{**row, **item_summary(grouped[row["appointment_id"]])} for row in rows]


def parse_revision(value):
    try:
        revision = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise BookingConfigurationError() from None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?", value) is None:
        raise BookingConfigurationError()
    return revision
