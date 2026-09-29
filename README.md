# pyzonneplan

[![GitHub Release][releases-shield]][releases]
[![Python Versions][python-versions-shield]][pypi]
![Project Stage][project-stage-shield]
![Project Maintenance][maintenance-shield]
[![License][license-shield]](LICENSE)

[![GitHub Activity][commits-shield]][commits-url]
[![PyPI Downloads][downloads-shield]][downloads-url]
[![GitHub Last Commit][last-commit-shield]][commits-url]
[![Open in Dev Containers][devcontainer-shield]][devcontainer]

[![Build Status][build-shield]][build-url]
[![Typing Status][typing-shield]][typing-url]
[![Code Coverage][codecov-shield]][codecov-url]

Asynchronous Python client for the Zonneplan API.

## About

pyzonneplan is an async client for the [Zonneplan](https://www.zonneplan.nl/) API, focused on:

- Dynamic electricity and gas prices
- Electricity and gas consumption, also without a P1 meter
- Solar (PV) installation data
- Home battery monitoring and control modes
- EV charge point sessions and schedules

The library is under active development and endpoint coverage will keep expanding.

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
| `async_get_electricity_delivered(connection_uuid)` / `async_get_gas(connection_uuid)` | P1 totals and live readings (`meters`), or `None` without a P1 meter |
| `async_get_pv_installation(connection_uuid)` | Every solar inverter on the connection and today's yield |
| `async_get_battery(connection_uuid, contract_uuid)` | Home battery state, results and modes |
| `async_get_battery_chart(contract_uuid, day, interval)` | Home battery results per day or month |
| `async_get_battery_control_mode(contract_uuid)` / `async_get_battery_home_optimization(contract_uuid)` | The battery's control mode, and the charge and discharge power for home optimization with the range each accepts |
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
- `ZonneplanRequestError`: the API rejected the request (HTTP 4xx), e.g. an unknown chart interval;
  `ZonneplanNotFoundError` for a 404, e.g. a device the account doesn't have. Not retried.
- `ZonneplanResponseError`: the response isn't JSON or doesn't have the expected shape.
- `ZonneplanConnectionError` / `ZonneplanTimeoutError`: a network error, a timeout or an HTTP 5xx.
  GET requests are retried with backoff first (`max_retries`, default 3); actions are never retried.

More examples can be found in the [examples](examples) directory.

## Documentation

Project documentation and API reference: https://erwindouna.github.io/pyzonneplan/

## Contributing

Contributions are welcome. Please open an issue or pull request.

For local development:

```bash
uv sync --all-groups && uv run pre-commit install
```

Run checks:

```bash
uv run pre-commit run --all-files
```

Run tests:

```bash
uv run pytest
```

## License

MIT License

Copyright (c) 2026 Erwin Douna

<!-- MARKDOWN LINKS & IMAGES -->

[build-shield]: https://github.com/erwindouna/pyzonneplan/actions/workflows/tests.yaml/badge.svg
[build-url]: https://github.com/erwindouna/pyzonneplan/actions/workflows/tests.yaml
[codecov-shield]: https://codecov.io/gh/erwindouna/pyzonneplan/branch/main/graph/badge.svg
[codecov-url]: https://codecov.io/gh/erwindouna/pyzonneplan
[commits-shield]: https://img.shields.io/github/commit-activity/y/erwindouna/pyzonneplan.svg
[commits-url]: https://github.com/erwindouna/pyzonneplan/commits/main
[devcontainer-shield]: https://img.shields.io/static/v1?label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode
[devcontainer]: https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/erwindouna/pyzonneplan
[downloads-shield]: https://img.shields.io/pypi/dm/pyzonneplan
[downloads-url]: https://pypistats.org/packages/pyzonneplan
[last-commit-shield]: https://img.shields.io/github/last-commit/erwindouna/pyzonneplan.svg
[license-shield]: https://img.shields.io/github/license/erwindouna/pyzonneplan.svg
[project-stage-shield]: https://img.shields.io/badge/project%20stage-experimental-yellow.svg
[maintenance-shield]: https://img.shields.io/maintenance/yes/2026.svg
[pypi]: https://pypi.org/project/pyzonneplan/
[python-versions-shield]: https://img.shields.io/pypi/pyversions/pyzonneplan
[releases-shield]: https://img.shields.io/github/release/erwindouna/pyzonneplan.svg
[releases]: https://github.com/erwindouna/pyzonneplan/releases
[typing-shield]: https://github.com/erwindouna/pyzonneplan/actions/workflows/typing.yaml/badge.svg
[typing-url]: https://github.com/erwindouna/pyzonneplan/actions/workflows/typing.yaml
