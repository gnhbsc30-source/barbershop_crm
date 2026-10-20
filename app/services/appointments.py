from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable

from app.db.database import transaction
from app.db.repositories import (
    create_appointment,
    get_customer,
    get_business_settings,
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


class AlternativeReasonCode(str, Enum):
    SAME_STAFF_SAME_DAY = "SAME_STAFF_SAME_DAY"
    OTHER_STAFF_SAME_DAY = "OTHER_STAFF_SAME_DAY"
    SAME_STAFF_OTHER_DAY = "SAME_STAFF_OTHER_DAY"
    OTHER_STAFF_OTHER_DAY = "OTHER_STAFF_OTHER_DAY"


@dataclass(frozen=True)
class AlternativeOption:
    """One request-scoped, non-reserved appointment alternative."""

    option_id: str
    staff_id: int
    staff_name: str
    start_datetime: datetime
    end_datetime: datetime
    items: list[ResolvedAppointmentItem]
    total_duration_minutes: int
    total_price: float
    priority_group: int
    reason_code: AlternativeReasonCode
    distance_from_requested_start_minutes: int
    distance_from_requested_date_days: int
    date: str
    day_of_week: str
    display_date: str
    display_time: str


@dataclass(frozen=True)
class AlternativeSearchResult:
    """The future Alternative Discovery response without persistence or reservation."""

    requested_staff_id: int | None
    requested_start_datetime: datetime
    requested_service_ids: list[int]
    alternatives: list[AlternativeOption]
    total_available: int
    has_more: bool


def classify_alternative_priority(
    *,
    requested_staff_id: int | None,
    requested_start_datetime: datetime,
    candidate_staff_id: int,
    candidate_start_datetime: datetime,
) -> tuple[int, AlternativeReasonCode]:
    """Classify one SPECIFIC-staff alternative into an approved priority group."""
    if requested_staff_id is None:
        raise ValueError("ANY_STAFF_PRIORITY_UNSUPPORTED")

    if candidate_start_datetime.date() < requested_start_datetime.date():
        raise ValueError("EARLIER_CANDIDATE_DATE_UNSUPPORTED")

    same_staff = candidate_staff_id == requested_staff_id
    same_date = candidate_start_datetime.date() == requested_start_datetime.date()

    if same_staff and same_date:
        return 1, AlternativeReasonCode.SAME_STAFF_SAME_DAY
    if not same_staff and same_date:
        return 2, AlternativeReasonCode.OTHER_STAFF_SAME_DAY
    if same_staff:
        return 3, AlternativeReasonCode.SAME_STAFF_OTHER_DAY
    return 4, AlternativeReasonCode.OTHER_STAFF_OTHER_DAY


def calculate_alternative_ranking_metadata(
    *,
    requested_staff_id: int | None,
    requested_start_datetime: datetime,
    candidate_staff_id: int,
    candidate_start_datetime: datetime,
) -> tuple[int, AlternativeReasonCode, int, int]:
    """Return approved priority and deterministic date/time distances."""
    priority_group, reason_code = classify_alternative_priority(
        requested_staff_id=requested_staff_id,
        requested_start_datetime=requested_start_datetime,
        candidate_staff_id=candidate_staff_id,
        candidate_start_datetime=candidate_start_datetime,
    )
    date_distance_days = abs(
        (candidate_start_datetime.date() - requested_start_datetime.date()).days
    )
    requested_clock_minutes = (
        requested_start_datetime.hour * 60 + requested_start_datetime.minute
    )
    candidate_clock_minutes = (
        candidate_start_datetime.hour * 60 + candidate_start_datetime.minute
    )
    start_distance_minutes = abs(candidate_clock_minutes - requested_clock_minutes)

    return (
        priority_group,
        reason_code,
        date_distance_days,
        start_distance_minutes,
    )


def deduplicate_alternative_options(
    options: list[AlternativeOption],
) -> list[AlternativeOption]:
    """Remove identical staff/start options, rejecting conflicting business truth."""
    unique_by_identity: dict[tuple[int, datetime], AlternativeOption] = {}

    for option in options:
        identity = (option.staff_id, option.start_datetime)
        existing = unique_by_identity.get(identity)
        if existing is None:
            unique_by_identity[identity] = option
            continue

        if _alternative_business_truth(existing) != _alternative_business_truth(option):
            raise ValueError("CONFLICTING_ALTERNATIVE_DUPLICATE")

    return list(unique_by_identity.values())


def rank_alternative_options(
    options: list[AlternativeOption],
    *,
    requested_staff_id: int | None,
    requested_start_datetime: datetime,
    booking_interval_minutes: int,
) -> list[AlternativeOption]:
    """Deduplicate, annotate, and deterministically sort SPECIFIC-staff options."""
    if booking_interval_minutes <= 0:
        raise ValueError("booking_interval_minutes must be positive")

    ranked_options: list[AlternativeOption] = []
    for option in deduplicate_alternative_options(options):
        (
            priority_group,
            reason_code,
            date_distance_days,
            start_distance_minutes,
        ) = calculate_alternative_ranking_metadata(
            requested_staff_id=requested_staff_id,
            requested_start_datetime=requested_start_datetime,
            candidate_staff_id=option.staff_id,
            candidate_start_datetime=option.start_datetime,
        )
        ranked_options.append(
            replace(
                option,
                priority_group=priority_group,
                reason_code=reason_code,
                distance_from_requested_date_days=date_distance_days,
                distance_from_requested_start_minutes=start_distance_minutes,
            )
        )

    return sorted(
        ranked_options,
        key=lambda option: (
            option.priority_group,
            option.distance_from_requested_date_days,
            option.distance_from_requested_start_minutes,
            not _is_booking_interval_aligned(
                option.start_datetime,
                booking_interval_minutes,
            ),
            option.staff_id,
            option.start_datetime,
        ),
    )


def _alternative_business_truth(
    option: AlternativeOption,
) -> tuple[object, ...]:
    return (
        option.staff_id,
        option.staff_name,
        option.start_datetime,
        option.end_datetime,
        tuple(option.items),
        option.total_duration_minutes,
        option.total_price,
    )


def _is_booking_interval_aligned(
    start_datetime: datetime,
    booking_interval_minutes: int,
) -> bool:
    day_start = start_datetime.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return (start_datetime - day_start) % timedelta(
        minutes=booking_interval_minutes
    ) == timedelta(0)


class AppointmentService:
    """Orchestrate deterministic appointment booking rules."""

    def __init__(
        self,
        connection: Any,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.connection = connection
        self._now_provider = now_provider or datetime.now

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

        self._ensure_booking_policy(
            business_id=business_id,
            requested_start=requested_start,
        )

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

        return requested_start, self.resolve_staff_services(
            business_id=business_id,
            staff_id=staff_id,
            service_ids=service_ids,
        )

    def resolve_staff_services(
        self,
        *,
        business_id: int,
        staff_id: int,
        service_ids: list[int],
    ) -> list[ResolvedAppointmentItem]:
        """Validate one staff member and resolve their service snapshots."""
        if not service_ids:
            raise AppointmentBookingError("NO_SERVICES")

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

        return resolved_items

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

    def _ensure_booking_policy(
        self,
        *,
        business_id: int,
        requested_start: datetime,
    ) -> None:
        """Apply business booking-window and minimum-notice rules."""
        settings = get_business_settings(self.connection, business_id)
        booking_window_months = (
            settings["booking_window_months"] if settings else 3
        )
        minimum_booking_notice_minutes = (
            settings["minimum_booking_notice_minutes"] if settings else 0
        )
        now = self._now_provider()

        if requested_start < now:
            raise AppointmentBookingError("PAST_DATETIME")

        earliest_allowed_start = now + timedelta(
            minutes=minimum_booking_notice_minutes
        )
        if requested_start < earliest_allowed_start:
            raise AppointmentBookingError("MINIMUM_BOOKING_NOTICE_VIOLATION")

        latest_allowed_start = self.add_calendar_months(
            now,
            booking_window_months,
        )
        if requested_start > latest_allowed_start:
            raise AppointmentBookingError("BOOKING_WINDOW_EXCEEDED")

    @staticmethod
    def add_calendar_months(value: datetime, months: int) -> datetime:
        """Add whole calendar months while clamping invalid month-end dates."""
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)

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
