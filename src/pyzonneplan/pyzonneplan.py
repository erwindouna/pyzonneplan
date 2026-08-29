"""Asynchronous Python client for the Zonneplan API."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from http import HTTPStatus
from importlib import metadata
from typing import Any, NoReturn, Self

import orjson
from aiohttp import ClientError, ClientResponseError, ClientSession
from aiohttp.hdrs import METH_GET, METH_POST
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential
from yarl import URL

from pyzonneplan.auth import OtpChallenge, Token, generate_pkce_pair
from pyzonneplan.const import (
    API_SCHEME,
    API_URL,
    APP_ENVIRONMENT,
    APP_VERSION,
    AUTHORIZE_CHALLENGE_PATH,
    TOKEN_PATH,
    PriceChart,
)
from pyzonneplan.exceptions import (
    ZonneplanAuthenticationError,
    ZonneplanConnectionError,
    ZonneplanInvalidOtpError,
    ZonneplanTimeoutError,
)
from pyzonneplan.models.account import Account
from pyzonneplan.models.consumption import ElectricityDelivered, Gas
from pyzonneplan.models.prices import ConsumerPrices

try:
    VERSION = metadata.version(__package__)
except metadata.PackageNotFoundError:  # pragma: no cover
    VERSION = "DEV-0.0.0"


@dataclass
class Zonneplan:
    """Asynchronous Python client for the Zonneplan API.

    Zonneplan uses an OAuth2 authorization-code flow with PKCE, but the
    user-facing step is a one-time password mailed to the account address
    instead of a browser redirect. The flow is three calls:

    1. POST /oauth/authorize-challenge  {response_type, email, code_challenge, ...}
       -> HTTP 403 with ``otp_required: true`` and an ``auth_session``.
          The 403 is the success path, not an error.
    2. POST /oauth/authorize-challenge  {auth_session, otp}
       -> ``authorization_code``
    3. POST /oauth/token  {grant_type: authorization_code, code, code_verifier}
       -> access + refresh token

    Every other request checks the current token before it goes out and
    transparently refreshes it once it's within its expiry margin.
    """

    request_timeout: float = 10.0
    session: ClientSession | None = None

    def __init__(
        self,
        email: str,
        request_timeout: float = 10.0,
        session: ClientSession | None = None,
        max_retries: int = 3,
        token: Token | None = None,
    ) -> None:
        """Initialize the Zonneplan client.

        Pass a previously-obtained ``token`` (see :attr:`token` and
        :meth:`pyzonneplan.auth.Token.as_dict`) to restore a session without
        going through the OTP flow again.
        """
        self._email = email
        self._session = session
        self.request_timeout = request_timeout
        self._close_session = session is None
        self._token = token
        self._max_retries = max_retries

    @property
    def token(self) -> Token | None:
        """Return the current token, or ``None`` before login."""
        return self._token

    async def _request(
        self,
        uri: str,
        *,
        method: str = METH_GET,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
        allow_forbidden: bool = False,
        authenticated: bool = True,
    ) -> Any:
        """Handle a request to the Zonneplan API.

        Args:
        ----
            uri: Request URI, without the leading '/', for example 'user-accounts/me'.
            method: HTTP method to use.
            params: Query parameters to include in the request.
            json_body: JSON body to include in the request.
            timeout: Timeout for the request (in seconds).
            allow_forbidden: Treat a 403 response as success instead of raising.
                Zonneplan's OTP challenge endpoint returns 403 on the happy path.
            authenticated: Whether this call should refresh an expiring token
                and send it along. False for the OTP/token endpoints
                themselves, which would otherwise recurse into a refresh.

        Returns:
        -------
            The JSON-decoded response body, or ``None`` for an empty response.

        Raises:
        ------
            ZonneplanAuthenticationError: If the access token is invalid or missing.
            ZonneplanConnectionError: On network errors.
            ZonneplanTimeoutError: If the request times out.

        """
        if authenticated and self._token is not None and self._token.is_expired:
            await self.async_refresh_token()

        url = URL.build(scheme=API_SCHEME, host=API_URL, path="/").join(URL(uri))

        headers = {
            "content-type": "application/json;charset=utf-8",
            "user-agent": f"pyzonneplan/{VERSION}",
            "x-app-version": APP_VERSION,
            "x-app-environment": APP_ENVIRONMENT,
        }
        if self._token is not None:
            headers["authorization"] = f"Bearer {self._token.access_token}"

        if self._session is None:
            self._session = ClientSession()
            self._close_session = True

        if timeout is None:
            timeout = self.request_timeout

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((ZonneplanConnectionError, ZonneplanTimeoutError)),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            stop=stop_after_attempt(self._max_retries + 1),
            reraise=True,
        ):
            with attempt:
                body = b""
                try:
                    async with asyncio.timeout(timeout):
                        response = await self._session.request(
                            method,
                            url,
                            headers=headers,
                            params=params,
                            json=json_body,
                        )
                        # Read the body before raise_for_status(), which releases
                        # the response (and with it, the body stream) before raising.
                        body = await response.read()
                        if not (allow_forbidden and response.status == HTTPStatus.FORBIDDEN):
                            response.raise_for_status()
                except TimeoutError as err:
                    msg = f"Timeout error while accessing {method} {url}: {err}"
                    raise ZonneplanTimeoutError(msg) from err
                except ClientResponseError as err:
                    self._raise_for_client_response_error(method, url, err, body)
                except (ClientError, socket.gaierror) as err:
                    msg = f"Unexpected error during {method} {url}: {err}"
                    raise ZonneplanConnectionError(msg) from err

        if response.status in (204, 304):
            return None

        return orjson.loads(body)

    @staticmethod
    def _raise_for_client_response_error(method: str, url: URL, err: ClientResponseError, body: bytes) -> NoReturn:
        """Map a failed HTTP response to the appropriate Zonneplan exception."""
        body_text = body.decode(errors="replace")
        match err.status:
            case 400 | 401 | 403:
                # Zonneplan returns a plain 400 (not 401/403) for a rejected or
                # expired OTP/refresh grant, so treat it as an authentication
                # failure too, not a connection error.
                msg = f"Authentication failed for {method} {url}: {err} ({body_text})"
                raise ZonneplanAuthenticationError(msg) from err
            case _:
                msg = f"Connection error for {method} {url}: {err} ({body_text})"
                raise ZonneplanConnectionError(msg) from err

    async def async_request_otp(self, source_name: str) -> OtpChallenge:
        """Start the login and have Zonneplan mail a one-time password."""
        code_verifier, code_challenge = generate_pkce_pair()
        payload = {
            "response_type": "code",
            "email": self._email,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "source_name": source_name[:255],
        }

        response = await self._request(
            AUTHORIZE_CHALLENGE_PATH,
            method=METH_POST,
            json_body=payload,
            allow_forbidden=True,
            authenticated=False,
        )

        if not response.get("otp_required") or "auth_session" not in response:
            msg = "Zonneplan did not return an OTP challenge"
            raise ZonneplanAuthenticationError(msg)

        return OtpChallenge(auth_session=response["auth_session"], code_verifier=code_verifier, email=self._email)

    async def async_submit_otp(self, challenge: OtpChallenge, otp: str) -> Token:
        """Exchange the mailed one-time password for a token, and use it for further requests."""
        try:
            response = await self._request(
                AUTHORIZE_CHALLENGE_PATH,
                method=METH_POST,
                json_body={"auth_session": challenge.auth_session, "otp": otp},
                authenticated=False,
            )
        except ZonneplanAuthenticationError as err:
            msg = "Zonneplan rejected the one-time password"
            raise ZonneplanInvalidOtpError(msg) from err

        authorization_code = response.get("authorization_code")
        if not authorization_code:
            msg = "Zonneplan rejected the one-time password"
            raise ZonneplanInvalidOtpError(msg)

        token = await self._async_request_token(
            {
                "grant_type": "authorization_code",
                "code": authorization_code,
                "code_verifier": challenge.code_verifier,
            }
        )
        self._token = token
        return token

    async def async_refresh_token(self) -> Token:
        """Exchange the current refresh token for a new one, regardless of whether it has expired yet."""
        if self._token is None:
            msg = "Cannot refresh a token before logging in"
            raise ZonneplanAuthenticationError(msg)

        self._token = await self._async_request_token({"grant_type": "refresh_token", "refresh_token": self._token.refresh_token})
        return self._token

    async def _async_request_token(self, grant: dict[str, str]) -> Token:
        """Call the token endpoint and parse the result."""
        response = await self._request(TOKEN_PATH, method=METH_POST, json_body=grant, authenticated=False)
        return Token.from_response(response)

    async def async_get_account(self) -> Account:
        """Fetch the authenticated Zonneplan account, its addresses and connections."""
        response = await self._request("user-accounts/me")
        return Account.from_dict(response["data"])

    async def async_get_consumer_prices(self, chart: str = PriceChart.ELECTRICITY_HOURLY) -> ConsumerPrices:
        """Fetch a consumer price chart (see :class:`pyzonneplan.const.PriceChart` for valid ``chart`` values)."""
        response = await self._request(f"api/consumer-prices/charts/{chart}")
        return ConsumerPrices.from_dict(response["data"])

    async def async_get_electricity_delivered(self, connection_uuid: str) -> ElectricityDelivered:
        """Fetch electricity delivery/production data (P1) for a connection."""
        response = await self._request(f"connections/{connection_uuid}/electricity-delivered")
        return ElectricityDelivered.from_dict(response["data"])

    async def async_get_gas(self, connection_uuid: str) -> Gas:
        """Fetch gas consumption data (P1) for a connection."""
        response = await self._request(f"connections/{connection_uuid}/gas")
        return Gas.from_dict(response["data"])

    async def close(self) -> None:
        """Close open client session."""
        if self._session and self._close_session:
            await self._session.close()

    async def __aenter__(self) -> Self:
        """Async enter.

        Returns
        -------
            The Zonneplan object.

        """
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        """Async exit.

        Args:
        ----
            _exc_info: Exec type.

        """
        await self.close()
