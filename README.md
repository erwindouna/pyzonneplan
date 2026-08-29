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

- Solar (PV) installation data
- Dynamic pricing and energy consumption data
- Home battery monitoring and control modes
- EV charge point sessions and schedules

The library is under active development and endpoint coverage will keep expanding.

## Installation

```bash
pip install pyzonneplan
```

## Usage

Zonneplan authenticates with a one-time password mailed to the account address, rather than a plain username/password login:

```python
import asyncio

from aiohttp import ClientSession
from pyzonneplan import ZonneplanAuth


async def main() -> None:
    async with ClientSession() as session:
        auth = ZonneplanAuth(session=session)
        challenge = await auth.async_request_otp("you@example.com", source_name="pyzonneplan")

        otp = input("Enter the one-time password mailed to you: ")
        token = await auth.async_submit_otp(challenge, otp)

        print("Access token:", token.access_token)


if __name__ == "__main__":
    asyncio.run(main())
```

Store `token.as_dict()` and restore it later with `Token.from_dict(...)`; refresh it
with `auth.async_refresh_token(token)` once it is close to `token.expires_at`.

More examples can be found in the examples directory.

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
