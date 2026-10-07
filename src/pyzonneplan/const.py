"""Constants for the Zonneplan API client."""

from decimal import Decimal
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

API_SCHEME: Final = "https"
API_URL: Final = "app-api.zonneplan.nl"

AUTHORIZE_CHALLENGE_PATH: Final = "oauth/authorize-challenge"
TOKEN_PATH: Final = "oauth/token"  # noqa: S105 (a URL path, not a credential)

# Sent by the mobile app; the API rejects requests without them.
APP_VERSION: Final = "5.10.1"
APP_ENVIRONMENT: Final = "production"

# The API's local time, e.g. for the charge point actions' end times.
API_TIMEZONE: Final = ZoneInfo("Europe/Amsterdam")

# Every monetary "amount" in the API is expressed in 1e-7 EUR.
MONEY_FACTOR: Final = Decimal("0.0000001")

# Energy values are Wh, power values are W.
WH_TO_KWH: Final = Decimal("0.001")


class ChartInterval:
    """Supported battery chart intervals."""

    DAYS: Final = "days"
    MONTHS: Final = "months"


class BatteryMode:
    """Home battery control modes.

    ``DYNAMIC_CHARGING`` (trading on prices) is what the battery does with the
    other two modes disabled.
    """

    SELF_CONSUMPTION: Final = "self_consumption"
    HOME_OPTIMIZATION: Final = "home_optimization"
    DYNAMIC_CHARGING: Final = "dynamic_charging"


class BatteryState(StrEnum):
    """Home battery and battery inverter states.

    The API sends them capitalised (``Charging``); the models parse them case-insensitively.
    """

    CHARGING = "charging"
    DISCHARGING = "discharging"
    OPERATIVE = "operative"


class ConsumptionChart:
    """Supported consumption chart intervals.

    ``HOURS`` covers the local day of the requested date, ``DAYS`` its month
    and ``MONTHS`` its year.
    """

    HOURS: Final = "hours"
    DAYS: Final = "days"
    MONTHS: Final = "months"


class PriceChart:
    """Supported consumer price charts."""

    ELECTRICITY_HOURLY: Final = "electricity-hourly"
    ELECTRICITY_QUARTER_HOURLY: Final = "electricity-quarter-hourly"
    GAS_DAILY: Final = "gas-daily"


class ContractType:
    """Contract types returned by /user-accounts/me."""

    ELECTRICITY: Final = "electricity"
    GAS: Final = "gas"
    PV_INSTALLATION: Final = "pv_installation"
    P1_INSTALLATION: Final = "p1_installation"
    CHARGE_POINT: Final = "charge_point_installation"
    HOME_BATTERY: Final = "home_battery_installation"
