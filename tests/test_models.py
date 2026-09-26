"""Tests for pyzonneplan.models."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import orjson

from pyzonneplan.const import ContractType
from pyzonneplan.models.account import Account, Address, AddressGroup, Connection, Contract, UserAccount
from pyzonneplan.models.consumption import ElectricityChart, GasChart
from pyzonneplan.models.devices import Battery, ChargePoint, ChargeSchedule, PvInverter, PvTotals
from pyzonneplan.models.prices import ConsumerPrices, Money, PriceChartData, PricePoint, PriceRange, PriceSeries

from . import load_fixtures


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


def test_pv_inverter_reads_contract_meta() -> None:
    """PvInverter exposes the static metadata carried on the contract."""
    inverter = PvInverter(
        contract=_contract(
            ContractType.PV_INSTALLATION,
            meta={
                "inverter_model_name": "SolarEdge SE5000",
                "panel_count": 12,
                "total_earned": 12345,
            },
        )
    )

    assert inverter.uuid == "c-1"
    assert inverter.model_name == "SolarEdge SE5000"
    assert inverter.panel_count == 12
    assert inverter.total_earned == Decimal("0.0012345")


def test_pv_totals_yield_today_kwh() -> None:
    """yield_today_kwh converts the raw Wh total to kWh."""
    assert PvTotals(total_today=1500).yield_today_kwh == Decimal("1.5")
    assert PvTotals(total_today=None).yield_today_kwh is None


def test_battery_is_charging() -> None:
    """is_charging reflects the sign of power_ac."""
    assert Battery(power_ac=100).is_charging is True
    assert Battery(power_ac=-100).is_charging is False
    assert Battery(power_ac=None).is_charging is None


def test_battery_earned_properties() -> None:
    """earned_total/earned_today convert the raw 1e-7 EUR amounts."""
    battery = Battery(total_earned=10_000_000, total_day=5_000_000)
    assert battery.earned_total == Decimal(1)
    assert battery.earned_today == Decimal("0.5")


def test_charge_point_next_schedule() -> None:
    """next_schedule returns the first schedule, or None when there is none."""
    schedule = ChargeSchedule(start_time=datetime.now(UTC), end_time=datetime.now(UTC))
    assert ChargePoint(charge_schedules=[schedule]).next_schedule is schedule
    assert ChargePoint().next_schedule is None


def test_charge_point_session_cost() -> None:
    """session_cost converts the raw 1e-7 EUR session total."""
    assert ChargePoint(session_charging_cost_total=20_000_000).session_cost == Decimal(2)
    assert ChargePoint().session_cost is None


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
    assert sum(measurement.delivered for measurement in group.measurements) == group.totals["d"] == 4987
    assert sum(measurement.produced for measurement in group.measurements) == group.totals["p"] == 11498
    assert group.delivered_kwh == Decimal("4.987")
    assert group.produced_kwh == Decimal("11.498")
    assert {measurement.tariff_group for measurement in group.measurements} == {"low", "normal", "high"}


def test_electricity_chart_months_parses_string_values() -> None:
    """The months chart sends the delivered value as a string."""
    group = _electricity_chart("get_electricity_chart_months.json").group
    assert group is not None

    assert group.measurements[0].delivered == 81633
    assert group.measurements[0].produced_kwh == Decimal("36.874")


def test_gas_chart_drops_the_repeated_trailing_hour() -> None:
    """The stray 25th entry repeats an earlier hour with 0 and must not replace the real value."""
    group = _gas_chart("get_gas_chart_hours.json").group
    assert group is not None

    starts = [measurement.start for measurement in group.measurements]
    assert len(starts) == len(set(starts)) == 24
    midnight_utc = next(measurement for measurement in group.measurements if measurement.start == datetime(2026, 9, 24, 0, 0, tzinfo=UTC))
    assert midnight_utc.volume == 3
    assert sum(measurement.volume for measurement in group.measurements) == group.total == 278
    assert group.total_m3 == Decimal("0.278")


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
