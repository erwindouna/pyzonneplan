"""Models for the P1, PV, battery and charge point endpoints.

The shapes come from fsaris/home-assistant-zonneplan-one (its sensor key-paths)
and the payloads users posted in its issues (#75 charge point, #266 battery);
they are not verified against live hardware here. Device endpoints wrap their
values in ``{"contracts": [...], "measurement_groups": [...]}``, with the
device state in each contract's ``meta``, so most models are typed views on a
:class:`Contract`, like the ones returned by /user-accounts/me.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from mashumaro import field_options
from mashumaro.mixins.orjson import DataClassORJSONMixin

from pyzonneplan.const import API_TIMEZONE, MONEY_FACTOR, WH_TO_KWH, BatteryState

from .account import Contract

_LOGGER = logging.getLogger(__name__)


def _euro(value: int | None) -> Decimal | None:
    """Convert a 1e-7 EUR amount to euro."""
    return None if value is None else Decimal(value) * MONEY_FACTOR


def _kwh(value: int | None) -> Decimal | None:
    """Convert Wh to kWh."""
    return None if value is None else Decimal(value) * WH_TO_KWH


def _battery_state(value: str | None) -> BatteryState | None:
    """Parse a battery state case-insensitively, or return ``None`` when it's missing or unknown."""
    if value is None:
        return None
    try:
        return BatteryState(str(value).lower())
    except ValueError:
        _LOGGER.debug("Unknown battery state: %s", value)
        return None


def _datetime(value: str | None) -> datetime | None:
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


# Field metadata that parses a timestamp with _datetime instead of mashumaro's parser.
_DATETIME = field_options(deserialize=_datetime)


@dataclass
class DeviceMeasurement(DataClassORJSONMixin):
    """One data point of a device's measurement group or chart."""

    measured_at: datetime | None = field(default=None, metadata=_DATETIME)
    value: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceMeasurementGroup(DataClassORJSONMixin):
    """A measurement window (``measurement_groups[i]``) of a device endpoint.

    ``date`` is the start of the window; ``total`` its sum, in the unit of the
    endpoint (Wh for PV yield, 1e-7 EUR for a battery result).
    """

    date: datetime | None = field(default=None, metadata=_DATETIME)
    total: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    measurements: list[DeviceMeasurement] = field(default_factory=list)


@dataclass(slots=True)
class P1Meter:
    """Typed view on a p1_installation contract's meta block (live P1 readings, in W)."""

    contract: Contract

    @property
    def uuid(self) -> str:
        """Return the P1 reader's contract UUID."""
        return self.contract.uuid

    @property
    def dsmr_version(self) -> str | None:
        """Return the smart meter's DSMR version."""
        return self.contract.meta.get("dsmr_version")

    @property
    def electricity_delivery(self) -> int | None:
        """Return the last measured power drawn from the grid, in W."""
        return self.contract.meta.get("electricity_last_measured_delivery_value")

    @property
    def electricity_production(self) -> int | None:
        """Return the last measured power returned to the grid, in W."""
        return self.contract.meta.get("electricity_last_measured_production_value")

    @property
    def electricity_average(self) -> int | None:
        """Return the average measured power, in W."""
        return self.contract.meta.get("electricity_last_measured_average_value")

    @property
    def electricity_first_measured_at(self) -> datetime | None:
        """Return when the meter first reported electricity."""
        return _datetime(self.contract.meta.get("electricity_first_measured_at"))

    @property
    def electricity_last_measured_at(self) -> datetime | None:
        """Return when the meter last reported electricity."""
        return _datetime(self.contract.meta.get("electricity_last_measured_at"))

    @property
    def electricity_last_measured_production_at(self) -> datetime | None:
        """Return when the meter last reported power returned to the grid."""
        return _datetime(self.contract.meta.get("electricity_last_measured_production_at"))

    @property
    def gas_first_measured_at(self) -> datetime | None:
        """Return when the meter first reported gas."""
        return _datetime(self.contract.meta.get("gas_first_measured_at"))

    @property
    def gas_last_measured_at(self) -> datetime | None:
        """Return when the meter last reported gas."""
        return _datetime(self.contract.meta.get("gas_last_measured_at"))


@dataclass(slots=True)
class PvInverter:
    """Typed view on a pv_installation contract's meta block."""

    contract: Contract

    @property
    def uuid(self) -> str:
        """Return the installation UUID."""
        return self.contract.uuid

    @property
    def model_name(self) -> str | None:
        """Return the inverter model."""
        return self.contract.meta.get("inverter_model_name")

    @property
    def inverter_firmware_version(self) -> str | None:
        """Return the inverter firmware version."""
        return self.contract.meta.get("inverter_firmware_version")

    @property
    def module_firmware_version(self) -> str | None:
        """Return the module firmware version."""
        return self.contract.meta.get("module_firmware_version")

    @property
    def installation_wp(self) -> int | None:
        """Return the total installed peak power."""
        return self.contract.meta.get("installation_wp")

    @property
    def panel_wp(self) -> int | None:
        """Return the peak power per panel."""
        return self.contract.meta.get("panel_wp")

    @property
    def panel_count(self) -> int | None:
        """Return the number of panels."""
        return self.contract.meta.get("panel_count")

    @property
    def last_measured_power(self) -> int | None:
        """Return the last measured production, in W."""
        return self.contract.meta.get("last_measured_power_value")

    @property
    def total_power_measured(self) -> int | None:
        """Return lifetime production, in Wh."""
        return self.contract.meta.get("total_power_measured")

    @property
    def total_power_measured_kwh(self) -> Decimal | None:
        """Return lifetime production, in kWh."""
        return _kwh(self.total_power_measured)

    @property
    def first_measured_at(self) -> datetime | None:
        """Return when the inverter first reported."""
        return _datetime(self.contract.meta.get("first_measured_at"))

    @property
    def last_measured_at(self) -> datetime | None:
        """Return when the inverter last reported."""
        return _datetime(self.contract.meta.get("last_measured_at"))

    @property
    def total_earned(self) -> Decimal | None:
        """Return lifetime Powerplay earnings."""
        return _euro(self.contract.meta.get("total_earned"))

    @property
    def total_day(self) -> Decimal | None:
        """Return today's Powerplay earnings."""
        return _euro(self.contract.meta.get("total_day"))

    @property
    def dynamic_control_enabled(self) -> bool | None:
        """Return whether Powerplay may curtail the inverter."""
        return self.contract.meta.get("dynamic_control_enabled")

    @property
    def power_limit_active(self) -> bool | None:
        """Return whether production is being curtailed right now."""
        return self.contract.meta.get("power_limit_active")


@dataclass
class PvInstallation(DataClassORJSONMixin):
    """Response of /connections/{uuid}/pv-installation: every inverter on the connection.

    ``measurement_groups[0]`` is today's combined yield of all inverters (Wh),
    with the power measured during the day in its ``measurements``.
    """

    contracts: list[Contract] = field(default_factory=list)
    measurement_groups: list[DeviceMeasurementGroup] = field(default_factory=list)

    @property
    def inverters(self) -> list[PvInverter]:
        """Return a view per inverter."""
        return [PvInverter(contract) for contract in self.contracts]

    @property
    def yield_today(self) -> int | None:
        """Return today's combined yield, in Wh."""
        return self.measurement_groups[0].total if self.measurement_groups else None

    @property
    def yield_today_kwh(self) -> Decimal | None:
        """Return today's combined yield, in kWh."""
        return _kwh(self.yield_today)


@dataclass(slots=True)
class Battery:
    """Typed view on a home_battery_installation contract's meta block.

    ``power_ac`` is positive while charging. Energy values are in Wh, money in
    1e-7 EUR (see the ``*_kwh`` and euro properties).
    """

    contract: Contract

    @property
    def uuid(self) -> str:
        """Return the battery's contract UUID."""
        return self.contract.uuid

    @property
    def model_name(self) -> str | None:
        """Return the battery model."""
        return self.contract.model_name

    @property
    def battery_state(self) -> BatteryState | None:
        """Return the battery state, or ``None`` when it's missing or unknown.

        The raw value stays in ``contract.meta["battery_state"]``.
        """
        return _battery_state(self.contract.meta.get("battery_state"))

    @property
    def inverter_state(self) -> BatteryState | None:
        """Return the battery inverter's state, or ``None`` when it's missing or unknown.

        The raw value stays in ``contract.meta["inverter_state"]``.
        """
        return _battery_state(self.contract.meta.get("inverter_state"))

    @property
    def state_of_charge(self) -> int | None:
        """Return the state of charge, in permille."""
        return self.contract.meta.get("state_of_charge")

    @property
    def state_of_charge_percent(self) -> Decimal | None:
        """Return the state of charge, in percent."""
        value = self.state_of_charge
        return None if value is None else Decimal(value) / 10

    @property
    def power_ac(self) -> int | None:
        """Return the current AC power, in W (positive while charging)."""
        return self.contract.meta.get("power_ac")

    @property
    def is_charging(self) -> bool | None:
        """Return whether the battery is currently charging."""
        return None if self.power_ac is None else self.power_ac > 0

    @property
    def cycle_count(self) -> int | None:
        """Return the number of full charge cycles."""
        return self.contract.meta.get("cycle_count")

    @property
    def first_measured_at(self) -> datetime | None:
        """Return when the battery first reported."""
        return _datetime(self.contract.meta.get("first_measured_at"))

    @property
    def last_measured_at(self) -> datetime | None:
        """Return when the battery last reported."""
        return _datetime(self.contract.meta.get("last_measured_at"))

    @property
    def total_earned(self) -> Decimal | None:
        """Return the lifetime trading result, in EUR."""
        return _euro(self.contract.meta.get("total_earned"))

    @property
    def total_day(self) -> Decimal | None:
        """Return today's trading result, in EUR."""
        return _euro(self.contract.meta.get("total_day"))

    @property
    def average_day(self) -> Decimal | None:
        """Return the average daily trading result, in EUR."""
        return _euro(self.contract.meta.get("average_day"))

    @property
    def delivery_day_kwh(self) -> Decimal | None:
        """Return the energy charged today, in kWh."""
        return _kwh(self.contract.meta.get("delivery_day"))

    @property
    def production_day_kwh(self) -> Decimal | None:
        """Return the energy discharged today, in kWh."""
        return _kwh(self.contract.meta.get("production_day"))

    @property
    def backup_power_capable(self) -> bool | None:
        """Return whether the battery supports backup power ("noodstroom")."""
        return self.contract.meta.get("backup_power_capable")

    @property
    def backup_power_active(self) -> bool | None:
        """Return whether the battery is supplying backup power right now."""
        return self.contract.meta.get("backup_power_active")

    @property
    def backup_power_usable_capacity_wh(self) -> int | None:
        """Return the most energy that can be reserved for backup power, in Wh."""
        return self.contract.meta.get("backup_power_usable_capacity_wh")

    @property
    def reserve_discharge_cutoff_wh(self) -> int | None:
        """Return the energy currently reserved for backup power, in Wh."""
        return self.contract.meta.get("reserve_discharge_cutoff_wh")

    @property
    def dynamic_charging_enabled(self) -> bool | None:
        """Return whether dynamic charging (trading on prices) is enabled."""
        return self.contract.meta.get("dynamic_charging_enabled")

    @property
    def self_consumption_enabled(self) -> bool | None:
        """Return whether self consumption mode is enabled."""
        return self.contract.meta.get("self_consumption_enabled")

    @property
    def home_optimization_enabled(self) -> bool | None:
        """Return whether home optimization mode is enabled."""
        return self.contract.meta.get("home_optimization_enabled")

    @property
    def home_optimization_active(self) -> bool | None:
        """Return whether home optimization is steering the battery right now."""
        return self.contract.meta.get("home_optimization_active")

    @property
    def manual_control_enabled(self) -> bool | None:
        """Return whether the battery is under manual control."""
        return self.contract.meta.get("manual_control_enabled")

    @property
    def dynamic_load_balancing_enabled(self) -> bool | None:
        """Return whether dynamic load balancing is enabled."""
        return self.contract.meta.get("dynamic_load_balancing_enabled")

    @property
    def dynamic_load_balancing_overload_active(self) -> bool | None:
        """Return whether load balancing is limiting the battery right now."""
        return self.contract.meta.get("dynamic_load_balancing_overload_active")

    @property
    def grid_congestion_active(self) -> bool | None:
        """Return whether grid congestion is limiting the battery right now."""
        return self.contract.meta.get("grid_congestion_active")


@dataclass
class BatteryInstallation(DataClassORJSONMixin):
    """Response of /connections/{uuid}/home-battery-installation/{contract_uuid}."""

    contracts: list[Contract] = field(default_factory=list)
    measurement_groups: list[DeviceMeasurementGroup] = field(default_factory=list)

    @property
    def battery(self) -> Battery | None:
        """Return the requested battery."""
        return Battery(self.contracts[0]) if self.contracts else None


@dataclass
class BatteryControlModeOption(DataClassORJSONMixin):
    """Whether one battery control mode is available and enabled."""

    enabled: bool = False
    available: bool = False


@dataclass
class BatteryControlMode(DataClassORJSONMixin):
    """Response of /api/contracts/{uuid}/home-battery/control-mode.

    ``control_mode`` is one of :class:`pyzonneplan.const.BatteryMode`.
    ``processing`` stays true after a change until the battery confirms it.
    """

    control_mode: str | None = None
    modes: dict[str, BatteryControlModeOption] = field(default_factory=dict)
    processing: bool = False

    @property
    def available_modes(self) -> list[str]:
        """Return the modes this battery supports."""
        return [mode for mode, option in self.modes.items() if option.available]

    def is_enabled(self, mode: str) -> bool:
        """Return whether a mode is enabled."""
        option = self.modes.get(mode)
        return option is not None and option.enabled


@dataclass
class BatteryPowerLimits(DataClassORJSONMixin):
    """The range a battery power setting accepts, in W."""

    min_watts: int | None = None
    max_watts: int | None = None


@dataclass
class BatteryHomeOptimization(DataClassORJSONMixin):
    """Response of the home_optimization control-mode endpoint.

    The most power (W) home optimization may charge and discharge with, and
    the range each setting accepts.
    """

    max_desired_charge_power_watts: int | None = None
    max_desired_charge_power_limits: BatteryPowerLimits | None = None
    max_desired_discharge_power_watts: int | None = None
    max_desired_discharge_power_limits: BatteryPowerLimits | None = None


@dataclass
class BatteryChartMeasurement(DataClassORJSONMixin):
    """One day or month of a battery chart."""

    start: datetime
    result: int
    delivery: int
    production: int

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any]) -> dict[str, Any]:
        """Flatten ``meta`` and rename ``measured_at``/``value``."""
        meta = d.get("meta") or {}
        return {
            "start": d["measured_at"],
            "result": d.get("value") or 0,
            "delivery": meta.get("delivery") or 0,
            "production": meta.get("production") or 0,
        }

    @property
    def result_euro(self) -> Decimal:
        """Return the trading result, in EUR."""
        return Decimal(self.result) * MONEY_FACTOR

    @property
    def delivery_kwh(self) -> Decimal:
        """Return the energy charged, in kWh."""
        return Decimal(self.delivery) * WH_TO_KWH

    @property
    def production_kwh(self) -> Decimal:
        """Return the energy discharged, in kWh."""
        return Decimal(self.production) * WH_TO_KWH


@dataclass
class BatteryChart(DataClassORJSONMixin):
    """A chart of /contracts/{uuid}/home_battery_installation/charts/{interval}.

    fsaris reads the first element of the response's ``data`` as the chart
    (unconfirmed). A ``days`` chart covers the month of the requested date, a ``months``
    chart its year. ``total`` is the trading result (1e-7 EUR), ``meta``
    holds the energy charged (``delivery``) and discharged (``production``) in Wh.
    """

    total: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    measurements: list[BatteryChartMeasurement] = field(default_factory=list)

    @property
    def result_euro(self) -> Decimal | None:
        """Return the trading result over the chart, in EUR."""
        return _euro(self.total)

    @property
    def delivery_kwh(self) -> Decimal | None:
        """Return the energy charged over the chart, in kWh."""
        return _kwh(self.meta.get("delivery"))

    @property
    def production_kwh(self) -> Decimal | None:
        """Return the energy discharged over the chart, in kWh."""
        return _kwh(self.meta.get("production"))


@dataclass
class ChargeSchedule(DataClassORJSONMixin):
    """A single planned charging window."""

    start_time: datetime | None = field(default=None, metadata=_DATETIME)
    end_time: datetime | None = field(default=None, metadata=_DATETIME)


@dataclass
class DynamicChargingConstraints(DataClassORJSONMixin):
    """User constraints of a dynamic charging session.

    ``desired_additional_battery_percentage`` is in permille.
    """

    desired_distance_in_kilometers: int | None = None
    desired_additional_battery_percentage: int | None = None
    desired_end_time: datetime | None = field(default=None, metadata=_DATETIME)


@dataclass
class ChargePointSession(DataClassORJSONMixin):
    """The currently running or last charging session."""

    start_time: datetime | None = field(default=None, metadata=_DATETIME)
    charged_distance_in_kilometers: int | None = None


@dataclass
class ChargePointState(DataClassORJSONMixin):
    """The ``state`` block of a charge point.

    ``state`` is ``Standby``, ``VehicleDetected``, ``Charging`` or ``Error``.
    ``processing`` stays true after an action until the charge point confirms it.
    """

    state: str | None = None
    processing: bool = False
    connectivity_state: bool | None = None
    power_actual: int | None = None
    energy_delivered_session: int | None = None
    start_mode: str | None = None
    dynamic_load_balancing_health: str | None = None
    can_charge: bool | None = None
    can_schedule: bool | None = None
    charging_manually: bool | None = None
    charging_automatically: bool | None = None
    plug_and_charge: bool | None = None
    overload_protection_active: bool | None = None
    dynamic_charging_enabled: bool | None = None
    charge_on_solar_enabled: bool | None = None
    dynamic_charging_flex_enabled: bool | None = None
    dynamic_charging_flex_suppressed: bool | None = None
    dynamic_charging_user_constraints: DynamicChargingConstraints | None = None
    charge_point_session: ChargePointSession | None = None

    @property
    def energy_delivered_session_kwh(self) -> Decimal | None:
        """Return the energy charged in the current session, in kWh."""
        return _kwh(self.energy_delivered_session)


@dataclass
class ChargePoint(Contract):
    """A charge_point_installation contract with its live ``state`` and planned schedules."""

    state: ChargePointState = field(default_factory=ChargePointState)
    charge_schedules: list[ChargeSchedule] = field(default_factory=list)

    @property
    def session_cost(self) -> Decimal | None:
        """Return the cost of the current session, in EUR."""
        return _euro(self.meta.get("session_charging_cost_total"))

    @property
    def charging_cost_total(self) -> Decimal | None:
        """Return the lifetime charging cost, in EUR."""
        return _euro(self.meta.get("charging_cost_total"))

    @property
    def session_flex_result(self) -> Decimal | None:
        """Return the current session's flex result, in EUR."""
        return _euro(self.meta.get("session_flex_result"))

    @property
    def session_average_cost(self) -> Decimal | None:
        """Return the current session's average price, in EUR/kWh.

        The API calls it ``session_average_cost_in_cents``, but it is in
        1e-7 EUR like every other amount.
        """
        return _euro(self.meta.get("session_average_cost_in_cents"))

    @property
    def next_schedule(self) -> ChargeSchedule | None:
        """Return the next planned charging window."""
        return self.charge_schedules[0] if self.charge_schedules else None


@dataclass
class Vehicle(DataClassORJSONMixin):
    """A vehicle registered for dynamic charging."""

    uuid: str
    label: str | None = None
    battery_capacity_useable_wh: int | None = None
    consumption_wh_per_km: int | None = None

    @property
    def range_km(self) -> int | None:
        """Return the range of a full battery, in km."""
        if not self.battery_capacity_useable_wh or not self.consumption_wh_per_km:
            return None
        return self.battery_capacity_useable_wh // self.consumption_wh_per_km


@dataclass
class ChargePointInstallation(DataClassORJSONMixin):
    """Response of /connections/{uuid}/charge-points/{contract_uuid}."""

    contracts: list[ChargePoint] = field(default_factory=list)
    vehicles: list[Vehicle] = field(default_factory=list)

    @property
    def charge_point(self) -> ChargePoint | None:
        """Return the requested charge point."""
        return self.contracts[0] if self.contracts else None
