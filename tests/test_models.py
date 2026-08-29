"""Tests for pyzonneplan.models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pyzonneplan.const import ContractType
from pyzonneplan.models.account import Account, Address, AddressGroup, Connection, Contract, UserAccount
from pyzonneplan.models.devices import Battery, ChargePoint, ChargeSchedule, PvInverter, PvTotals


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
