"""Constants for the Zonneplan API client."""

from __future__ import annotations

from decimal import Decimal
from typing import Final

API_SCHEME: Final = "https"
API_HOST: Final = "app-api.zonneplan.nl"

AUTHORIZE_CHALLENGE_PATH: Final = "oauth/authorize-challenge"
TOKEN_PATH: Final = "oauth/token"  # noqa: S105 (a URL path, not a credential)

# Sent by the mobile app; the API rejects requests without them.
APP_VERSION: Final = "5.10.1"
APP_ENVIRONMENT: Final = "production"

# Every monetary "amount" in the API is expressed in 1e-7 EUR.
MONEY_FACTOR: Final = Decimal("0.0000001")

# Energy values are Wh, power values are W.
WH_TO_KWH: Final = Decimal("0.001")


class ChartInterval:
    """Supported battery chart intervals."""

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
