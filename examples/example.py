"""Asynchronous example for pyzonneplan."""

import asyncio

from aiohttp import ClientSession

from pyzonneplan import Token, ZonneplanAuth


async def main() -> None:
    """Run the example."""
    async with ClientSession() as session:
        auth = ZonneplanAuth(session=session)
        challenge = await auth.async_request_otp("you@example.com", source_name="pyzonneplan")

        otp = input("Enter the one-time password mailed to you: ")
        token: Token = await auth.async_submit_otp(challenge, otp)

        print("Access token:", token.access_token)
        print("Refresh token:", token.refresh_token)
        print("Expires at:", token.expires_at)


if __name__ == "__main__":
    asyncio.run(main())
