from dataclasses import replace
import math

import pytest
from pydantic import ValidationError

from app.api.customer_booking_models import BookingRequest, Confirmation, SearchRequest
from app.services.appointments import ResolvedAppointmentItem
from app.services.booking_confirmation import BookingConfigurationError, BookingConfirmation, format_cents, parse_money, real_to_cents


@pytest.mark.parametrize("value,cents", [(0, 0), (-0.0, 0), (0.1, 10), (0.29, 29), (1.23, 123), (80.0, 8000)])
def test_exact_real_money(value, cents):
    assert real_to_cents(value) == cents
    assert parse_money(format_cents(cents)) == cents


@pytest.mark.parametrize("value", [True, None, "1.23", float("nan"), float("inf"), float("-inf"), -0.01, 1.005,
                                         0.1 + 0.2, math.nextafter(1.23, math.inf), math.nextafter(1.23, -math.inf), 1e100])
def test_invalid_real_money_never_rounds(value):
    with pytest.raises(BookingConfigurationError):
        real_to_cents(value)


@pytest.mark.parametrize("value", [1.23, True, "1", "1.0", "1.000", "01.00", "+1.00", "-0.00", "NaN", "Infinity", "1e2", " 1.00", "1.00\n", "١.00", "1" * 65 + ".00"])
def test_transport_money_is_canonical(value):
    with pytest.raises(ValueError):
        parse_money(value)


def test_item_totals_use_cents_not_float_sum():
    items = [ResolvedAppointmentItem(1, "A", 10, 0.1), ResolvedAppointmentItem(2, "B", 20, 0.2)]
    terms = BookingConfirmation.from_items(items)
    assert terms.total_price_cents == 30
    assert terms.total_duration_minutes == 30
    assert terms != replace(terms, items=tuple(reversed(terms.items)))


@pytest.mark.parametrize("changes", [
    {"business_id": 2}, {"customer_id": 2}, {"service_ids": [True]}, {"service_ids": []},
    {"service_ids": [1, 1]}, {"service_ids": list(range(1, 22))}, {"start_datetime": "2027-01-01"},
    {"start_datetime": "2027-01-01T10:00:00Z"}, {"start_datetime": "2027-01-01T10:00:00+02:00"},
])
def test_search_transport_rejects_identity_and_invalid_inputs(changes):
    value = {"service_ids": [1], "staff_selection": {"type": "SPECIFIC", "staff_id": 1}, "start_datetime": "2027-01-01T10:00:00"}
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({**value, **changes})
