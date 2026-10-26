from __future__ import annotations

from app.services.alternative_selection import (
    AlternativeSelectionContextStore,
    AlternativeSelectionError,
)
from app.services.appointments import (
    AppointmentBookingError,
    AppointmentBookingResult,
    AppointmentService,
)
from app.services.booking_confirmation import BookingConfirmation


class AlternativeSelectionService:
    """Book one trusted alternative through the existing appointment flow."""

    _STALE_SELECTION_ERROR_CODES = frozenset(
        {
            "APPOINTMENT_UNAVAILABLE",
            "PAST_DATETIME",
            "MINIMUM_BOOKING_NOTICE_VIOLATION",
            "BOOKING_WINDOW_EXCEEDED",
            "STAFF_NOT_FOUND",
            "STAFF_INACTIVE",
            "SERVICE_NOT_FOUND",
            "SERVICE_NOT_SUPPORTED",
        }
    )

    def __init__(
        self,
        selection_context_store: AlternativeSelectionContextStore,
        appointment_service: AppointmentService,
    ) -> None:
        self._selection_context_store = selection_context_store
        self._appointment_service = appointment_service

    def book_selected_alternative(
        self,
        *,
        search_id: str,
        option_id: str,
        customer_id: int,
        notes: str | None = None,
        confirmation: BookingConfirmation | None = None,
    ) -> AppointmentBookingResult:
        """Resolve trusted selection data and book it with fresh validation."""
        selection = self._selection_context_store.claim_selection(
            search_id,
            option_id,
        )

        try:
            result = self._appointment_service.book(
                business_id=selection.business_id,
                customer_id=customer_id,
                start_datetime=selection.start_datetime.isoformat(),
                service_ids=list(selection.requested_service_ids),
                staff_selection={"type": "SPECIFIC", "staff_id": selection.staff_id},
                notes=notes,
                **({"confirmation": confirmation} if confirmation is not None else {}),
            )
        except AppointmentBookingError as error:
            self._selection_context_store.release_selection_claim(search_id)
            if error.code in self._STALE_SELECTION_ERROR_CODES:
                raise AlternativeSelectionError(
                    "SELECTED_ALTERNATIVE_NO_LONGER_AVAILABLE"
                ) from None
            raise
        except BaseException:
            self._selection_context_store.release_selection_claim(search_id)
            raise

        self._selection_context_store.expire_search_context(search_id)
        return result
