"""Owner API response and input contracts."""

import re
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.api.customer_booking_models import (
    Confirmation, PositiveID, PriceSummary, QuoteView, SlotRequest, StrictModel,
)



class CalendarAppointment(QuoteView):
    appointment_id: PositiveID
    customer_id: PositiveID
    customer_name: str
    status: Literal["BOOKED", "COMPLETED", "CANCELLED", "NO_SHOW"]


class AppointmentDetail(CalendarAppointment):
    notes: str | None
    customer_phone: str | None




def validated_phone(value):
    value = value.strip()
    if re.fullmatch(r"\+?[0-9]{7,15}", value) is None:
        raise ValueError("INVALID_PHONE")
    return value


class ExistingCustomer(StrictModel):
    type: Literal["EXISTING"]
    customer_id: PositiveID


class NewCustomer(StrictModel):
    type: Literal["NEW"]
    name: str
    phone: str = Field(default=None)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        value = value.strip()
        if not 1 <= len(value) <= 100:
            raise ValueError("INVALID_NAME")
        return value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        return validated_phone(value)


class OwnerSlot(SlotRequest):
    staff_id: PositiveID


class OwnerBooking(OwnerSlot):
    customer: Annotated[ExistingCustomer | NewCustomer, Field(discriminator="type")]
    confirmation: Confirmation
    notes: Annotated[str | None, Field(max_length=2000)] = None


class CustomerChoice(StrictModel):
    customer_id: PositiveID
    name: str
    phone: str | None


class CustomerPage(StrictModel):
    customers: list[CustomerChoice]
    next_after_id: PositiveID | None


class BookingService(StrictModel):
    service_id: PositiveID
    name: str


class BookingStaff(StrictModel):
    staff_id: PositiveID
    staff_name: str
    quote: PriceSummary | None = None


class BookingOptions(StrictModel):
    services: list[BookingService]
    staff: list[BookingStaff]


class ConfirmableSlot(StrictModel):
    staff_id: PositiveID
    start_datetime: str
    end_datetime: str
    confirmation: Confirmation


class SlotQuote(StrictModel):
    available: bool
    reason: str | None
    quote: ConfirmableSlot | None
