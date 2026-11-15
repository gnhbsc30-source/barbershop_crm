"""Strict customer transport contracts; identities are never request fields."""

from datetime import datetime
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.booking_confirmation import BookingConfirmation, ConfirmationItem, parse_money


PositiveID = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
Money = Annotated[str, Field(strict=True, max_length=64, pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$")]
Services = Annotated[list[PositiveID], Field(min_length=1, max_length=20)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SpecificStaff(StrictModel):
    type: Literal["SPECIFIC"]
    staff_id: PositiveID


class AnyStaff(StrictModel):
    type: Literal["ANY"]
    mode: Literal["CHOICE"]


class ItemConfirmation(StrictModel):
    service_id: PositiveID
    duration_minutes: Annotated[int, Field(strict=True, gt=0)]
    price: Money

    @field_validator("price")
    @classmethod
    def valid_money(cls, value):
        parse_money(value)
        return value


class Confirmation(StrictModel):
    items: Annotated[list[ItemConfirmation], Field(min_length=1, max_length=20)]
    total_duration_minutes: Annotated[int, Field(strict=True, gt=0)]
    total_price: Money
    currency: Literal["ILS"]

    @field_validator("total_price")
    @classmethod
    def valid_money(cls, value):
        parse_money(value)
        return value

    def domain(self):
        return BookingConfirmation(
            tuple(ConfirmationItem(i.service_id, i.duration_minutes, parse_money(i.price)) for i in self.items),
            self.total_duration_minutes, parse_money(self.total_price), self.currency,
        )


class SlotRequest(StrictModel):
    service_ids: Services
    start_datetime: str

    @field_validator("service_ids")
    @classmethod
    def unique_services(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("DUPLICATE_SERVICE")
        return values

    @field_validator("start_datetime")
    @classmethod
    def local_datetime(cls, value):
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?", value) is None:
            raise ValueError("INVALID_DATETIME")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("INVALID_DATETIME") from None
        if parsed.tzinfo is not None:
            raise ValueError("INVALID_DATETIME")
        return value


class SearchRequest(SlotRequest):
    staff_selection: Annotated[SpecificStaff | AnyStaff, Field(discriminator="type")]
    include_alternatives: bool = False

    @model_validator(mode="after")
    def specific_discovery(self):
        if self.include_alternatives and self.staff_selection.type == "ANY":
            raise ValueError("ANY_STAFF_DISCOVERY_UNSUPPORTED")
        return self


class BookingRequest(SlotRequest):
    staff_selection: SpecificStaff
    confirmation: Confirmation
    notes: Annotated[str | None, Field(max_length=2000)] = None


class SelectionRequest(StrictModel):
    option_id: Annotated[str, Field(min_length=1, max_length=128)]
    confirmation: Confirmation
    notes: Annotated[str | None, Field(max_length=2000)] = None


class ItemView(StrictModel):
    service_id: PositiveID
    service_name: str
    duration_minutes: Annotated[int, Field(strict=True, gt=0)]
    price: Money


class PriceSummary(StrictModel):
    items: list[ItemView]
    total_duration_minutes: Annotated[int, Field(strict=True, ge=0)]
    total_price: Money
    currency: Literal["ILS"]


class StaffView(PriceSummary):
    staff_id: PositiveID
    staff_name: str


class QuoteView(StaffView):
    start_datetime: str
    end_datetime: str


class AlternativeView(QuoteView):
    option_id: str
    priority_group: Annotated[int, Field(ge=1, le=4)]
    reason_code: Literal["SAME_STAFF_SAME_DAY", "OTHER_STAFF_SAME_DAY", "SAME_STAFF_OTHER_DAY", "OTHER_STAFF_OTHER_DAY"]
    distance_from_requested_start_minutes: int
    distance_from_requested_date_days: int
    date: str
    day_of_week: str
    display_date: str
    display_time: str


class RequestedAvailability(StrictModel):
    available: bool
    reason: str | None
    quote: QuoteView | None


class AlternativeResults(StrictModel):
    search_id: str | None
    expires_at: str | None
    options: list[AlternativeView]
    total_available: int
    has_more: bool


class SearchResponse(StrictModel):
    business_timezone: Literal["Asia/Jerusalem"]
    requested_availability: RequestedAvailability
    alternatives: AlternativeResults | None


class ServiceView(StrictModel):
    service_id: PositiveID
    name: str
    description: str | None
    default_duration_minutes: Annotated[int, Field(strict=True, gt=0)]
    default_price: Money


class CatalogResponse(StrictModel):
    services: list[ServiceView]


class StaffResponse(StrictModel):
    service_ids: Services
    staff: list[StaffView]


class AppointmentView(QuoteView):
    appointment_id: PositiveID
    status: Literal["BOOKED", "COMPLETED", "CANCELLED", "NO_SHOW"]
    notes: str | None


class HistoryResponse(StrictModel):
    appointments: list[AppointmentView]
    next_before_id: PositiveID | None
    has_more: bool


def request_contract(model):
    """Describe manually bounded JSON parsing without dangling schema references."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(definitions[value["$ref"].rsplit("/", 1)[1]])
            # Discriminator mappings reference the same named definitions; the
            # inline oneOf schemas already express these discriminated variants.
            return {key: inline(item) for key, item in value.items() if key != "discriminator"}
        return value

    return {"requestBody": {"required": True, "content": {"application/json": {"schema": inline(schema)}}}}
