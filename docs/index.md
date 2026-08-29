# Home

Welcome to the documentation for pyzonneplan, an asynchronous Python client for the [Zonneplan](https://www.zonneplan.nl/) API.

## About

This is an asynchronous Python client for the Zonneplan API. Created by [Erwin Douna](https://github.com/erwindouna). It is focused on reading solar (PV), home battery, and EV charge point data from a Zonneplan account.

Zonneplan authenticates with a one-time password mailed to the account address rather than a plain username/password login. The API reference is the best place to look for all available classes.

## Installation

```bash
pip install pyzonneplan
```

## Usage

Requesting and submitting the one-time password:

```python
import asyncio

from aiohttp import ClientSession
from pyzonneplan import ZonneplanAuth


async def main() -> None:
    """Run the example."""
    async with ClientSession() as session:
        auth = ZonneplanAuth(session=session)
        challenge = await auth.async_request_otp("you@example.com", source_name="pyzonneplan")

        otp = input("Enter the one-time password mailed to you: ")
        token = await auth.async_submit_otp(challenge, otp)

        print("Access token:", token.access_token)


if __name__ == "__main__":
    asyncio.run(main())
```

Store `token.as_dict()` and restore it later with `Token.from_dict(...)`; refresh it with `auth.async_refresh_token(token)` once it is close to `token.expires_at`.

A client that uses the token to fetch account, PV, battery and charge point data is on the roadmap.

## Support

If you like my opensource work, you can support me via the following ways:

<a href="https://github.com/sponsors/erwindouna"><img src="https://img.shields.io/static/v1?label=Github%20Sponsor&message=%E2%9D%A4&logo=GitHub&color=%23fe8e86&style=flat-square&height=100" alt="Github Sponsor"></a>

<a href="https://buymeacoffee.com/edounae"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me a Coffee"></a>
