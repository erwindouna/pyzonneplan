"""Asynchronous example for pyzonneplan."""

import asyncio

from pyzonneplan import Zonneplan


async def main() -> None:
    """Run the example."""
    async with Zonneplan(email="you@example.com") as client:
        challenge = await client.async_request_otp(source_name="pyzonneplan")

        otp = input("Enter the one-time password mailed to you: ")
        token = await client.async_submit_otp(challenge, otp)

        print("Access token:", token.access_token)
        print("Refresh token:", token.refresh_token)
        print("Expires at:", token.expires_at)


if __name__ == "__main__":
    asyncio.run(main())
