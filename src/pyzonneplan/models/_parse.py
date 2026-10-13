"""Lenient parsers the models share: unknown or invalid values become ``None``."""

import logging
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from mashumaro import field_options

from pyzonneplan.const import API_TIMEZONE, MONEY_FACTOR, WH_TO_KWH

_LOGGER = logging.getLogger(__name__)


def _normalise(value: str) -> str:
    return value.replace("_", "").replace("-", "").lower()


def parse_enum[E: StrEnum](enum: type[E], value: Any, label: str) -> E | None:
    """Parse an enum value ignoring case and underscores (``ShuttingDown`` = ``shutting_down``).

    Returns ``None`` when the value is missing or unknown, so values the API
    adds later don't break parsing.
    """
    if value is None:
        return None
    key = _normalise(str(value))
    for member in enum:
        if _normalise(member.value) == key:
            return member
    _LOGGER.debug("Unknown %s: %s", label, value)
    return None


def enum_field(enum: type[StrEnum], label: str) -> dict[str, Any]:
    """Return field metadata that parses an optional enum with :func:`parse_enum`."""
    return field_options(deserialize=lambda value: parse_enum(enum, value, label))


def to_datetime(value: Any) -> datetime | None:
    """Parse an API timestamp, or return ``None`` when it's missing or invalid.

    Timestamps come in UTC (``...Z``). One without a timezone is read as
    Europe/Amsterdam local time, the format the charge point actions take,
    so callers always get a timezone-aware datetime.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=API_TIMEZONE)


# Field metadata that parses a timestamp with to_datetime instead of mashumaro's parser.
DATETIME = field_options(deserialize=to_datetime)


def euro(value: float | None) -> Decimal | None:
    """Convert a 1e-7 EUR amount to euro.

    Per-unit prices in chart metadata are fractional, so floats go through
    ``str`` to avoid binary rounding noise.
    """
    return None if value is None else Decimal(str(value)) * MONEY_FACTOR


def kwh(value: int | None) -> Decimal | None:
    """Convert Wh to kWh."""
    return None if value is None else Decimal(value) * WH_TO_KWH
