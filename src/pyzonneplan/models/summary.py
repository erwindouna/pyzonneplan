"""Models for the connection summary endpoint (``/connections/{uuid}/summary``).

Modelled from a captured response of an account without a P1 meter, where the
live usage fields are empty (``value`` 0, ``measured_at`` null). The response
is the same for the electricity and the gas connection's uuid.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from mashumaro.mixins.orjson import DataClassORJSONMixin

from pyzonneplan.const import MONEY_FACTOR


@dataclass
class SummaryUsage(DataClassORJSONMixin):
    """The live usage block of the summary, as the app shows it on its home screen.

    ``value`` is the current usage in W and ``measured_at`` when the meter was
    last read; both need a P1 meter. ``type`` is the current tariff group
    (low/normal/high) and ``sustainability_score`` is in permille.
    ``status_message`` and ``status_tip`` are texts in the account's locale.
    """

    value: int | None = None
    measured_at: datetime | None = None
    speed: int | None = None
    type: str | None = None
    sustainability_score: int | None = None
    status_message: str | None = None
    status_tip: str | None = None


@dataclass
class SummaryPrice(DataClassORJSONMixin):
    """One hour of the summary's price forecast, with prices in 1e-7 EUR.

    ``gas_price`` is only set on the hour the gas day starts (06:00 local),
    and applies from then until the next gas day.
    """

    start: datetime
    electricity_price: int
    electricity_price_excl_tax: int
    gas_price: int | None = None
    gas_price_excl_tax: int | None = None
    tariff_group: str | None = None
    sustainability_score: int | None = None
    solar_percentage: int | None = None
    solar_yield: int | None = None

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Rename ``datetime``, which would shadow the type."""
        return {"start": d["datetime"], **{key: value for key, value in d.items() if key != "datetime"}}

    @property
    def end(self) -> datetime:
        """Return the end of the hour (exclusive)."""
        return self.start + timedelta(hours=1)

    @property
    def electricity_price_euro(self) -> Decimal:
        """Return the electricity price including tax, in EUR/kWh."""
        return Decimal(self.electricity_price) * MONEY_FACTOR

    @property
    def gas_price_euro(self) -> Decimal | None:
        """Return the gas price including tax, in EUR/m³, on the hour the gas day starts."""
        return None if self.gas_price is None else Decimal(self.gas_price) * MONEY_FACTOR


@dataclass
class Summary(DataClassORJSONMixin):
    """Response of /connections/{uuid}/summary.

    ``price_per_hour`` runs from the current hour for about two days.
    ``electricity`` and ``surplus_electricity`` are 0 without a P1 meter;
    their meaning with one is unconfirmed.
    """

    usage: SummaryUsage = field(default_factory=SummaryUsage)
    electricity: int | None = None
    surplus_electricity: int | None = None
    price_per_hour: list[SummaryPrice] = field(default_factory=list)

    def price_at(self, moment: datetime) -> SummaryPrice | None:
        """Return the hour whose ``[start, end)`` contains ``moment``, if the forecast covers it.

        ``moment`` must be timezone-aware.
        """
        return next((price for price in self.price_per_hour if price.start <= moment < price.end), None)
