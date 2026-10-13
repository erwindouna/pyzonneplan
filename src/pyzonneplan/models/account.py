"""Models for /user-accounts/me and /user-accounts/app-settings."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from mashumaro.mixins.orjson import DataClassORJSONMixin

from pyzonneplan.const import ContractType, PriceResolution

from ._parse import parse_enum


@dataclass
class Contract(DataClassORJSONMixin):
    """A single product on a connection (PV, P1, battery, charge point, ...)."""

    uuid: str
    type: str
    label: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Return whether the contract has not ended yet."""
        return self.end_date is None or self.end_date > datetime.now(self.end_date.tzinfo)

    @property
    def model_name(self) -> str | None:
        """Return the hardware model, where the API exposes one."""
        return self.meta.get("host_device_model_name") or self.meta.get("charge_point_model_name")

    @property
    def serial_number(self) -> str | None:
        """Return the hardware serial, where the API exposes one."""
        return self.meta.get("serial_number") or self.meta.get("identifier")


@dataclass
class ConnectionFeature(DataClassORJSONMixin):
    """A feature enabled on a connection, e.g. ``E004`` "Verbruikshistorie" (usage history)."""

    code: str
    label: str | None = None
    start_date: date | None = None


@dataclass
class Connection(DataClassORJSONMixin):
    """A grid connection and the contracts attached to it."""

    uuid: str
    ean: str | None = None
    market_segment: str | None = None
    contracts: list[Contract] = field(default_factory=list)
    features: list[ConnectionFeature] = field(default_factory=list)

    def contracts_of_type(self, contract_type: str, *, active_only: bool = True) -> list[Contract]:
        """Return the contracts of a given type."""
        return [contract for contract in self.contracts if contract.type == contract_type and (not active_only or contract.is_active)]

    def has(self, contract_type: str) -> bool:
        """Return whether an active contract of this type exists."""
        return bool(self.contracts_of_type(contract_type))

    @property
    def has_pv(self) -> bool:
        """Return whether a solar installation is present."""
        return self.has(ContractType.PV_INSTALLATION)

    @property
    def has_p1(self) -> bool:
        """Return whether a P1 reader is present."""
        return self.has(ContractType.P1_INSTALLATION)

    @property
    def has_battery(self) -> bool:
        """Return whether a home battery is present."""
        return self.has(ContractType.HOME_BATTERY)

    @property
    def has_charge_point(self) -> bool:
        """Return whether a charge point is present."""
        return self.has(ContractType.CHARGE_POINT)

    @property
    def has_gas_meter(self) -> bool:
        """Return whether any P1 contract has recently reported gas."""
        return any(contract.meta.get("gas_last_measured_at") for contract in self.contracts_of_type(ContractType.P1_INSTALLATION))


@dataclass
class Address(DataClassORJSONMixin):
    """Postal address of an address group."""

    id: str
    street: str
    number: str
    zipcode: str
    city: str
    sunrise: datetime
    sunset: datetime
    addition: str | None = None


@dataclass
class AddressGroup(DataClassORJSONMixin):
    """One address, with one or more connections."""

    uuid: str
    address: Address
    connections: list[Connection] = field(default_factory=list)
    is_representative: bool = False
    organization_uuid: str | None = None


@dataclass
class UserAccount(DataClassORJSONMixin):
    """The authenticated Zonneplan account."""

    uuid: str
    email: str
    full_name: str
    first_name: str | None = None
    initials: str | None = None
    is_representative: bool = False


@dataclass
class Account(DataClassORJSONMixin):
    """Top level response of /user-accounts/me."""

    user_account: UserAccount
    address_groups: list[AddressGroup] = field(default_factory=list)

    @property
    def connections(self) -> list[Connection]:
        """Return every connection across every address group."""
        return [connection for address_group in self.address_groups for connection in address_group.connections]


@dataclass
class AppSettings(DataClassORJSONMixin):
    """Response of /user-accounts/app-settings: the app settings Zonneplan stores for the account.

    ``settings`` keeps every setting the API sends, keyed by its raw name,
    also ones the library doesn't know yet.
    """

    settings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def __pre_deserialize__(cls, d: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
        """Turn the API's ``[{"key": ..., "value": ...}]`` list (or null) into ``settings``."""
        if isinstance(d, dict):
            return d
        return {"settings": {item["key"]: item["value"] for item in d or []}}

    @property
    def price_resolution(self) -> PriceResolution | None:
        """Return the interval the app shows electricity prices in."""
        return parse_enum(PriceResolution, self.settings.get("price_resolution"), "price resolution")
