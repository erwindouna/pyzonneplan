"""Tests for pyzonneplan.models."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import orjson
import pytest

from pyzonneplan.const import (
    BatteryMode,
    BatteryPowerPreset,
    BatteryState,
    BatteryUnavailabilityReason,
    ChargePointStartMode,
    ChargePointStatus,
    ContractType,
    MarketTimeResolution,
    MeasurementGroupType,
    TariffGroup,
)
from pyzonneplan.models.account import Account, Address, AddressGroup, Connection, ConnectionFeature, Contract, UserAccount
from pyzonneplan.models.consumption import ElectricityChart, ElectricityDelivered, Gas, GasChart
from pyzonneplan.models.devices import (
    Battery,
    BatteryChart,
    BatteryControlMode,
    BatteryDayAheadArbitrage,
    BatteryHomeOptimization,
    BatteryInstallation,
    BatteryPowerPresetOption,
    ChargePoint,
    ChargePointInstallation,
    ChargeSchedule,
    P1Installation,
    P1Meter,
    PvInstallation,
    Vehicle,
)
from pyzonneplan.models.prices import ConsumerPrices, Money, PriceChartData, PricePoint, PriceRange, PriceSeries
from pyzonneplan.models.summary import Summary, SummaryUsage

from . import load_fixtures

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def _contract(contract_type: str, *, end_date: datetime | None = None, meta: dict[str, Any] | None = None) -> Contract:
    return Contract(uuid="c-1", type=contract_type, end_date=end_date, meta=meta or {})


def _address() -> Address:
    return Address(
        id="addr-1",
        street="Teststraat",
        number="1",
        zipcode="1234AB",
        city="Amsterdam",
        sunrise=datetime.now(UTC),
        sunset=datetime.now(UTC),
    )


def test_contract_is_active_without_end_date() -> None:
    """A contract with no end date is always active."""
    assert _contract(ContractType.PV_INSTALLATION).is_active is True


def test_contract_is_active_with_future_end_date() -> None:
    """A contract ending in the future is still active."""
    contract = _contract(ContractType.PV_INSTALLATION, end_date=datetime.now(UTC) + timedelta(days=1))
    assert contract.is_active is True


def test_contract_is_inactive_with_past_end_date() -> None:
    """A contract that already ended is not active."""
    contract = _contract(ContractType.PV_INSTALLATION, end_date=datetime.now(UTC) - timedelta(days=1))
    assert contract.is_active is False


def test_contract_model_name_prefers_host_device() -> None:
    """model_name falls back from host device to charge point naming."""
    assert _contract("x", meta={"host_device_model_name": "Inverter X"}).model_name == "Inverter X"
    assert _contract("x", meta={"charge_point_model_name": "Wallbox"}).model_name == "Wallbox"
    assert _contract("x").model_name is None


def test_contract_serial_number_prefers_serial() -> None:
    """serial_number falls back from serial_number to identifier."""
    assert _contract("x", meta={"serial_number": "SN1"}).serial_number == "SN1"
    assert _contract("x", meta={"identifier": "ID1"}).serial_number == "ID1"
    assert _contract("x").serial_number is None


def test_connection_contracts_of_type_filters_inactive_by_default() -> None:
    """Inactive contracts are excluded unless explicitly requested."""
    active = _contract(ContractType.PV_INSTALLATION)
    inactive = _contract(ContractType.PV_INSTALLATION, end_date=datetime.now(UTC) - timedelta(days=1))
    connection = Connection(uuid="conn-1", contracts=[active, inactive])

    assert connection.contracts_of_type(ContractType.PV_INSTALLATION) == [active]
    assert connection.contracts_of_type(ContractType.PV_INSTALLATION, active_only=False) == [active, inactive]


def test_connection_has_flags() -> None:
    """The has_* properties reflect the contracts present on the connection."""
    connection = Connection(
        uuid="conn-1",
        contracts=[
            _contract(ContractType.PV_INSTALLATION),
            _contract(ContractType.HOME_BATTERY),
        ],
    )

    assert connection.has_pv is True
    assert connection.has_battery is True
    assert connection.has_p1 is False
    assert connection.has_charge_point is False


def test_connection_has_gas_meter() -> None:
    """has_gas_meter looks at the P1 contract's gas_last_measured_at meta key."""
    with_gas = Connection(
        uuid="conn-1",
        contracts=[_contract(ContractType.P1_INSTALLATION, meta={"gas_last_measured_at": "2026-01-01"})],
    )
    without_gas = Connection(
        uuid="conn-2",
        contracts=[_contract(ContractType.P1_INSTALLATION)],
    )

    assert with_gas.has_gas_meter is True
    assert without_gas.has_gas_meter is False


def test_account_connections_flattens_address_groups() -> None:
    """Account.connections collects connections across all address groups."""
    connection_a = Connection(uuid="conn-a")
    connection_b = Connection(uuid="conn-b")
    account = Account(
        user_account=UserAccount(
            uuid="user-1",
            email="user@example.com",
            first_name="Test",
            full_name="Test User",
            initials="TU",
        ),
        address_groups=[
            AddressGroup(uuid="ag-1", address=_address(), connections=[connection_a]),
            AddressGroup(uuid="ag-2", address=_address(), connections=[connection_b]),
        ],
    )

    assert account.connections == [connection_a, connection_b]


def _properties(obj: object) -> dict[str, Any]:
    """Return every property of a model, for a snapshot of its typed view."""
    return {name: getattr(obj, name) for name in dir(type(obj)) if isinstance(getattr(type(obj), name), property)}


def _data(fixture: str) -> Any:
    return orjson.loads(load_fixtures(fixture))["data"]


def test_p1_meter_reads_contract_meta(snapshot: SnapshotAssertion) -> None:
    """P1Meter exposes the live readings on a p1_installation contract, with timestamps parsed."""
    meter = P1Meter(
        contract=_contract(
            ContractType.P1_INSTALLATION,
            meta={
                "dsmr_version": "5.0",
                "electricity_last_measured_delivery_value": 450,
                "electricity_last_measured_production_value": 0,
                "electricity_last_measured_average_value": 400,
                "electricity_first_measured_at": "2024-01-01T00:00:00.000000Z",
                "electricity_last_measured_at": "2026-09-29T11:55:00.000000Z",
                "electricity_last_measured_production_at": "2026-09-28T16:00:00.000000Z",
                "gas_first_measured_at": "2024-01-01T00:00:00.000000Z",
                "gas_last_measured_at": None,
            },
        )
    )

    assert meter.electricity_last_measured_at == datetime(2026, 9, 29, 11, 55, tzinfo=UTC)
    assert meter.gas_last_measured_at is None
    assert _properties(meter) == snapshot


def test_p1_meters_from_consumption_summaries() -> None:
    """electricity-delivered and gas carry the P1 contracts with their live readings."""
    electricity = ElectricityDelivered.from_dict(_data("get_electricity_delivered.json"))
    gas = Gas.from_dict(_data("get_gas.json"))

    assert [meter.electricity_delivery for meter in electricity.meters] == [450]
    assert [meter.gas_last_measured_at for meter in gas.meters] == [datetime(2026, 8, 29, 11, 0, tzinfo=UTC)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-29T10:00:00.000000Z", datetime(2026, 9, 29, 10, tzinfo=UTC)),
        ("2026-09-29 12:00:00", datetime(2026, 9, 29, 12, tzinfo=ZoneInfo("Europe/Amsterdam"))),
        ("", None),
        ("not a date", None),
        (None, None),
    ],
    ids=["utc", "naive_is_local", "empty", "invalid", "missing"],
)
def test_device_timestamps(value: str | None, expected: datetime | None) -> None:
    """Device timestamps are timezone-aware, reading a naive one as Amsterdam time, and None when empty or invalid."""
    meter = P1Meter(contract=_contract(ContractType.P1_INSTALLATION, meta={"electricity_last_measured_at": value}))
    schedule = ChargeSchedule.from_dict({"start_time": value})

    assert meter.electricity_last_measured_at == expected
    assert schedule.start_time == expected
    if expected is not None:
        assert meter.electricity_last_measured_at is not None
        assert meter.electricity_last_measured_at.tzinfo is not None


@pytest.mark.parametrize(
    ("now", "offline"),
    [
        (datetime(2026, 10, 13, 10, 14, tzinfo=UTC), False),
        (datetime(2026, 10, 13, 10, 16, tzinfo=UTC), True),
    ],
    ids=["recent", "stale"],
)
def test_p1_installation(now: datetime, *, offline: bool) -> None:
    """P1Installation flattens the reader's details, converts the main fuse to A and is offline after 15 minutes."""
    installation = P1Installation.from_dict(_data("get_p1_installation.json"))

    assert installation.dsmr_version == "50"
    assert installation.number_of_phases == 3
    assert installation.max_current_per_phase_amps == Decimal(25)
    assert installation.load_balancing_status == "HEALTHY"
    assert installation.is_offline(now) is offline


def test_p1_installation_without_readings() -> None:
    """A P1 installation without details has no main fuse and no offline state; a numeric DSMR version is kept as text."""
    installation = P1Installation.from_dict({"host_device": {"dsmr_version": 50}})

    assert installation.dsmr_version == "50"
    assert installation.max_current_per_phase_amps is None
    assert installation.is_offline() is None
    assert P1Installation.from_dict({"measurement_status": {"last_measured_at": "2026-01-01T00:00:00Z"}}).is_offline() is True


def test_pv_installation(snapshot: SnapshotAssertion) -> None:
    """PvInstallation has a view per inverter and today's combined yield."""
    installation = PvInstallation.from_dict(_data("get_pv_installation.json"))

    assert [inverter.panel_count for inverter in installation.inverters] == [9, 6]
    assert installation.yield_today_kwh == Decimal(6)
    assert installation.inverters[0].total_earned == Decimal(25)
    assert installation.inverters[1].total_earned is None
    assert [_properties(inverter) for inverter in installation.inverters] == snapshot


def test_pv_installation_without_measurements() -> None:
    """Without a measurement group there is no yield today."""
    assert PvInstallation().yield_today_kwh is None


def test_battery_installation(snapshot: SnapshotAssertion) -> None:
    """Battery reads the contract meta, converting permille, Wh and 1e-7 EUR."""
    battery = BatteryInstallation.from_dict(_data("get_battery_installation.json")).battery

    assert battery is not None
    assert battery.state_of_charge_percent == Decimal("65.5")
    assert battery.is_charging is False
    assert battery.model_name == "Example Battery 5"
    assert _properties(battery) == snapshot


def test_battery_without_readings() -> None:
    """An empty response has no battery, and a battery without readings reports None."""
    assert BatteryInstallation().battery is None
    battery = Battery(contract=_contract(ContractType.HOME_BATTERY))
    assert battery.state_of_charge_percent is None
    assert battery.is_charging is None
    assert battery.battery_state is None
    assert battery.inverter_state is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Charging", BatteryState.CHARGING),
        ("discharging", BatteryState.DISCHARGING),
        ("OPERATIVE", BatteryState.OPERATIVE),
        ("ShuttingDown", BatteryState.SHUTTING_DOWN),
        ("backup_power_active", BatteryState.BACKUP_POWER_ACTIVE),
        ("UnknownState", BatteryState.UNKNOWN_STATE),
        ("Hibernating", None),
        (None, None),
    ],
    ids=["capitalised", "lowercase", "uppercase", "pascal_case", "snake_case", "unknown_state", "unknown", "missing"],
)
def test_battery_states(value: str | None, expected: BatteryState | None) -> None:
    """Battery and inverter states parse case-insensitively, and are None when missing or unknown."""
    battery = Battery(contract=_contract(ContractType.HOME_BATTERY, meta={"battery_state": value, "inverter_state": value}))

    assert battery.battery_state is expected
    assert battery.inverter_state is expected
    assert battery.contract.meta["battery_state"] == value


def test_battery_unknown_state_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """An unknown state is logged at debug level."""
    battery = Battery(contract=_contract(ContractType.HOME_BATTERY, meta={"battery_state": "Hibernating"}))

    with caplog.at_level(logging.DEBUG, logger="pyzonneplan"):
        assert battery.battery_state is None

    assert "Unknown battery state: Hibernating" in caplog.text


def test_battery_control_mode() -> None:
    """The control mode lists the available modes and which one is enabled."""
    control_mode = BatteryControlMode.from_dict(_data("get_battery_control_mode.json"))

    assert control_mode.control_mode == BatteryMode.SELF_CONSUMPTION
    assert control_mode.available_modes == [BatteryMode.SELF_CONSUMPTION, BatteryMode.HOME_OPTIMIZATION]
    assert control_mode.is_enabled(BatteryMode.SELF_CONSUMPTION) is True
    assert control_mode.is_enabled(BatteryMode.HOME_OPTIMIZATION) is False
    assert control_mode.is_enabled("unknown") is False
    assert control_mode.processing is False
    assert control_mode.modes[BatteryMode.SELF_CONSUMPTION].unavailability_reason_code is None
    assert control_mode.modes[BatteryMode.DAY_AHEAD_ARBITRAGE].unavailability_reason_code is BatteryUnavailabilityReason.DISALLOWED_BY_PROJECT


@pytest.mark.parametrize(
    ("control_mode", "expected"),
    [
        ("home_optimization", BatteryMode.HOME_OPTIMIZATION),
        ("day_ahead_arbitrage", BatteryMode.DAY_AHEAD_ARBITRAGE),
        ("solar_boost", None),
        (None, None),
    ],
    ids=["known", "day_ahead_arbitrage", "unknown", "missing"],
)
def test_battery_control_mode_values(control_mode: str | None, expected: BatteryMode | None) -> None:
    """The control mode parses to BatteryMode, an unknown one to None; unknown modes stay in ``modes`` but aren't offered."""
    parsed = BatteryControlMode.from_dict(
        {
            "control_mode": control_mode,
            "modes": {
                "home_optimization": {"enabled": True, "available": True},
                "solar_boost": {"enabled": False, "available": True, "unavailability_reason_code": "not_yet_known"},
            },
        }
    )

    assert parsed.control_mode is expected
    assert parsed.available_modes == [BatteryMode.HOME_OPTIMIZATION]
    assert parsed.modes["solar_boost"].available is True
    assert parsed.modes["solar_boost"].unavailability_reason_code is None


def test_battery_home_optimization_presets() -> None:
    """Power presets are unwrapped from their ``data`` envelope and typed."""
    optimization = BatteryHomeOptimization.from_dict(_data("get_battery_home_optimization_presets.json"))

    assert optimization.power_preset is BatteryPowerPreset.MEDIUM
    assert optimization.power_presets == [
        BatteryPowerPresetOption(name=BatteryPowerPreset.LOW, max_desired_charge_power_watts=1500, max_desired_discharge_power_watts=1500),
        BatteryPowerPresetOption(name=BatteryPowerPreset.MEDIUM, max_desired_charge_power_watts=3000, max_desired_discharge_power_watts=3000),
        BatteryPowerPresetOption(name=BatteryPowerPreset.HIGH, max_desired_charge_power_watts=5000, max_desired_discharge_power_watts=5000),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"power_preset": None, "power_presets": None},
        {"power_preset": "turbo", "power_presets": {"data": None}},
    ],
    ids=["missing", "null", "unknown_preset"],
)
def test_battery_home_optimization_without_presets(payload: dict[str, Any]) -> None:
    """Older responses without presets, null presets and an unknown preset all parse."""
    optimization = BatteryHomeOptimization.from_dict(_data("get_battery_home_optimization.json") | payload)

    assert optimization.max_desired_charge_power_watts == 2500
    assert optimization.power_preset is None
    assert optimization.power_presets == []


def test_battery_power_preset_unknown_name() -> None:
    """A preset with an unknown name keeps its power settings."""
    optimization = BatteryHomeOptimization.from_dict({"power_presets": {"data": [{"name": "turbo", "max_desired_charge_power_watts": 9000}]}})

    assert optimization.power_presets == [BatteryPowerPresetOption(name=None, max_desired_charge_power_watts=9000)]


@pytest.mark.parametrize(
    ("payload", "price_spread", "resolution"),
    [
        (None, Decimal("0.08"), MarketTimeResolution.QUARTER_HOUR),
        ({"configured_price_spread": None, "market_time_resolution": None}, None, None),
        ({"configured_price_spread": {"amount": 1200000}, "market_time_resolution": {"iso8601": "PT30M"}}, Decimal("0.12"), None),
    ],
    ids=["configured", "not_set_up", "unknown_resolution"],
)
def test_battery_day_ahead_arbitrage(payload: dict[str, Any] | None, price_spread: Decimal | None, resolution: MarketTimeResolution | None) -> None:
    """The price spread is converted to EUR and the resolution parsed; missing or unknown values are None."""
    settings = BatteryDayAheadArbitrage.from_dict(payload if payload is not None else _data("get_battery_day_ahead_arbitrage.json"))

    assert settings.price_spread == price_spread
    assert settings.market_time_resolution is resolution


def test_battery_chart() -> None:
    """A battery chart converts its result and energy totals, per day and in total."""
    chart = BatteryChart.from_dict(_data("get_battery_chart_days.json")[0])

    assert chart.result_euro == Decimal("1.5")
    assert chart.delivery_kwh == Decimal(9)
    assert chart.production_kwh == Decimal("7.5")
    assert [measurement.start for measurement in chart.measurements] == [
        datetime(2026, 8, 31, 22, tzinfo=UTC),
        datetime(2026, 9, 1, 22, tzinfo=UTC),
    ]
    assert chart.measurements[0].result_euro == Decimal("0.5")
    assert chart.measurements[0].delivery_kwh == Decimal(3)
    assert chart.measurements[0].production_kwh == Decimal("2.5")


def test_charge_point_installation(snapshot: SnapshotAssertion) -> None:
    """ChargePoint is a contract with its state and schedules, and the vehicles come along."""
    installation = ChargePointInstallation.from_dict(_data("get_charge_point.json"))
    charge_point = installation.charge_point

    assert charge_point is not None
    assert charge_point.model_name == "Example Charger 11"
    assert charge_point.state.state is ChargePointStatus.CHARGING
    assert charge_point.state.start_mode is ChargePointStartMode.ALWAYS_FLEX
    assert charge_point.state.plugged_in_at == datetime(2026, 9, 29, 9, 55, tzinfo=UTC)
    assert charge_point.state.last_known_vehicle_uuid == installation.vehicles[0].uuid
    assert charge_point.state.energy_delivered_session_kwh == Decimal(10)
    assert charge_point.next_schedule == charge_point.charge_schedules[0]
    assert installation.vehicles[0].range_km == 375
    assert _properties(charge_point) == snapshot


@pytest.mark.parametrize(
    ("state", "start_mode", "expected_state", "expected_start_mode"),
    [
        ("VehicleDetected", "AlwaysFlex", ChargePointStatus.VEHICLE_DETECTED, ChargePointStartMode.ALWAYS_FLEX),
        ("Error", "StartWithApp", ChargePointStatus.ERROR, ChargePointStartMode.START_WITH_APP),
        ("Standby", "PlugAndCharge", ChargePointStatus.STANDBY, ChargePointStartMode.PLUG_AND_CHARGE),
        ("Offline", "Automatic", None, None),
        (None, None, None, None),
    ],
    ids=["vehicle_detected", "error", "standby", "unknown", "missing"],
)
def test_charge_point_state_enums(
    state: str | None,
    start_mode: str | None,
    expected_state: ChargePointStatus | None,
    expected_start_mode: ChargePointStartMode | None,
) -> None:
    """Charge point state and start mode parse to enums, unknown values to None."""
    parsed = ChargePoint.from_dict({"uuid": "cp-1", "type": ContractType.CHARGE_POINT, "state": {"state": state, "start_mode": start_mode}})

    assert parsed.state.state is expected_state
    assert parsed.state.start_mode is expected_start_mode


def test_unknown_enum_value_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """An unknown enum value in a response is logged at debug level."""
    with caplog.at_level(logging.DEBUG, logger="pyzonneplan"):
        ChargePoint.from_dict({"uuid": "cp-1", "type": ContractType.CHARGE_POINT, "state": {"start_mode": "Automatic"}})

    assert "Unknown charge point start mode: Automatic" in caplog.text


def test_charge_point_without_data() -> None:
    """An empty response has no charge point, and a bare one has no schedule, costs or range."""
    assert ChargePointInstallation().charge_point is None
    charge_point = ChargePoint(uuid="c-1", type=ContractType.CHARGE_POINT)
    assert charge_point.next_schedule is None
    assert charge_point.session_cost is None
    assert Vehicle(uuid="v-1").range_km is None


def _price_point(start: datetime, amount: int) -> PricePoint:
    return PricePoint(
        start_date=start,
        end_date=start + timedelta(hours=1),
        price_tax_included=Money(amount=amount),
        price_tax_excluded=Money(amount=amount),
    )


def _consumer_prices(points: list[PricePoint]) -> ConsumerPrices:
    return ConsumerPrices(
        chart=PriceChartData(
            range=PriceRange(start_date=points[0].start_date, end_date=points[-1].end_date),
            series=PriceSeries(prices=points),
        )
    )


def test_consumer_prices_prices_for_day_filters_by_local_day() -> None:
    """prices_for_day buckets points by their local (not UTC) calendar day."""
    tz = ZoneInfo("Europe/Amsterdam")
    before_midnight = _price_point(datetime(2024, 1, 14, 23, 0, tzinfo=UTC), 1)  # 00:00 local on the 15th
    late_in_day = _price_point(datetime(2024, 1, 15, 22, 0, tzinfo=UTC), 2)  # 23:00 local on the 15th
    next_day = _price_point(datetime(2024, 1, 15, 23, 0, tzinfo=UTC), 3)  # 00:00 local on the 16th
    prices = _consumer_prices([before_midnight, late_in_day, next_day])

    assert prices.prices_for_day(date(2024, 1, 15), tz) == [before_midnight, late_in_day]
    assert prices.prices_for_day(date(2024, 1, 16), tz) == [next_day]
    assert prices.prices_for_day(date(2024, 1, 13), tz) == []


def test_consumer_prices_price_at() -> None:
    """price_at returns the point covering the moment, with a half-open interval, and None outside the chart."""
    first = _price_point(datetime(2024, 1, 15, 10, 0, tzinfo=UTC), 1)
    second = _price_point(datetime(2024, 1, 15, 11, 0, tzinfo=UTC), 2)
    prices = _consumer_prices([first, second])

    assert prices.price_at(datetime(2024, 1, 15, 10, 30, tzinfo=UTC)) is first
    assert prices.price_at(datetime(2024, 1, 15, 11, 0, tzinfo=UTC)) is second
    assert prices.price_at(datetime(2024, 1, 15, 12, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))) is second
    assert prices.price_at(datetime(2024, 1, 15, 12, 0, tzinfo=UTC)) is None
    assert prices.price_at(datetime(2024, 1, 15, 9, 59, tzinfo=UTC)) is None


def test_consumer_prices_extreme_price() -> None:
    """extreme_price returns the cheapest or most expensive point for a day, or None."""
    tz = UTC
    day = date(2024, 6, 1)
    cheap = _price_point(datetime(2024, 6, 1, 0, tzinfo=UTC), 100)
    mid = _price_point(datetime(2024, 6, 1, 1, tzinfo=UTC), 500)
    expensive = _price_point(datetime(2024, 6, 1, 2, tzinfo=UTC), 900)
    prices = _consumer_prices([cheap, mid, expensive])

    assert prices.extreme_price(day, tz, lowest=True) is cheap
    assert prices.extreme_price(day, tz, lowest=False) is expensive
    assert prices.extreme_price(date(2024, 6, 2), tz, lowest=True) is None


def test_consumer_prices_price_block() -> None:
    """price_block grows outward from the day's extreme price while staying within 5%."""
    tz = UTC
    day = date(2024, 6, 1)
    points = [
        _price_point(datetime(2024, 6, 1, 0, tzinfo=UTC), 5000),
        _price_point(datetime(2024, 6, 1, 1, tzinfo=UTC), 1020),
        _price_point(datetime(2024, 6, 1, 2, tzinfo=UTC), 1000),
        _price_point(datetime(2024, 6, 1, 3, tzinfo=UTC), 1010),
        _price_point(datetime(2024, 6, 1, 4, tzinfo=UTC), 1200),
        _price_point(datetime(2024, 6, 1, 5, tzinfo=UTC), 6000),
    ]
    prices = _consumer_prices(points)

    assert prices.price_block(day, tz, lowest=True) == (points[1], points[3])
    assert prices.price_block(date(2024, 6, 2), tz, lowest=True) is None


def _electricity_chart(fixture: str) -> ElectricityChart:
    return ElectricityChart.from_dict(orjson.loads(load_fixtures(fixture))["data"])


def _gas_chart(fixture: str) -> GasChart:
    return GasChart.from_dict(orjson.loads(load_fixtures(fixture))["data"])


def test_electricity_chart_hourly_measurements_add_up_to_totals() -> None:
    """Hourly production is negative in the API but positive in the totals; the model makes both positive."""
    group = _electricity_chart("get_electricity_chart_hours.json").group
    assert group is not None

    assert len(group.measurements) == 24
    assert group.measurements[0].start == datetime(2026, 9, 23, 22, 0, tzinfo=UTC)
    assert sum(measurement.delivered for measurement in group.measurements) == group.totals["d"] == 6000
    assert sum(measurement.produced for measurement in group.measurements) == group.totals["p"] == 9000
    assert group.delivered_kwh == Decimal("6.000")
    assert group.produced_kwh == Decimal("9.000")
    assert {measurement.tariff_group for measurement in group.measurements} == set(TariffGroup)
    assert group.type is MeasurementGroupType.HOURS
    assert group.delivery_cost == Decimal("2.1889682")
    assert group.delivery_cost_excl_tax == Decimal("1.5238797")


def test_electricity_chart_months_parses_string_values() -> None:
    """The months chart sends the delivered value as a string."""
    group = _electricity_chart("get_electricity_chart_months.json").group
    assert group is not None

    assert group.measurements[0].delivered == 67000
    assert group.measurements[0].produced_kwh == Decimal("34.500")


def test_gas_chart_drops_the_repeated_trailing_hour() -> None:
    """The stray 25th entry repeats an earlier hour with 0 and must not replace the real value."""
    group = _gas_chart("get_gas_chart_hours.json").group
    assert group is not None

    starts = [measurement.start for measurement in group.measurements]
    assert len(starts) == len(set(starts)) == 24
    midnight_utc = next(measurement for measurement in group.measurements if measurement.start == datetime(2026, 9, 24, 0, 0, tzinfo=UTC))
    assert midnight_utc.volume == 4
    assert sum(measurement.volume for measurement in group.measurements) == group.total == 400
    assert group.total_m3 == Decimal("0.400")
    assert group.type is MeasurementGroupType.HOURS
    assert group.delivery_cost == Decimal("0.6689845")
    assert group.delivery_cost_excl_tax == Decimal("0.3782650")


def test_chart_without_data_yet_reports_no_totals() -> None:
    """Pending days report zeros (today's gas even has 24 zero hours); has_data tells them apart from real zeros."""
    electricity = _electricity_chart("get_electricity_chart_hours_pending.json").group
    gas_pending = _gas_chart("get_gas_chart_hours_pending.json").group
    gas_today = _gas_chart("get_gas_chart_hours_today.json").group
    assert electricity is not None
    assert gas_pending is not None
    assert gas_today is not None

    assert not electricity.has_data
    assert electricity.delivered_kwh is None
    assert electricity.delivery_cost is None
    assert electricity.produced_kwh is None
    assert not gas_pending.has_data
    assert gas_pending.total_m3 is None
    assert gas_pending.delivery_cost is None
    assert len(gas_today.measurements) == 24
    assert not gas_today.has_data
    assert gas_today.total_m3 is None


def test_empty_chart_has_no_group() -> None:
    """A chart without measurement groups has no group."""
    assert ElectricityChart().group is None
    assert GasChart().group is None


def test_summary_price_at() -> None:
    """price_at returns the forecast hour covering the moment, half-open, and None outside the forecast."""
    summary = Summary.from_dict(orjson.loads(load_fixtures("get_summary.json"))["data"])
    first, second = summary.price_per_hour[:2]
    last = summary.price_per_hour[-1]

    assert summary.price_at(first.start) is first
    assert summary.price_at(first.start + timedelta(minutes=59)) is first
    assert summary.price_at(second.start) is second
    assert summary.price_at(datetime(2026, 9, 25, 19, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))) is second
    assert summary.price_at(first.start - timedelta(seconds=1)) is None
    assert summary.price_at(last.end) is None


def test_summary_prices_in_euro() -> None:
    """Prices convert from 1e-7 EUR; the gas price is only set on the hour the gas day starts."""
    summary = Summary.from_dict(orjson.loads(load_fixtures("get_summary.json"))["data"])
    gas_day_start = summary.price_at(datetime(2026, 9, 26, 6, 0, tzinfo=ZoneInfo("Europe/Amsterdam")))

    assert summary.price_per_hour[0].electricity_price_euro == Decimal("0.4217229")
    assert summary.price_per_hour[0].gas_price_euro is None
    assert gas_day_start is not None
    assert gas_day_start.gas_price_euro == Decimal("1.6549336")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("low", TariffGroup.LOW),
        ("Normal", TariffGroup.NORMAL),
        ("HIGH", TariffGroup.HIGH),
        ("extreme", None),
        (None, None),
    ],
    ids=["low", "capitalised", "uppercase", "unknown", "missing"],
)
def test_tariff_groups(value: str | None, expected: TariffGroup | None) -> None:
    """Tariff groups parse to TariffGroup on every model that has one, and unknown ones to None."""
    start = "2026-09-25T16:00:00.000000Z"
    point = PricePoint.from_dict(
        {
            "start_date": start,
            "end_date": "2026-09-25T17:00:00.000000Z",
            "price_tax_included": {"amount": 1},
            "price_tax_excluded": {"amount": 1},
            "tariff_group": value,
        }
    )
    chart = ElectricityChart.from_dict(
        {
            "measurement_groups": [
                {"date": start, "type": "hours", "measurements": [{"date": start, "values": {"d": 1}, "meta": {"tariff_group": value}}]}
            ]
        }
    )
    summary = Summary.from_dict(
        {
            "usage": {"type": value},
            "price_per_hour": [{"datetime": start, "electricity_price": 1, "electricity_price_excl_tax": 1, "tariff_group": value}],
        }
    )

    assert chart.group is not None
    assert point.tariff_group is expected
    assert chart.group.measurements[0].tariff_group is expected
    assert summary.usage.type is expected
    assert summary.price_per_hour[0].tariff_group is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("live", MeasurementGroupType.LIVE),
        ("minutes", MeasurementGroupType.MINUTES),
        ("days", MeasurementGroupType.DAYS),
        ("weeks", None),
    ],
    ids=["live", "minutes", "days", "unknown"],
)
def test_measurement_group_types(value: str, expected: MeasurementGroupType | None) -> None:
    """Chart group types parse to MeasurementGroupType, an unknown one to None."""
    electricity = ElectricityChart.from_dict({"measurement_groups": [{"date": "2026-09-01T00:00:00Z", "type": value}]})
    gas = GasChart.from_dict({"measurement_groups": [{"date": "2026-09-01T00:00:00Z", "type": value}]})

    assert electricity.group is not None
    assert gas.group is not None
    assert electricity.group.type is expected
    assert gas.group.type is expected


def test_summary_usage_defaults() -> None:
    """A summary without usage has no tariff group."""
    assert SummaryUsage().type is None


def test_consumption_summary_dates() -> None:
    """The consumption summaries' window dates are timezone-aware datetimes."""
    electricity = ElectricityDelivered.from_dict({"measurement_groups": [{"date": "2026-09-29T22:00:00.000000Z", "totals": {"d": 1000}}]})
    gas = Gas.from_dict({"measurement_groups": [{"date": "2026-09-29", "total": 1}, {"date": "garbage"}]})

    assert electricity.measurement_groups[0].date == datetime(2026, 9, 29, 22, tzinfo=UTC)
    assert gas.measurement_groups[0].date == datetime(2026, 9, 29, tzinfo=ZoneInfo("Europe/Amsterdam"))
    assert gas.measurement_groups[1].date is None


def test_account_features() -> None:
    """Connection features are typed, and a missing first name is allowed."""
    account = Account.from_dict(_data("get_account_no_p1.json"))
    electricity = account.connections[0]

    assert account.user_account.first_name is None
    assert electricity.features[0] == ConnectionFeature(code="E004", label="Verbruikshistorie", start_date=date(2026, 1, 1))
    assert [feature.code for feature in electricity.features] == ["E004", "S003", "E005"]
