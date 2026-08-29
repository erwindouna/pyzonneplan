"""Models for the PV, battery and charge point endpoints.

Note that most *static* device metadata (inverter model, panel count, firmware,
DSMR version, last measured values) does not come from these endpoints at all:
it lives in ``contract.meta`` on /user-accounts/me. The typed views below sit on
top of that dict so callers do not have to know that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from mashumaro.mixins.orjson import DataClassORJSONMixin

from pyzonneplan.const import MONEY_FACTOR, WH_TO_KWH

from .account import Contract


def _euro(value: int | None) -> Decimal | None:
    """Convert a 1e-7 EUR amount to euro."""
    return None if value is None else Decimal(value) * MONEY_FACTOR


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
    def total_earned(self) -> Decimal | None:
        """Return lifetime earnings."""
        return _euro(self.contract.meta.get("total_earned"))

    @property
    def total_day(self) -> Decimal | None:
        """Return today's earnings."""
        return _euro(self.contract.meta.get("total_day"))


@dataclass
class PvTotals(DataClassORJSONMixin):
    """Response of /connections/{uuid}/pv-installation."""

    total_today: int | None = None

    @property
    def yield_today_kwh(self) -> Decimal | None:
        """Return combined yield today across all inverters, in kWh."""
        return None if self.total_today is None else Decimal(self.total_today) * WH_TO_KWH


@dataclass
class Battery(DataClassORJSONMixin):
    """Response of /connections/{uuid}/home-battery-installation/{uuid}."""

    battery_state: str | None = None
    inverter_state: str | None = None
    state_of_charge: int | None = None
    power_ac: int | None = None
    cycle_count: int | None = None
    backup_power_usable_capacity_wh: int | None = None
    first_measured_at: datetime | None = None
    last_measured_at: datetime | None = None
    total_earned: int | None = None
    total_day: int | None = None
    average_day: int | None = None
    delivery_day: int | None = None
    production_day: int | None = None
    dynamic_charging_enabled: bool | None = None
    dynamic_load_balancing_enabled: bool | None = None
    dynamic_load_balancing_overload_active: bool | None = None
    manual_control_enabled: bool | None = None
    self_consumption_enabled: bool | None = None
    home_optimization_enabled: bool | None = None
    home_optimization_active: bool | None = None
    grid_congestion_active: bool | None = None
    backup_power_active: bool | None = None

    @property
    def earned_total(self) -> Decimal | None:
        """Return lifetime trading result."""
        return _euro(self.total_earned)

    @property
    def earned_today(self) -> Decimal | None:
        """Return today's trading result."""
        return _euro(self.total_day)

    @property
    def is_charging(self) -> bool | None:
        """Return whether the battery is currently charging."""
        return None if self.power_ac is None else self.power_ac > 0


@dataclass
class BatteryControlMode(DataClassORJSONMixin):
    """Response of /api/contracts/{uuid}/home-battery/control-mode."""

    control_mode: str | None = None


@dataclass
class BatteryHomeOptimization(DataClassORJSONMixin):
    """Response of the home_optimization control-mode endpoint."""

    max_desired_charge_power_watts: int | None = None
    max_desired_discharge_power_watts: int | None = None


@dataclass
class ChargeSchedule(DataClassORJSONMixin):
    """A single planned charging window."""

    start_time: datetime | None = None
    end_time: datetime | None = None


@dataclass
class DynamicChargingConstraints(DataClassORJSONMixin):
    """User constraints for a dynamic charging session."""

    desired_distance_in_kilometers: int | None = None
    desired_additional_battery_percentage: int | None = None
    desired_end_time: datetime | None = None


@dataclass
class ChargePointSession(DataClassORJSONMixin):
    """The currently running or last charging session."""

    start_time: datetime | None = None
    charged_distance_in_kilometers: int | None = None


@dataclass
class ChargePoint(DataClassORJSONMixin):
    """Response of /connections/{uuid}/charge-points/{uuid}."""

    state: str | None = None
    start_mode: str | None = None
    power_actual: int | None = None
    energy_delivered_session: int | None = None
    session_charging_cost_total: int | None = None
    charging_cost_total: int | None = None
    session_flex_result: int | None = None
    session_average_cost_in_cents: int | None = None
    dynamic_load_balancing_health: str | None = None
    selected_vehicle: str | None = None
    connectivity_state: bool | None = None
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
    charge_schedules: list[ChargeSchedule] = field(default_factory=list)
    charge_point_session: ChargePointSession | None = None
    dynamic_charging_user_constraints: DynamicChargingConstraints | None = None

    @property
    def session_cost(self) -> Decimal | None:
        """Return the cost of the running session."""
        return _euro(self.session_charging_cost_total)

    @property
    def next_schedule(self) -> ChargeSchedule | None:
        """Return the next planned charging window."""
        return self.charge_schedules[0] if self.charge_schedules else None


@dataclass
class BatteryChart(DataClassORJSONMixin):
    """Response of the battery chart endpoints.

    Shape still needs a captured payload; the raw body is kept meanwhile.
    """

    raw: dict[str, Any] = field(default_factory=dict)
