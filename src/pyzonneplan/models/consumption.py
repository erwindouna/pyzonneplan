"""Models for the P1 electricity and gas consumption endpoints.

Shape reverse-engineered from the dotted sensor key-paths (e.g.
``measurement_groups.0.totals.d``) in fsaris/home-assistant-zonneplan-one's
``const.py``, which reads these against the live API. No raw response has
been captured yet, so the exact set of ``meta`` keys is not confirmed.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from mashumaro.mixins.orjson import DataClassORJSONMixin

from pyzonneplan.const import WH_TO_KWH


@dataclass
class ElectricityMeasurementGroup(DataClassORJSONMixin):
    """One measurement window (today / this month / this year) of electricity-delivered."""

    date: str | None = None
    totals: dict[str, int] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def delivered_kwh(self) -> Decimal | None:
        """Return electricity consumed from the grid, in kWh."""
        value = self.totals.get("d")
        return None if value is None else Decimal(value) * WH_TO_KWH

    @property
    def returned_kwh(self) -> Decimal | None:
        """Return electricity returned to the grid, in kWh."""
        value = self.totals.get("p")
        return None if value is None else Decimal(value) * WH_TO_KWH


@dataclass
class ElectricityDelivered(DataClassORJSONMixin):
    """Response of /connections/{uuid}/electricity-delivered."""

    measurement_groups: list[ElectricityMeasurementGroup] = field(default_factory=list)


@dataclass
class GasMeasurementGroup(DataClassORJSONMixin):
    """One measurement window (today / this month / this year) of gas consumption."""

    date: str | None = None
    total: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def total_m3(self) -> Decimal | None:
        """Return gas consumed, in cubic meters."""
        return None if self.total is None else Decimal(self.total) * WH_TO_KWH


@dataclass
class Gas(DataClassORJSONMixin):
    """Response of /connections/{uuid}/gas."""

    measurement_groups: list[GasMeasurementGroup] = field(default_factory=list)
