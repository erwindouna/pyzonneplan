"""Models for the electricity and gas consumption endpoints.

The chart endpoints (``/connections/{uuid}/{electricity-delivered,gas}/charts/{interval}``)
are modelled from captured responses. They work without a P1 meter: usage then
comes from the grid operator's history, which arrives a day or more late.

The summary endpoints (``/connections/{uuid}/{electricity-delivered,gas}``)
return ``null`` without a P1 meter. Their models are reverse-engineered from
the dotted sensor key-paths (e.g. ``measurement_groups.0.totals.d``,
``contracts.0.meta.dsmr_version``) in fsaris/home-assistant-zonneplan-one's
``const.py``; no populated response has been captured yet.

The consumption insight charts (``/api/connections/{uuid}/{electricity,gas}/charts/consumption-insight``)
are modelled from a captured electricity response and the app's code.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from mashumaro.mixins.orjson import DataClassORJSONMixin

from pyzonneplan.const import WH_TO_KWH, MeasurementGroupType, TariffGroup

from ._parse import DATETIME, enum_field, euro, kwh
from .account import Contract
from .devices import P1Meter

# Gas volumes are reported in dm³ (litres).
DM3_TO_M3 = Decimal("0.001")


@dataclass
class ElectricityMeasurementGroup(DataClassORJSONMixin):
    """One measurement window (today / this month / this year) of electricity-delivered."""

    date: datetime | None = field(default=None, metadata=DATETIME)
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
    """Response of /connections/{uuid}/electricity-delivered.

    ``contracts`` are the P1 readers with their live readings, which are more
    current here than in /user-accounts/me.
    """

    contracts: list[Contract] = field(default_factory=list)
    measurement_groups: list[ElectricityMeasurementGroup] = field(default_factory=list)

    @property
    def meters(self) -> list[P1Meter]:
        """Return a view per P1 reader."""
        return [P1Meter(contract) for contract in self.contracts]


@dataclass
class GasMeasurementGroup(DataClassORJSONMixin):
    """One measurement window (today / this month / this year) of gas consumption."""

    date: datetime | None = field(default=None, metadata=DATETIME)
    total: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def total_m3(self) -> Decimal | None:
        """Return gas consumed, in cubic meters."""
        return None if self.total is None else Decimal(self.total) * DM3_TO_M3


@dataclass
class Gas(DataClassORJSONMixin):
    """Response of /connections/{uuid}/gas (``contracts`` as in :class:`ElectricityDelivered`)."""

    contracts: list[Contract] = field(default_factory=list)
    measurement_groups: list[GasMeasurementGroup] = field(default_factory=list)

    @property
    def meters(self) -> list[P1Meter]:
        """Return a view per P1 reader."""
        return [P1Meter(contract) for contract in self.contracts]


@dataclass
class ElectricityChartMeasurement(DataClassORJSONMixin):
    """Electricity used and returned during one interval of a chart, in Wh."""

    start: datetime
    delivered: int
    produced: int
    tariff_group: TariffGroup | None = field(default=None, metadata=enum_field(TariffGroup, "tariff group"))

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Flatten ``values``/``meta``.

        The API reports production as a negative number here (but positive in
        the group totals), and the months chart sends ``d`` as a string.
        """
        values = d.get("values", {})
        return {
            "start": d["date"],
            "delivered": abs(int(values.get("d") or 0)),
            "produced": abs(int(values.get("p") or 0)),
            "tariff_group": d.get("meta", {}).get("tariff_group"),
        }

    @property
    def delivered_kwh(self) -> Decimal:
        """Return electricity consumed from the grid, in kWh."""
        return Decimal(self.delivered) * WH_TO_KWH

    @property
    def produced_kwh(self) -> Decimal:
        """Return electricity returned to the grid, in kWh."""
        return Decimal(self.produced) * WH_TO_KWH


@dataclass
class GasChartMeasurement(DataClassORJSONMixin):
    """Gas used during one interval of a chart, in dm³."""

    start: datetime
    volume: int

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Normalise the hours chart (``measured_at``) and the day/month charts (``date``)."""
        return {"start": d.get("measured_at") or d["date"], "volume": int(d.get("value") or 0)}

    @property
    def volume_m3(self) -> Decimal:
        """Return gas consumed, in cubic meters."""
        return Decimal(self.volume) * DM3_TO_M3


def _drop_duplicate_starts(measurements: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Keep the first measurement for each start time.

    The gas hours chart ends with a stray ``<day>T00:00Z`` entry of 0 that
    repeats an earlier hour, which would otherwise overwrite real usage.
    """
    seen: set[str | None] = set()
    unique = []
    for measurement in measurements:
        start = measurement.get(key) or measurement.get("date")
        if start in seen:
            continue
        seen.add(start)
        unique.append(measurement)
    return unique


@dataclass
class ElectricityChartGroup(DataClassORJSONMixin):
    """The measurements of one electricity chart window, and their totals (Wh) and costs (1e-7 EUR)."""

    start: datetime
    type: MeasurementGroupType | None = field(default=None, metadata=enum_field(MeasurementGroupType, "measurement group type"))
    totals: dict[str, int] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    measurements: list[ElectricityChartMeasurement] = field(default_factory=list)

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Rename ``date`` and drop repeated measurements."""
        return {**d, "start": d["date"], "measurements": _drop_duplicate_starts(d.get("measurements", []), "date")}

    @property
    def has_data(self) -> bool:
        """Return whether the grid operator has delivered data for this window.

        Pending windows report zero totals (and sometimes zero measurements),
        so zero can't be told apart from "not yet known" by the values alone.
        """
        return self.meta.get("energy_delivered_sum") is not None

    @property
    def delivered_kwh(self) -> Decimal | None:
        """Return electricity consumed from the grid, in kWh, or ``None`` while pending."""
        return Decimal(self.totals.get("d", 0)) * WH_TO_KWH if self.has_data else None

    @property
    def produced_kwh(self) -> Decimal | None:
        """Return electricity returned to the grid, in kWh, or ``None`` while pending."""
        return Decimal(self.totals.get("p", 0)) * WH_TO_KWH if self.has_data else None

    @property
    def delivery_cost(self) -> Decimal | None:
        """Return the cost of the electricity used, including tax, in EUR, or ``None`` while pending."""
        return euro(self.meta.get("delivery_costs_incl_tax"))

    @property
    def delivery_cost_excl_tax(self) -> Decimal | None:
        """Return the cost of the electricity used, excluding tax, in EUR, or ``None`` while pending."""
        return euro(self.meta.get("delivery_costs_excl_tax"))


@dataclass
class GasChartGroup(DataClassORJSONMixin):
    """The measurements of one gas chart window, and their total (dm³) and costs (1e-7 EUR)."""

    start: datetime
    type: MeasurementGroupType | None = field(default=None, metadata=enum_field(MeasurementGroupType, "measurement group type"))
    total: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    measurements: list[GasChartMeasurement] = field(default_factory=list)

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Rename ``date`` and drop repeated measurements."""
        return {**d, "start": d["date"], "measurements": _drop_duplicate_starts(d.get("measurements", []), "measured_at")}

    @property
    def has_data(self) -> bool:
        """Return whether the grid operator has delivered data for this window (see ElectricityChartGroup)."""
        return self.meta.get("energy_delivered_sum") is not None

    @property
    def total_m3(self) -> Decimal | None:
        """Return gas consumed, in cubic meters, or ``None`` while pending."""
        return Decimal(self.total) * DM3_TO_M3 if self.has_data else None

    @property
    def delivery_cost(self) -> Decimal | None:
        """Return the cost of the gas used, including tax, in EUR, or ``None`` while pending."""
        return euro(self.meta.get("delivery_costs_incl_tax"))

    @property
    def delivery_cost_excl_tax(self) -> Decimal | None:
        """Return the cost of the gas used, excluding tax, in EUR, or ``None`` while pending."""
        return euro(self.meta.get("delivery_costs_excl_tax"))


@dataclass
class ElectricityChart(DataClassORJSONMixin):
    """Response of /connections/{uuid}/electricity-delivered/charts/{interval}."""

    measurement_groups: list[ElectricityChartGroup] = field(default_factory=list)

    @property
    def group(self) -> ElectricityChartGroup | None:
        """Return the chart's window; the API sends exactly one."""
        return self.measurement_groups[0] if self.measurement_groups else None


@dataclass
class GasChart(DataClassORJSONMixin):
    """Response of /connections/{uuid}/gas/charts/{interval}."""

    measurement_groups: list[GasChartGroup] = field(default_factory=list)

    @property
    def group(self) -> GasChartGroup | None:
        """Return the chart's window; the API sends exactly one."""
        return self.measurement_groups[0] if self.measurement_groups else None


def _insight_volume(volume: dict[str, int] | None) -> Decimal | None:
    """Convert a consumption insight volume, ``{"wh": ...}`` or ``{"dm3": ...}``, to kWh or m³."""
    if not volume:
        return None
    if "wh" in volume:
        return kwh(volume["wh"])
    if "dm3" in volume:
        return Decimal(volume["dm3"]) * DM3_TO_M3
    return None


@dataclass
class ConsumptionInsightPoint(DataClassORJSONMixin):
    """One month of a consumption insight chart.

    ``volume`` is in kWh for electricity and m³ for gas; ``average_price`` is
    the average price per kWh or m³ in EUR, negative for electricity returned.
    """

    start: datetime
    volume: Decimal | None = None
    average_price: Decimal | None = None

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Flatten ``volume.wh``/``volume.dm3`` and ``average_cost.amount`` (1e-7 EUR)."""
        return {
            "start": d["date"],
            "volume": _insight_volume(d.get("volume")),
            "average_price": euro((d.get("average_cost") or {}).get("amount")),
        }


@dataclass
class ConsumptionInsightSeries(DataClassORJSONMixin):
    """The months of a consumption insight series, with their total and average price (units as in :class:`ConsumptionInsightPoint`)."""

    total: Decimal | None = None
    average_price: Decimal | None = None
    points: list[ConsumptionInsightPoint] = field(default_factory=list)

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Flatten ``aggregate`` and rename ``data``."""
        aggregate = d.get("aggregate") or {}
        return {
            "total": _insight_volume(aggregate.get("sum_volume")),
            "average_price": euro((aggregate.get("average_cost") or {}).get("amount")),
            "points": d.get("data") or [],
        }


@dataclass
class ConsumptionInsight(DataClassORJSONMixin):
    """Response of /api/connections/{uuid}/{electricity,gas}/charts/consumption-insight.

    ``delivery`` is the energy used, ``production`` the electricity returned
    to the grid (gas has none).
    """

    granularity: MeasurementGroupType | None = field(default=None, metadata=enum_field(MeasurementGroupType, "measurement group type"))
    start: datetime | None = field(default=None, metadata=DATETIME)
    end: datetime | None = field(default=None, metadata=DATETIME)
    delivery: ConsumptionInsightSeries | None = None
    production: ConsumptionInsightSeries | None = None

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Flatten ``range`` and ``series``."""
        date_range = d.get("range") or {}
        series = d.get("series") or {}
        return {
            "granularity": d.get("granularity"),
            "start": date_range.get("start_date"),
            "end": date_range.get("end_date"),
            "delivery": series.get("delivery"),
            "production": series.get("production"),
        }
