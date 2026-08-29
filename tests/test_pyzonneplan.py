"""Tests for pyzonneplan.pyzonneplan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import orjson
import pytest
from aiohttp import ClientConnectionError
from aresponses import ResponsesMockServer

from pyzonneplan import Zonneplan
from pyzonneplan.auth import OtpChallenge, Token
from pyzonneplan.const import PriceChart
from pyzonneplan.exceptions import (
    ZonneplanAuthenticationError,
    ZonneplanConnectionError,
    ZonneplanInvalidOtpError,
    ZonneplanTimeoutError,
)

from . import load_fixtures

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

HOST = "app-api.zonneplan.nl"


async def test_async_request_otp_success(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A 403 with otp_required is the successful path."""
    aresponses.add(
        HOST,
        "/oauth/authorize-challenge",
        "POST",
        aresponses.Response(
            status=403,
            headers={"Content-Type": "application/json"},
            text=orjson.dumps({"otp_required": True, "auth_session": "sess-123"}).decode(),
        ),
    )

    challenge = await zonneplan_client.async_request_otp("pytest")

    assert challenge.auth_session == "sess-123"
    assert challenge.email == "user@example.com"


async def test_async_request_otp_without_challenge_raises(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A 403 that is not an OTP challenge is a real authentication failure."""
    aresponses.add(
        HOST,
        "/oauth/authorize-challenge",
        "POST",
        aresponses.Response(status=403, text=orjson.dumps({"error": "blocked"}).decode()),
    )

    with pytest.raises(ZonneplanAuthenticationError):
        await zonneplan_client.async_request_otp("pytest")


async def test_async_submit_otp_success(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A valid OTP is exchanged for an authorization code, then a token, which is kept for later requests."""
    aresponses.add(
        HOST,
        "/oauth/authorize-challenge",
        "POST",
        aresponses.Response(text=orjson.dumps({"authorization_code": "code-123"}).decode()),
    )
    aresponses.add(
        HOST,
        "/oauth/token",
        "POST",
        aresponses.Response(text=orjson.dumps({"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}).decode()),
    )

    challenge = OtpChallenge(auth_session="sess-123", code_verifier="verifier", email="user@example.com")
    token = await zonneplan_client.async_submit_otp(challenge, "123456")

    assert token.access_token == "access"
    assert token.refresh_token == "refresh"
    assert zonneplan_client._token is token


async def test_async_submit_otp_invalid_raises(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A rejected OTP has no authorization_code in the response."""
    aresponses.add(
        HOST,
        "/oauth/authorize-challenge",
        "POST",
        aresponses.Response(text=orjson.dumps({}).decode()),
    )

    challenge = OtpChallenge(auth_session="sess-123", code_verifier="verifier", email="user@example.com")
    with pytest.raises(ZonneplanInvalidOtpError):
        await zonneplan_client.async_submit_otp(challenge, "000000")


async def test_async_submit_otp_400_raises_invalid_otp_error(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Zonneplan signals a rejected/expired OTP with a plain 400, which is also an invalid-OTP error."""
    aresponses.add(HOST, "/oauth/authorize-challenge", "POST", aresponses.Response(status=400, text="{}"))

    challenge = OtpChallenge(auth_session="sess-123", code_verifier="verifier", email="user@example.com")
    with pytest.raises(ZonneplanInvalidOtpError):
        await zonneplan_client.async_submit_otp(challenge, "000000")


async def test_request_400_raises_authentication_error(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A plain 400 response is treated as an authentication failure, not a connection error."""
    aresponses.add(HOST, "/user-accounts/me", "GET", aresponses.Response(status=400, text="{}"))

    with pytest.raises(ZonneplanAuthenticationError):
        await zonneplan_client._request("user-accounts/me")


async def test_request_error_message_includes_response_body(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """The raised error includes the response body, so the server's actual reason is visible."""
    aresponses.add(
        HOST,
        "/user-accounts/me",
        "GET",
        aresponses.Response(status=400, text=orjson.dumps({"error": "too_many_requests"}).decode()),
    )

    with pytest.raises(ZonneplanAuthenticationError, match="too_many_requests"):
        await zonneplan_client._request("user-accounts/me")


async def test_async_get_account(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion) -> None:
    """A real /user-accounts/me response is unwrapped from its 'data' envelope and parsed."""
    aresponses.add(
        HOST,
        "/user-accounts/me",
        "GET",
        aresponses.Response(text=load_fixtures("get_account.json")),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    account = await zonneplan_client.async_get_account()

    assert account == snapshot


async def test_async_get_consumer_prices(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion) -> None:
    """A real electricity-hourly consumer-prices response is unwrapped and parsed."""
    aresponses.add(
        HOST,
        "/api/consumer-prices/charts/electricity-hourly",
        "GET",
        aresponses.Response(text=load_fixtures("get_consumer_prices_electricity_hourly.json")),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    prices = await zonneplan_client.async_get_consumer_prices()

    assert prices == snapshot


async def test_async_get_consumer_prices_gas_daily(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion) -> None:
    """A real gas-daily consumer-prices response (no tariff_group/sustainability_score) is parsed."""
    aresponses.add(
        HOST,
        "/api/consumer-prices/charts/gas-daily",
        "GET",
        aresponses.Response(text=load_fixtures("get_consumer_prices_gas_daily.json")),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    prices = await zonneplan_client.async_get_consumer_prices(chart=PriceChart.GAS_DAILY)

    assert prices == snapshot


async def test_async_get_electricity_delivered(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion) -> None:
    """electricity-delivered is parsed from a synthetic fixture.

    No P1 meter is connected on the test account this was scoped against, so
    the shape is reverse-engineered from fsaris/home-assistant-zonneplan-one's
    sensor key-paths rather than a captured response. Replace this fixture
    with a real capture once available.
    """
    aresponses.add(
        HOST,
        "/connections/conn-1/electricity-delivered",
        "GET",
        aresponses.Response(text=load_fixtures("get_electricity_delivered.json")),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    electricity = await zonneplan_client.async_get_electricity_delivered("conn-1")

    assert electricity == snapshot


async def test_async_get_gas(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion) -> None:
    """Gas is parsed from a synthetic fixture (see test_async_get_electricity_delivered for why)."""
    aresponses.add(
        HOST,
        "/connections/conn-1/gas",
        "GET",
        aresponses.Response(text=load_fixtures("get_gas.json")),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    gas = await zonneplan_client.async_get_gas("conn-1")

    assert gas == snapshot


async def test_request_refreshes_expired_token_before_use(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """An expired token is refreshed before the request that needed it goes out."""
    aresponses.add(
        HOST,
        "/oauth/token",
        "POST",
        aresponses.Response(text=orjson.dumps({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}).decode()),
    )
    aresponses.add(
        HOST,
        "/user-accounts/me",
        "GET",
        aresponses.Response(text=orjson.dumps({"data": {"user_account": {"uuid": "u-1"}}}).decode()),
    )

    zonneplan_client._token = Token(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    account = await zonneplan_client.async_get_account()

    assert zonneplan_client._token.access_token == "new-access"
    assert account.user_account.uuid == "u-1"


async def test_seeded_token_is_used_without_login(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A token passed in at construction time is usable without an OTP login first."""
    seeded = Token(access_token="seeded-access", refresh_token="seeded-refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    client = Zonneplan(email="user@example.com", session=zonneplan_client._session, max_retries=0, token=seeded)

    assert client.token is seeded

    aresponses.add(
        HOST,
        "/user-accounts/me",
        "GET",
        aresponses.Response(text=orjson.dumps({"data": {"user_account": {"uuid": "u-1"}}}).decode()),
    )

    account = await client.async_get_account()
    assert account.user_account.uuid == "u-1"


async def test_async_refresh_token_forces_a_refresh(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """async_refresh_token exchanges the refresh token even when the current one hasn't expired yet."""
    aresponses.add(
        HOST,
        "/oauth/token",
        "POST",
        aresponses.Response(text=orjson.dumps({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}).decode()),
    )

    zonneplan_client._token = Token(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    refreshed = await zonneplan_client.async_refresh_token()

    assert refreshed.access_token == "new-access"
    assert zonneplan_client.token is refreshed


async def test_async_refresh_token_without_login_raises(zonneplan_client: Zonneplan) -> None:
    """Refreshing before a token exists is a client-side authentication error."""
    with pytest.raises(ZonneplanAuthenticationError):
        await zonneplan_client.async_refresh_token()


async def test_request_unauthorized_raises_authentication_error(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A 401 response is surfaced as an authentication error."""
    aresponses.add(HOST, "/user-accounts/me", "GET", aresponses.Response(status=401, text="{}"))

    with pytest.raises(ZonneplanAuthenticationError):
        await zonneplan_client._request("user-accounts/me")


class _RaisingRequest:
    """A ``session.request`` stand-in that raises as soon as it's called."""

    def __init__(self, exception: BaseException) -> None:
        self._exception = exception

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self._exception


async def test_request_timeout_raises_zonneplan_timeout_error(zonneplan_client: Zonneplan) -> None:
    """A network timeout is translated to ZonneplanTimeoutError."""
    zonneplan_client._session.request = _RaisingRequest(TimeoutError())  # type: ignore[method-assign,union-attr]
    with pytest.raises(ZonneplanTimeoutError):
        await zonneplan_client._request("user-accounts/me")


async def test_request_connection_error_raises_zonneplan_connection_error(zonneplan_client: Zonneplan) -> None:
    """A connection failure is translated to ZonneplanConnectionError."""
    zonneplan_client._session.request = _RaisingRequest(ClientConnectionError())  # type: ignore[method-assign,union-attr]
    with pytest.raises(ZonneplanConnectionError):
        await zonneplan_client._request("user-accounts/me")
