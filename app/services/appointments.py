from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.db.database import transaction
from app.db.repositories import (
    create_appointment,
    get_customer,
    get_services_by_ids,
    get_staff,
    get_staff_service,
)
from app.services.availability import AvailabilityEngine


class AppointmentBookingError(ValueError):
    """A deterministic business-rule rejection while booking."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ResolvedAppointmentItem:
    service_id: int
    service_name: str
    duration_minutes: int
    price: float


@dataclass(frozen=True)
class AppointmentBookingResult:
    appointment_id: int
    staff_id: int
    start_datetime: str
    end_datetime: str
    total_duration_minutes: int
    total_price: float
    items: list[ResolvedAppointmentItem]


class AppointmentService:
    """Orchestrate deterministic appointment booking rules."""

    def __init__(self, connection: Any):
        self.connection = connection

    def book(
        self,
        *,
        business_id: int,
        customer_id: int,
        start_datetime: str,
        service_ids: list[int],
        staff_selection: dict[str, Any],
        notes: str | None = None,
        allow_auto_assign: bool = False,
    ) -> AppointmentBookingResult:
        """Book one appointment after validation and two availability checks."""
        staff_id = self._resolve_selected_staff(
            business_id=business_id,
            start_datetime=start_datetime,
            service_ids=service_ids,
            staff_selection=staff_selection,
            allow_auto_assign=allow_auto_assign,
        )
        requested_start, items = self.resolve(
            business_id=business_id,
            customer_id=customer_id,
            staff_id=staff_id,
            start_datetime=start_datetime,
            service_ids=service_ids,
        )
        total_duration, total_price = self.totals(items)
        requested_end = requested_start + timedelta(minutes=total_duration)

        self._ensure_available(
            connection=self.connection,
            business_id=business_id,
            staff_id=staff_id,
            service_ids=service_ids,
            start_datetime=requested_start,
        )

        with transaction(self.connection) as connection:
            self._ensure_available(
                connection=connection,
                business_id=business_id,
                staff_id=staff_id,
                service_ids=service_ids,
                start_datetime=requested_start,
            )
            appointment_id = create_appointment(
                connection=connection,
                business_id=business_id,
                customer_id=customer_id,
                staff_id=staff_id,
                start_datetime=requested_start.isoformat(),
                end_datetime=requested_end.isoformat(),
                notes=notes,
                items=[
                    {
                        "service_id": item.service_id,
                        "service_name": item.service_name,
                        "duration_minutes": item.duration_minutes,
                        "price": item.price,
                    }
                    for item in items
                ],
            )

        return AppointmentBookingResult(
            appointment_id=appointment_id,
            staff_id=staff_id,
            start_datetime=requested_start.isoformat(),
            end_datetime=requested_end.isoformat(),
            total_duration_minutes=total_duration,
            total_price=total_price,
            items=items,
        )

    def resolve(
        self,
        *,
        business_id: int,
        customer_id: int,
        staff_id: int,
        start_datetime: str,
        service_ids: list[int],
    ) -> tuple[datetime, list[ResolvedAppointmentItem]]:
        """Validate a specific-staff request and derive its item snapshots."""
        requested_start = self._parse_start_datetime(start_datetime)

        if not service_ids:
            raise AppointmentBookingError("NO_SERVICES")

        if get_customer(self.connection, business_id, customer_id) is None:
            raise AppointmentBookingError("CUSTOMER_NOT_FOUND")

        staff = get_staff(self.connection, business_id, staff_id)
        if staff is None:
            raise AppointmentBookingError("STAFF_NOT_FOUND")
        if not staff["is_active"]:
            raise AppointmentBookingError("STAFF_INACTIVE")

        services = get_services_by_ids(
            self.connection,
            business_id,
            service_ids,
        )
        if len(services) != len(set(service_ids)):
            raise AppointmentBookingError("SERVICE_NOT_FOUND")

        services_by_id = {service["id"]: service for service in services}
        resolved_items: list[ResolvedAppointmentItem] = []

        for service_id in service_ids:
            staff_service = get_staff_service(
                self.connection,
                business_id,
                staff_id,
                service_id,
            )
            if staff_service is None or not staff_service["is_active"]:
                raise AppointmentBookingError("SERVICE_NOT_SUPPORTED")

            service = services_by_id[service_id]
            duration = staff_service["duration_minutes"]
            price = staff_service["price"]
            resolved_items.append(
                ResolvedAppointmentItem(
                    service_id=service_id,
                    service_name=service["name"],
                    duration_minutes=(
                        duration
                        if duration is not None
                        else service["default_duration_minutes"]
                    ),
                    price=float(
                        price if price is not None else service["default_price"]
                    ),
                )
            )

        return requested_start, resolved_items

    @staticmethod
    def _parse_start_datetime(start_datetime: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(start_datetime)
        except (TypeError, ValueError):
            raise AppointmentBookingError("INVALID_DATETIME") from None

        if parsed.tzinfo is not None:
            raise AppointmentBookingError("INVALID_DATETIME")

        return parsed

    @staticmethod
    def totals(items: list[ResolvedAppointmentItem]) -> tuple[int, float]:
        return (
            sum(item.duration_minutes for item in items),
            sum(item.price for item in items),
        )

    def _resolve_selected_staff(
        self,
        *,
        business_id: int,
        start_datetime: str,
        service_ids: list[int],
        staff_selection: dict[str, Any],
        allow_auto_assign: bool,
    ) -> int:
        staff_type = staff_selection.get("type")
        if staff_type == "SPECIFIC":
            staff_id = staff_selection.get("staff_id")
            if not isinstance(staff_id, int):
                raise AppointmentBookingError("STAFF_NOT_FOUND")
            return staff_id

        if staff_type != "ANY":
            raise AppointmentBookingError("INVALID_STAFF_SELECTION")

        if staff_selection.get("mode", "CHOICE") == "CHOICE":
            raise AppointmentBookingError("STAFF_SELECTION_REQUIRES_CHOICE")
        if staff_selection.get("mode") != "AUTO":
            raise AppointmentBookingError("INVALID_STAFF_SELECTION")
        if not allow_auto_assign:
            raise AppointmentBookingError("AUTO_ASSIGNMENT_NOT_AUTHORIZED")

        availability = AvailabilityEngine(self.connection).check(
            business_id=business_id,
            service_ids=service_ids,
            start_datetime=start_datetime,
            staff_selection={"type": "ANY", "mode": "AUTO"},
        )
        if not availability.available or availability.staff_id is None:
            raise AppointmentBookingError("APPOINTMENT_UNAVAILABLE")
        return availability.staff_id

    @staticmethod
    def _ensure_available(
        *,
        connection: Any,
        business_id: int,
        staff_id: int,
        service_ids: list[int],
        start_datetime: datetime,
    ) -> None:
        availability = AvailabilityEngine(connection).check(
            business_id=business_id,
            service_ids=service_ids,
            start_datetime=start_datetime.isoformat(),
            staff_selection={"type": "SPECIFIC", "staff_id": staff_id},
        )
        if not availability.available:
            raise AppointmentBookingError("APPOINTMENT_UNAVAILABLE")
