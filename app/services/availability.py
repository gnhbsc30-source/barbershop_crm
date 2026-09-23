from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from app.db.repositories import (
    get_active_staff_for_service,
    get_business_working_hours,
    get_business_settings,
    get_schedule_blocks,
    get_schedule_breaks,
    get_services_by_ids,
    get_staff,
    get_staff_appointments,
    get_staff_service,
    get_staff_working_hours,
)


StaffSelectionType = Literal["SPECIFIC", "ANY"]
AnySelectionMode = Literal["CHOICE", "AUTO"]


def generate_candidate_start_times(
    free_window_start: datetime,
    free_window_end: datetime,
    service_duration_minutes: int,
    booking_interval_minutes: int,
) -> list[datetime]:
    """Generate legal starts from one real free window.

    The real window start is always considered first. Later candidates use
    the preferred interval, but only when the full service fits in the window.
    """
    if service_duration_minutes <= 0:
        raise ValueError("service_duration_minutes must be positive")
    if booking_interval_minutes <= 0:
        raise ValueError("booking_interval_minutes must be positive")

    duration = timedelta(minutes=service_duration_minutes)
    if free_window_start + duration > free_window_end:
        return []

    candidates = [free_window_start]
    interval = timedelta(minutes=booking_interval_minutes)
    day_start = free_window_start.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    elapsed = free_window_start - day_start
    next_candidate = day_start + ((elapsed // interval) + 1) * interval

    while next_candidate + duration <= free_window_end:
        candidates.append(next_candidate)
        next_candidate += interval

    return candidates


@dataclass
class AvailabilityResult:
    """
    Structured result returned by the Availability Engine.

    The UI and Chat layer can consume the same result.
    """

    available: bool
    reason: str | None = None
    staff_id: int | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None


class AvailabilityEngine:
    """
    Determines whether a requested appointment fits into the
    real schedule of a business and staff member.

    The engine does NOT create appointments.

    Booking Interval is NOT a hard availability constraint.

    Real availability is calculated from:
    - business working hours
    - staff working hours
    - staff breaks
    - schedule blocks
    - existing appointments
    - service duration for the selected staff
    """

    def __init__(self, connection):
        self.connection = connection

    def check(
        self,
        business_id: int,
        service_ids: list[int],
        start_datetime: str,
        staff_selection: dict[str, Any],
    ) -> AvailabilityResult:
        """
        Check whether the requested appointment can be scheduled.

        Examples of staff_selection:

            {
                "type": "SPECIFIC",
                "staff_id": 3
            }

        or:

            {
                "type": "ANY",
                "mode": "CHOICE"
            }

        or:

            {
                "type": "ANY",
                "mode": "AUTO"
            }
        """

        requested_start = self._parse_datetime(start_datetime)

        if requested_start is None:
            return AvailabilityResult(
                available=False,
                reason="INVALID_DATETIME",
            )

        if requested_start < datetime.now():
            return AvailabilityResult(
                available=False,
                reason="PAST_DATETIME",
            )

        if not service_ids:
            return AvailabilityResult(
                available=False,
                reason="NO_SERVICES",
            )

        services = get_services_by_ids(
            self.connection,
            business_id,
            service_ids,
        )

        # Every requested service must exist and be active.
        if len(services) != len(set(service_ids)):
            return AvailabilityResult(
                available=False,
                reason="SERVICE_NOT_FOUND",
            )

        staff_type = staff_selection.get("type")

        # --------------------------------------------------------------
        # SPECIFIC STAFF
        # --------------------------------------------------------------

        if staff_type == "SPECIFIC":
            staff_id = staff_selection.get("staff_id")

            if staff_id is None:
                return AvailabilityResult(
                    available=False,
                    reason="STAFF_NOT_FOUND",
                )

            staff = get_staff(
                self.connection,
                business_id,
                staff_id,
            )

            if staff is None:
                return AvailabilityResult(
                    available=False,
                    reason="STAFF_NOT_FOUND",
                )

            if not self._staff_supports_all_services(
                business_id,
                staff_id,
                service_ids,
            ):
                return AvailabilityResult(
                    available=False,
                    reason="SERVICE_NOT_SUPPORTED",
                    staff_id=staff_id,
                )

            duration = self._calculate_staff_duration(
                business_id=business_id,
                staff_id=staff_id,
                service_ids=service_ids,
                services=services,
            )

            requested_end = requested_start + timedelta(
                minutes=duration
            )

            if self._fits_staff_schedule(
                business_id=business_id,
                staff_id=staff_id,
                start_datetime=requested_start,
                end_datetime=requested_end,
            ):
                return AvailabilityResult(
                    available=True,
                    staff_id=staff_id,
                    start_datetime=requested_start.isoformat(),
                    end_datetime=requested_end.isoformat(),
                )

            return AvailabilityResult(
                available=False,
                reason=self._determine_unavailable_reason(
                    business_id=business_id,
                    staff_id=staff_id,
                    start_datetime=requested_start,
                    end_datetime=requested_end,
                ),
                staff_id=staff_id,
            )

        # --------------------------------------------------------------
        # ANY STAFF
        # --------------------------------------------------------------

        if staff_type == "ANY":
            mode = staff_selection.get("mode", "CHOICE")

            eligible_staff = self._get_eligible_staff(
                business_id=business_id,
                service_ids=service_ids,
            )

            if not eligible_staff:
                return AvailabilityResult(
                    available=False,
                    reason="NO_ELIGIBLE_STAFF",
                )

            available_staff: list[tuple[int, int]] = []

            for staff in eligible_staff:
                staff_id = staff["id"]

                duration = self._calculate_staff_duration(
                    business_id=business_id,
                    staff_id=staff_id,
                    service_ids=service_ids,
                    services=services,
                )

                requested_end = requested_start + timedelta(
                    minutes=duration
                )

                if self._fits_staff_schedule(
                    business_id=business_id,
                    staff_id=staff_id,
                    start_datetime=requested_start,
                    end_datetime=requested_end,
                ):
                    available_staff.append(
                        (staff_id, duration)
                    )

            if not available_staff:
                return AvailabilityResult(
                    available=False,
                    reason="NO_STAFF_AVAILABLE",
                )

            # ----------------------------------------------------------
            # ANY + AUTO
            # ----------------------------------------------------------

            if mode == "AUTO":
                # Staff are already returned in deterministic order
                # by the repository.
                selected_staff_id, selected_duration = (
                    available_staff[0]
                )

                selected_end = requested_start + timedelta(
                    minutes=selected_duration
                )

                return AvailabilityResult(
                    available=True,
                    staff_id=selected_staff_id,
                    start_datetime=requested_start.isoformat(),
                    end_datetime=selected_end.isoformat(),
                )

            # ----------------------------------------------------------
            # ANY + CHOICE
            # ----------------------------------------------------------

            # At this stage the result contract only contains one
            # staff_id. The important decision here is that the Engine
            # determines that the requested time is available and
            # resolves an eligible staff member.
            #
            # A richer result containing all available staff will be
            # introduced when we build the UI/Chat selection layer.

            selected_staff_id, selected_duration = available_staff[0]

            selected_end = requested_start + timedelta(
                minutes=selected_duration
            )

            return AvailabilityResult(
                available=True,
                staff_id=selected_staff_id,
                start_datetime=requested_start.isoformat(),
                end_datetime=selected_end.isoformat(),
            )

        return AvailabilityResult(
            available=False,
            reason="INVALID_STAFF_SELECTION",
        )

    # ==================================================================
    # STAFF / SERVICE RESOLUTION
    # ==================================================================

    def _get_eligible_staff(
        self,
        business_id: int,
        service_ids: list[int],
    ) -> list[dict[str, Any]]:
        """
        Return active staff members who can perform every requested
        service.
        """

        first_service_staff = get_active_staff_for_service(
            self.connection,
            business_id,
            service_ids[0],
        )

        eligible_staff: list[dict[str, Any]] = []

        for staff in first_service_staff:
            staff_id = staff["id"]

            if self._staff_supports_all_services(
                business_id=business_id,
                staff_id=staff_id,
                service_ids=service_ids,
            ):
                eligible_staff.append(staff)

        return eligible_staff

    def _staff_supports_all_services(
        self,
        business_id: int,
        staff_id: int,
        service_ids: list[int],
    ) -> bool:
        """
        Check that this staff member can perform every requested
        service.
        """

        for service_id in service_ids:
            staff_service = get_staff_service(
                self.connection,
                business_id,
                staff_id,
                service_id,
            )

            if staff_service is None:
                return False

            if not staff_service["is_active"]:
                return False

        return True

    def _calculate_staff_duration(
        self,
        business_id: int,
        staff_id: int,
        service_ids: list[int],
        services: list[dict[str, Any]],
    ) -> int:
        """
        Calculate the actual duration required by this staff member.

        Priority:

        1. Staff-specific duration override
        2. Service default duration
        """

        services_by_id = {
            service["id"]: service
            for service in services
        }

        total_duration = 0

        for service_id in service_ids:
            service = services_by_id[service_id]

            staff_service = get_staff_service(
                self.connection,
                business_id,
                staff_id,
                service_id,
            )

            if staff_service is None:
                raise ValueError(
                    f"Staff {staff_id} does not support "
                    f"service {service_id}"
                )

            if not staff_service["is_active"]:
                raise ValueError(
                    f"Staff {staff_id} has inactive configuration "
                    f"for service {service_id}"
                )

            duration = staff_service["duration_minutes"]

            if duration is None:
                duration = service["default_duration_minutes"]

            total_duration += duration

        return total_duration

    # ==================================================================
    # REAL SCHEDULE
    # ==================================================================

    def _fits_staff_schedule(
        self,
        business_id: int,
        staff_id: int,
        start_datetime: datetime,
        end_datetime: datetime,
    ) -> bool:
        """
        Check whether the complete requested interval fits into the
        staff member's real available schedule.
        """

        target_date = start_datetime.date()

        # Current scheduling model handles appointments within one day.
        if end_datetime.date() != target_date:
            return False

        day_of_week = self._to_schema_day_of_week(
            start_datetime
        )

        business_hours = get_business_working_hours(
            self.connection,
            business_id,
            day_of_week,
        )

        if not business_hours:
            return False

        if not business_hours["is_working_day"]:
            return False

        staff_hours = get_staff_working_hours(
            self.connection,
            staff_id,
            day_of_week,
        )

        if not staff_hours:
            return False

        if not staff_hours["is_working_day"]:
            return False

        business_start, business_end = self._build_datetime_range(
            target_date,
            business_hours["start_time"],
            business_hours["end_time"],
        )

        staff_start, staff_end = self._build_datetime_range(
            target_date,
            staff_hours["start_time"],
            staff_hours["end_time"],
        )

        # The staff member can only work where both:
        # business AND staff are working.
        real_work_start = max(
            business_start,
            staff_start,
        )

        real_work_end = min(
            business_end,
            staff_end,
        )

        if start_datetime < real_work_start:
            return False

        if end_datetime > real_work_end:
            return False

        blocked_ranges: list[
            tuple[datetime, datetime]
        ] = []

        # --------------------------------------------------------------
        # Recurring breaks
        # --------------------------------------------------------------

        breaks = get_schedule_breaks(
            self.connection,
            staff_id,
            day_of_week,
        )

        for break_item in breaks:
            break_start, break_end = self._build_datetime_range(
                target_date,
                break_item["start_time"],
                break_item["end_time"],
            )

            blocked_ranges.append(
                (break_start, break_end)
            )

        # --------------------------------------------------------------
        # One-time blocks
        # --------------------------------------------------------------

        blocks = get_schedule_blocks(
            self.connection,
            business_id,
            staff_id,
            start_datetime.isoformat(),
            end_datetime.isoformat(),
        )

        for block in blocks:
            block_start = self._parse_datetime(
                block["start_datetime"]
            )

            block_end = self._parse_datetime(
                block["end_datetime"]
            )

            if block_start and block_end:
                blocked_ranges.append(
                    (block_start, block_end)
                )

        # --------------------------------------------------------------
        # Existing appointments
        # --------------------------------------------------------------

        appointments = get_staff_appointments(
            self.connection,
            business_id,
            staff_id,
            start_datetime.isoformat(),
            end_datetime.isoformat(),
        )

        for appointment in appointments:
            appointment_start = self._parse_datetime(
                appointment["start_datetime"]
            )

            appointment_end = self._parse_datetime(
                appointment["end_datetime"]
            )

            if appointment_start and appointment_end:
                blocked_ranges.append(
                    (
                        appointment_start,
                        appointment_end,
                    )
                )

        return not self._interval_overlaps_any(
            start_datetime,
            end_datetime,
            blocked_ranges,
        )

    def _build_staff_availability_windows(
        self,
        business_id: int,
        staff_id: int,
        target_date: date,
    ) -> list[tuple[datetime, datetime]]:
        """
        Build the real free windows for a staff member on a day.

        This method is intentionally independent of Booking Interval.

        The returned windows represent actual continuous free time.
        """

        day_of_week = self._to_schema_day_of_week_for_date(
            target_date
        )

        business_hours = get_business_working_hours(
            self.connection,
            business_id,
            day_of_week,
        )

        if not business_hours:
            return []

        if not business_hours["is_working_day"]:
            return []

        staff_hours = get_staff_working_hours(
            self.connection,
            staff_id,
            day_of_week,
        )

        if not staff_hours:
            return []

        if not staff_hours["is_working_day"]:
            return []

        business_start, business_end = self._build_datetime_range(
            target_date,
            business_hours["start_time"],
            business_hours["end_time"],
        )

        staff_start, staff_end = self._build_datetime_range(
            target_date,
            staff_hours["start_time"],
            staff_hours["end_time"],
        )

        # Actual working window = intersection of business and staff.
        real_start = max(
            business_start,
            staff_start,
        )

        real_end = min(
            business_end,
            staff_end,
        )

        if real_start >= real_end:
            return []

        blocked_ranges: list[
            tuple[datetime, datetime]
        ] = []

        # --------------------------------------------------------------
        # Breaks
        # --------------------------------------------------------------

        breaks = get_schedule_breaks(
            self.connection,
            staff_id,
            day_of_week,
        )

        for break_item in breaks:
            break_start, break_end = self._build_datetime_range(
                target_date,
                break_item["start_time"],
                break_item["end_time"],
            )

            blocked_ranges.append(
                (break_start, break_end)
            )

        # --------------------------------------------------------------
        # Schedule blocks
        # --------------------------------------------------------------

        blocks = get_schedule_blocks(
            self.connection,
            business_id,
            staff_id,
            real_start.isoformat(),
            real_end.isoformat(),
        )

        for block in blocks:
            block_start = self._parse_datetime(
                block["start_datetime"]
            )

            block_end = self._parse_datetime(
                block["end_datetime"]
            )

            if block_start and block_end:
                blocked_ranges.append(
                    (block_start, block_end)
                )

        # --------------------------------------------------------------
        # Existing appointments
        # --------------------------------------------------------------

        appointments = get_staff_appointments(
            self.connection,
            business_id,
            staff_id,
            real_start.isoformat(),
            real_end.isoformat(),
        )

        for appointment in appointments:
            appointment_start = self._parse_datetime(
                appointment["start_datetime"]
            )

            appointment_end = self._parse_datetime(
                appointment["end_datetime"]
            )

            if appointment_start and appointment_end:
                blocked_ranges.append(
                    (
                        appointment_start,
                        appointment_end,
                    )
                )

        return self._subtract_blocked_ranges(
            window_start=real_start,
            window_end=real_end,
            blocked_ranges=blocked_ranges,
        )

    def _subtract_blocked_ranges(
        self,
        window_start: datetime,
        window_end: datetime,
        blocked_ranges: list[
            tuple[datetime, datetime]
        ],
    ) -> list[tuple[datetime, datetime]]:
        """
        Remove blocked intervals from a working window.

        The result contains continuous, real free-time windows.
        """

        normalized: list[
            tuple[datetime, datetime]
        ] = []

        for start, end in blocked_ranges:
            clipped_start = max(
                start,
                window_start,
            )

            clipped_end = min(
                end,
                window_end,
            )

            if clipped_start < clipped_end:
                normalized.append(
                    (
                        clipped_start,
                        clipped_end,
                    )
                )

        if not normalized:
            return [
                (
                    window_start,
                    window_end,
                )
            ]

        normalized.sort(
            key=lambda item: item[0]
        )

        # Merge overlapping blocked ranges.
        merged: list[
            tuple[datetime, datetime]
        ] = []

        for start, end in normalized:
            if not merged:
                merged.append(
                    (start, end)
                )
                continue

            previous_start, previous_end = merged[-1]

            if start <= previous_end:
                merged[-1] = (
                    previous_start,
                    max(
                        previous_end,
                        end,
                    ),
                )
            else:
                merged.append(
                    (start, end)
                )

        # Calculate the gaps between blocked ranges.
        free_windows: list[
            tuple[datetime, datetime]
        ] = []

        cursor = window_start

        for blocked_start, blocked_end in merged:
            if cursor < blocked_start:
                free_windows.append(
                    (
                        cursor,
                        blocked_start,
                    )
                )

            cursor = max(
                cursor,
                blocked_end,
            )

        if cursor < window_end:
            free_windows.append(
                (
                    cursor,
                    window_end,
                )
            )

        return free_windows

    # ==================================================================
    # REASONING / OVERLAP
    # ==================================================================

    def _interval_overlaps_any(
        self,
        start_datetime: datetime,
        end_datetime: datetime,
        ranges: list[
            tuple[datetime, datetime]
        ],
    ) -> bool:
        """
        Check whether an interval overlaps any blocked interval.

        Overlap rule:

            existing_start < requested_end
            AND
            existing_end > requested_start
        """

        for existing_start, existing_end in ranges:
            if (
                existing_start < end_datetime
                and existing_end > start_datetime
            ):
                return True

        return False

    def _determine_unavailable_reason(
        self,
        business_id: int,
        staff_id: int,
        start_datetime: datetime,
        end_datetime: datetime,
    ) -> str:
        """
        Return the best currently-supported reason for
        an unavailable requested interval.
        """

        day_of_week = self._to_schema_day_of_week(
            start_datetime
        )

        business_hours = get_business_working_hours(
            self.connection,
            business_id,
            day_of_week,
        )

        if not business_hours:
            return "BUSINESS_CLOSED"

        if not business_hours["is_working_day"]:
            return "BUSINESS_CLOSED"

        staff_hours = get_staff_working_hours(
            self.connection,
            staff_id,
            day_of_week,
        )

        if not staff_hours:
            return "STAFF_NOT_WORKING"

        if not staff_hours["is_working_day"]:
            return "STAFF_NOT_WORKING"

        windows = self._build_staff_availability_windows(
            business_id=business_id,
            staff_id=staff_id,
            target_date=start_datetime.date(),
        )

        for window_start, window_end in windows:
            if (
                window_start <= start_datetime
                and end_datetime <= window_end
            ):
                return "AVAILABLE"

        return "NO_STAFF_AVAILABLE"

    # ==================================================================
    # DATETIME HELPERS
    # ==================================================================

    @staticmethod
    def _parse_datetime(
        value: str | None,
    ) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_datetime_range(
        target_date: date,
        start_time: str,
        end_time: str,
    ) -> tuple[datetime, datetime]:
        start_datetime = datetime.fromisoformat(
            f"{target_date.isoformat()}T{start_time}"
        )

        end_datetime = datetime.fromisoformat(
            f"{target_date.isoformat()}T{end_time}"
        )

        return start_datetime, end_datetime

    @staticmethod
    def _to_schema_day_of_week(
        value: datetime,
    ) -> int:
        """
        Python weekday:
            Monday = 0
            ...
            Sunday = 6

        Database schema:
            Sunday = 0
            Monday = 1
            ...
            Saturday = 6
        """

        return (value.weekday() + 1) % 7

    @staticmethod
    def _to_schema_day_of_week_for_date(
        value: date,
    ) -> int:
        return (value.weekday() + 1) % 7
