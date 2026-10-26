"""Exact ILS confirmation terms; no HTTP, persistence, or scheduling decisions."""

from dataclasses import dataclass
from decimal import Decimal
import math
import re


class BookingConfigurationError(ValueError):
    code = "BOOKING_CONFIGURATION_INVALID"

    def __init__(self):
        super().__init__(self.code)


def format_cents(cents: int) -> str:
    if type(cents) is not int or cents < 0:
        raise BookingConfigurationError()
    result = f"{cents // 100}.{cents % 100:02d}"
    if len(result) > 64:
        raise BookingConfigurationError()
    return result


def real_to_cents(value: int | float) -> int:
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        raise BookingConfigurationError()
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise BookingConfigurationError()
    numerator, denominator = amount.as_integer_ratio()
    cents, remainder = divmod(numerator * 100, denominator)
    if remainder:
        raise BookingConfigurationError()
    format_cents(cents)  # Check the same wire bound before any booking write.
    return cents


def parse_money(value: str) -> int:
    if not isinstance(value, str) or len(value) > 64 or re.fullmatch(r"(?:0|[1-9][0-9]*)\.[0-9]{2}", value) is None:
        raise ValueError("INVALID_MONEY")
    whole, fraction = value.split(".")
    return int(whole) * 100 + int(fraction)


@dataclass(frozen=True)
class ConfirmationItem:
    service_id: int
    duration_minutes: int
    price_cents: int


@dataclass(frozen=True)
class BookingConfirmation:
    items: tuple[ConfirmationItem, ...]
    total_duration_minutes: int
    total_price_cents: int
    currency: str = "ILS"

    @classmethod
    def from_items(cls, items):
        terms = tuple(ConfirmationItem(item.service_id, item.duration_minutes, real_to_cents(item.price)) for item in items)
        if any(type(item.duration_minutes) is not int or item.duration_minutes <= 0 for item in terms):
            raise BookingConfigurationError()
        total = sum(item.price_cents for item in terms)
        format_cents(total)
        return cls(terms, sum(item.duration_minutes for item in terms), total)
