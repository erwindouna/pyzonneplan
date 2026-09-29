"""Tests for pyzonneplan.models."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import orjson

from pyzonneplan.const import BatteryMode, ContractType
from pyzonneplan.models.account import Account, Address, AddressGroup, Connection, Contract, UserAccount
from pyzonneplan.models.consumption import ElectricityChart, ElectricityDelivered, Gas, GasChart
from pyzonneplan.models.devices import (
    Battery,
    BatteryChart,
    BatteryControlMode,
    BatteryInstallation,
    ChargePoint,
    ChargePointInstallation,
    P1Meter,
    PvInstallation,
    Vehicle,
)
from pyzonneplan.models.prices import ConsumerPrices, Money, PriceChartData, PricePoint, PriceRange, PriceSeries
from pyzonneplan.models.summary import Summary

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


def test_battery_control_mode() -> None:
    """The control mode lists the available modes and which one is enabled."""
    control_mode = BatteryControlMode.from_dict(_data("get_battery_control_mode.json"))

    assert control_mode.control_mode == BatteryMode.SELF_CONSUMPTION
    assert control_mode.available_modes == [BatteryMode.SELF_CONSUMPTION, BatteryMode.HOME_OPTIMIZATION]
    assert control_mode.is_enabled(BatteryMode.SELF_CONSUMPTION) is True
    assert control_mode.is_enabled(BatteryMode.HOME_OPTIMIZATION) is False
    assert control_mode.is_enabled("unknown") is False
    assert control_mode.processing is False


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
    assert charge_point.state.state == "Charging"
    assert charge_point.state.energy_delivered_session_kwh == Decimal(10)
    assert charge_point.next_schedule == charge_point.charge_schedules[0]
    assert installation.vehicles[0].range_km == 375
    assert _properties(charge_point) == snapshot


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
    assert {measurement.tariff_group for measurement in group.measurements} == {"low", "normal", "high"}


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
    assert electricity.produced_kwh is None
    assert not gas_pending.has_data
    assert gas_pending.total_m3 is None
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
