# Home

Welcome to the documentation for pyzonneplan, an asynchronous Python client for the [Zonneplan](https://www.zonneplan.nl/) API.

## About

This is an asynchronous Python client for the Zonneplan API. Created by [Erwin Douna](https://github.com/erwindouna). It reads prices, consumption, solar (PV), home battery and EV charge point data from a Zonneplan account.

## Installation

```bash
pip install pyzonneplan
```

## Usage

Zonneplan authenticates with a one-time password (OTP) mailed to the account address instead of a password.
Log in once, then store the token and pass it back in later:

```python
import asyncio
import json
from datetime import date
from pathlib import Path

from pyzonneplan import Token, Zonneplan

TOKEN_FILE = Path("token.json")


async def main() -> None:
    """Log in (or restore the saved token) and read some data."""
    token = Token.from_dict(json.loads(TOKEN_FILE.read_text())) if TOKEN_FILE.exists() else None

    async with Zonneplan(email="you@example.com", token=token) as client:
        if token is None:
            challenge = await client.async_request_otp(source_name="pyzonneplan")
            await client.async_submit_otp(challenge, input("One-time password: "))

        account = await client.async_get_account()
        for connection in account.connections:
            print(connection.market_segment, connection.uuid)

        electricity = next(c for c in account.connections if c.market_segment == "electricity")
        summary = await client.async_get_summary(electricity.uuid)
        print("Tariff group now:", summary.usage.type)

        chart = await client.async_get_electricity_chart(electricity.uuid, date.today())
        if chart.group is not None and chart.group.has_data:
            print("Used today:", chart.group.delivered_kwh, "kWh")

        # The client refreshes the token when it nears expiry, which rotates it: save the latest one.
        if client.token is not None:
            TOKEN_FILE.write_text(json.dumps(client.token.as_dict()))


if __name__ == "__main__":
    asyncio.run(main())
```

### Available data

| Method | Returns |
|---|---|
| `async_get_account()` | The account, its addresses, connections and contracts |
| `async_get_consumer_prices(chart)` | Electricity (hourly or quarter-hourly) or daily gas prices, with `price_at()` and cheapest/most expensive helpers |
| `async_get_summary(connection_uuid)` | Live usage (P1 only), the current tariff group and a price forecast of about two days |
| `async_get_electricity_chart(connection_uuid, day, interval)` | Electricity used and returned per hour, day or month |
| `async_get_gas_chart(connection_uuid, day, interval)` | Gas used per hour, day or month |
| `async_get_electricity_delivered(connection_uuid)` / `async_get_gas(connection_uuid)` | P1 totals, or `None` without a P1 meter |
| `async_get_pv_installation(connection_uuid)` | Every solar inverter on the connection and today's yield |
| `async_get_battery(connection_uuid, contract_uuid)` | Home battery state, results and modes |
| `async_get_battery_chart(contract_uuid, day, interval)` | Home battery results per day or month |
| `async_get_battery_control_mode(contract_uuid)` / `async_get_battery_home_optimization(contract_uuid)` | The battery's control mode, and its charge and discharge power settings for home optimization |
| `async_get_charge_point(connection_uuid, contract_uuid)` | Charge point state, schedules and vehicles |
| `async_set_locale(locale)` | Sets the language of the API's texts, e.g. `nl-NL` |

The contract UUIDs come from the account, e.g. `connection.contracts_of_type(ContractType.HOME_BATTERY)`
(constants such as `ContractType`, `ChartInterval` and `BatteryMode` live in `pyzonneplan.const`).
Fields keep the API's raw units (1e-7 EUR, Wh, dm³, permille); properties such as `delivered_kwh`,
`electricity_price_euro` and `state_of_charge_percent` convert them.

Usage without a P1 meter arrives a day or more late from the grid operator: check `has_data` before trusting zeros,
and expect the last hours of a day to be revised when the next day arrives.

The PV, battery and charge point models follow the responses other projects have seen, but haven't been tested
against live hardware yet. Reports and captured (anonymised) responses are welcome.

### Errors

Every error derives from `ZonneplanError`:

- `ZonneplanAuthenticationError`: the token is invalid or expired (log in again); `ZonneplanInvalidOtpError` when the OTP is rejected.
- `ZonneplanRateLimitError`: HTTP 429; `retry_after` holds the seconds to wait. It isn't retried.
- `ZonneplanRequestError`: the API rejected the request (HTTP 400), e.g. an unknown chart interval.
- `ZonneplanConnectionError` / `ZonneplanTimeoutError`: a network error, a timeout or any other HTTP error status; retried with backoff (`max_retries`, default 3).

See the API reference for every model and its properties.

## Support

If you like my opensource work, you can support me via the following ways:

<a href="https://github.com/sponsors/erwindouna"><img src="https://img.shields.io/static/v1?label=Github%20Sponsor&message=%E2%9D%A4&logo=GitHub&color=%23fe8e86&style=flat-square&height=100" alt="Github Sponsor"></a>

<a href="https://buymeacoffee.com/edounae"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me a Coffee"></a>
