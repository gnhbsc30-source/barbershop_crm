from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable

from app.db.repositories import (
    get_business_settings,
    get_services_by_ids,
    get_staff,
)
from app.services.alternative_selection import (
    AlternativeSelectionContextStore,
    AlternativeSelectionOption,
)
from app.services.appointments import (
    AlternativeOption,
    AlternativeReasonCode,
    AlternativeSearchResult,
    AppointmentBookingError,
    AppointmentService,
    ResolvedAppointmentItem,
    rank_alternative_options,
)
from app.services.availability import AvailabilityEngine, generate_candidate_start_times


@dataclass(frozen=True)
class AlternativeDiscoveryResponse:
    """Discovery data paired with an optional temporary selection context."""

    search_id: str | None
    result: AlternativeSearchResult


class AlternativeDiscoveryService:
    """Discover non-persistent alternatives for SPECIFIC staff requests."""

    def __init__(
        self,
        connection: Any,
        selection_context_store: AlternativeSelectionContextStore,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.connection = connection
        self._selection_context_store = selection_context_store
        self._now_provider = now_provider or datetime.now

    def discover(
        self,
        *,
        business_id: int,
        requested_staff_id: int | None,
        requested_start_datetime: datetime,
        requested_service_ids: list[int],
    ) -> AlternativeDiscoveryResponse:
        """Return every legal, ranked alternative in the approved discovery range."""
        if requested_staff_id is None:
            raise AppointmentBookingError("ANY_STAFF_DISCOVERY_UNSUPPORTED")

        now = self._now_provider()
        settings = get_business_settings(self.connection, business_id)
        booking_window_months = settings["booking_window_months"] if settings else 3
        minimum_notice_minutes = (
            settings["minimum_booking_notice_minutes"] if settings else 0
        )
        search_window_days = settings["alternative_search_window_days"] if settings else 7
        booking_interval_minutes = settings["booking_interval_minutes"] if settings else 30

        earliest_start = now + timedelta(minutes=minimum_notice_minutes)
        booking_window_end = AppointmentService.add_calendar_months(
            now,
            booking_window_months,
        )

        if requested_start_datetime < now:
            raise AppointmentBookingError("PAST_DATETIME")
        if requested_start_datetime < earliest_start:
            raise AppointmentBookingError("MINIMUM_BOOKING_NOTICE_VIOLATION")
        if requested_start_datetime > booking_window_end:
            raise AppointmentBookingError("BOOKING_WINDOW_EXCEEDED")

        last_search_date = min(
            requested_start_datetime.date() + timedelta(days=search_window_days),
            booking_window_end.date(),
        )

        if not requested_service_ids:
            raise AppointmentBookingError("NO_SERVICES")

        requested_staff = get_staff(
            self.connection,
            business_id,
            requested_staff_id,
        )
        if requested_staff is None:
            raise AppointmentBookingError("STAFF_NOT_FOUND")
        if not requested_staff["is_active"]:
            raise AppointmentBookingError("STAFF_INACTIVE")

        services = get_services_by_ids(
            self.connection,
            business_id,
            requested_service_ids,
        )
        if len(services) != len(set(requested_service_ids)):
            raise AppointmentBookingError("SERVICE_NOT_FOUND")

        resolver = AppointmentService(self.connection)
        availability = AvailabilityEngine(self.connection)
        eligible_staff = availability.get_eligible_staff(
            business_id=business_id,
            service_ids=requested_service_ids,
        )
        staff_by_id = {staff["id"]: staff for staff in eligible_staff}
        staff_ids = ([] if requested_staff_id not in staff_by_id else [requested_staff_id]) + [
            staff_id for staff_id in staff_by_id if staff_id != requested_staff_id
        ]
        resolved_by_staff = {
            staff_id: resolver.resolve_staff_services(
                business_id=business_id,
                staff_id=staff_id,
                service_ids=requested_service_ids,
            )
            for staff_id in staff_ids
        }

        unranked: list[AlternativeOption] = []
        for target_date in self._search_dates(
            requested_date=requested_start_datetime.date(),
            last_search_date=last_search_date,
        ):
            for staff_id in staff_ids:
                items = resolved_by_staff[staff_id]
                total_duration, total_price = AppointmentService.totals(items)
                for window_start, window_end in availability.get_staff_free_windows(
                    business_id=business_id,
                    staff_id=staff_id,
                    target_date=target_date,
                ):
                    candidate_window_start = max(window_start, earliest_start)
                    for candidate_start in generate_candidate_start_times(
                        candidate_window_start,
                        window_end,
                        total_duration,
                        booking_interval_minutes,
                    ):
                        if candidate_start > booking_window_end:
                            continue
                        unranked.append(
                            self._build_option(
                                staff=staff_by_id[staff_id],
                                start_datetime=candidate_start,
                                items=items,
                                total_duration=total_duration,
                                total_price=total_price,
                            )
                        )

        ranked = rank_alternative_options(
            unranked,
            requested_staff_id=requested_staff_id,
            requested_start_datetime=requested_start_datetime,
            booking_interval_minutes=booking_interval_minutes,
        )
        alternatives = [
            replace(option, option_id=f"option_{index:03d}")
            for index, option in enumerate(ranked, start=1)
        ]
        result = AlternativeSearchResult(
            requested_staff_id=requested_staff_id,
            requested_start_datetime=requested_start_datetime,
            requested_service_ids=requested_service_ids,
            alternatives=alternatives,
            total_available=len(alternatives),
            has_more=False,
        )
        if not alternatives:
            return AlternativeDiscoveryResponse(search_id=None, result=result)

        context = self._selection_context_store.create_search_context(
            business_id=business_id,
            requested_service_ids=requested_service_ids,
            options=[
                AlternativeSelectionOption(
                    option_id=option.option_id,
                    staff_id=option.staff_id,
                    start_datetime=option.start_datetime,
                )
                for option in alternatives
            ],
        )
        return AlternativeDiscoveryResponse(search_id=context.search_id, result=result)

    @staticmethod
    def _search_dates(
        *,
        requested_date: date,
        last_search_date: date,
    ) -> list[date]:
        if last_search_date < requested_date:
            return []
        return [
            requested_date + timedelta(days=offset)
            for offset in range((last_search_date - requested_date).days + 1)
        ]

    @staticmethod
    def _build_option(
        *,
        staff: dict[str, Any],
        start_datetime: datetime,
        items: list[ResolvedAppointmentItem],
        total_duration: int,
        total_price: float,
    ) -> AlternativeOption:
        return AlternativeOption(
            option_id="pending",
            staff_id=staff["id"],
            staff_name=staff["name"],
            start_datetime=start_datetime,
            end_datetime=start_datetime + timedelta(minutes=total_duration),
            items=items,
            total_duration_minutes=total_duration,
            total_price=total_price,
            priority_group=0,
            reason_code=AlternativeReasonCode.SAME_STAFF_SAME_DAY,
            distance_from_requested_start_minutes=0,
            distance_from_requested_date_days=0,
            date=start_datetime.date().isoformat(),
            day_of_week=start_datetime.strftime("%A"),
            display_date=start_datetime.date().isoformat(),
            display_time=start_datetime.strftime("%H:%M"),
        )
