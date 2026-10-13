"""Constants for the Zonneplan API client."""

from decimal import Decimal
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

API_SCHEME: Final = "https"
API_URL: Final = "app-api.zonneplan.nl"

AUTHORIZE_CHALLENGE_PATH: Final = "oauth/authorize-challenge"
TOKEN_PATH: Final = "oauth/token"  # noqa: S105 (a URL path, not a credential)
LOGOUT_PATH: Final = "auth/logout"

# Sent by the mobile app; the API rejects requests without them.
APP_VERSION: Final = "6.23.0"
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


class BatteryMode(StrEnum):
    """Home battery control modes.

    ``DYNAMIC_CHARGING`` (trading on prices) is what the battery does with the
    other modes disabled.
    """

    SELF_CONSUMPTION = "self_consumption"
    HOME_OPTIMIZATION = "home_optimization"
    DYNAMIC_CHARGING = "dynamic_charging"
    DAY_AHEAD_ARBITRAGE = "day_ahead_arbitrage"


class BatteryUnavailabilityReason(StrEnum):
    """Why a battery control mode is unavailable."""

    DISALLOWED_BY_PROJECT = "disallowed_by_project"
    NO_ACTIVE_ELECTRICITY_CONTRACT = "no_active_electricity_contract"


class BatteryPowerPreset(StrEnum):
    """Home optimization power presets."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MarketTimeResolution(StrEnum):
    """Market time resolution day-ahead arbitrage trades on, as an ISO 8601 duration."""

    QUARTER_HOUR = "PT15M"
    HOUR = "PT60M"


class BatteryChartType(StrEnum):
    """The kind of recent activity chart: day-ahead arbitrage adds the planning and market prices."""

    DEFAULT = "default"
    DAY_AHEAD_ARBITRAGE = "day_ahead_arbitrage"


class BatteryActivityType(StrEnum):
    """What steered a home battery during a period of its recent activity."""

    HOME_OPTIMIZATION = "home_optimization"
    GRID_OPTIMIZATION = "grid_optimization"


class BatteryActivityDirection(StrEnum):
    """Whether a planned battery activity charges or discharges."""

    CHARGE = "charge"
    DISCHARGE = "discharge"


class PriceResolution(StrEnum):
    """The interval the app shows electricity prices in (the ``price_resolution`` app setting)."""

    QUARTER_HOUR = "15-min"
    HOUR = "60-min"


class BatteryState(StrEnum):
    """Home battery and battery inverter states.

    The API sends them in PascalCase (``ShuttingDown``); the models parse them
    case-insensitively and with or without underscores.
    """

    UNKNOWN_STATE = "unknown_state"
    OFF = "off"
    SLEEPING = "sleeping"
    STARTING = "starting"
    OPERATIVE = "operative"
    THROTTLED = "throttled"
    SHUTTING_DOWN = "shutting_down"
    FAULT = "fault"
    STANDBY = "standby"
    UPDATING = "updating"
    COOLING = "cooling"
    PREHEAT = "preheat"
    CHARGING = "charging"
    DISCHARGING = "discharging"
    PERMANENT_FAULT = "permanent_fault"
    BACKUP_POWER_ACTIVE = "backup_power_active"


class ChargePointStatus(StrEnum):
    """Charge point states (``state.state``, sent in PascalCase)."""

    STANDBY = "standby"
    VEHICLE_DETECTED = "vehicle_detected"
    CHARGING = "charging"
    ERROR = "error"


class ChargePointStartMode(StrEnum):
    """How a charge point starts a session (``state.start_mode``, sent in PascalCase)."""

    ALWAYS_FLEX = "always_flex"
    PLUG_AND_CHARGE = "plug_and_charge"
    START_WITH_APP = "start_with_app"


class LoadBalancingHealth(StrEnum):
    """Health of a charge point's dynamic load balancing (``state.dynamic_load_balancing_health``, sent in upper case)."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class ChargePointMode(StrEnum):
    """Charge point control modes (``modes`` of the charge point control-mode endpoint)."""

    BOOST = "boost"
    PLUG_AND_CHARGE = "plug_and_charge"
    DYNAMIC_CHARGING = "dynamic_charging"
    SCHEDULED_CHARGING = "scheduled_charging"
    CHARGE_ON_SOLAR = "charge_on_solar"


class TariffGroup(StrEnum):
    """Price level of an interval compared to the rest of the day."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class MeasurementGroupType(StrEnum):
    """The granularity of a measurement group (``measurement_groups[i].type``)."""

    LIVE = "live"
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"
    MONTHS = "months"


class RecentPowerDuration(StrEnum):
    """How far back the recent power chart goes, as an ISO 8601 duration."""

    HOURS_12 = "PT12H"
    HOURS_24 = "PT24H"
    HOURS_48 = "PT48H"


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
