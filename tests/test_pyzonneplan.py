"""Tests for pyzonneplan.pyzonneplan."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import orjson
import pytest
from aiohttp import ClientConnectionError
from aresponses import ResponsesMockServer
from tenacity import wait_none

from pyzonneplan import ElectricityDelivered, Zonneplan
from pyzonneplan.auth import OtpChallenge, Token
from pyzonneplan.const import (
    BatteryMode,
    ChargePointStartMode,
    ChartInterval,
    ConsumptionChart,
    MarketTimeResolution,
    PriceChart,
    PriceResolution,
    RecentPowerDuration,
)
from pyzonneplan.exceptions import (
    ZonneplanAuthenticationError,
    ZonneplanConnectionError,
    ZonneplanInvalidOtpError,
    ZonneplanNotFoundError,
    ZonneplanRateLimitError,
    ZonneplanRequestError,
    ZonneplanResponseError,
    ZonneplanTimeoutError,
)
from pyzonneplan.models import ChargeSchedule

from . import load_fixtures

if TYPE_CHECKING:
    from collections.abc import Callable

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


async def test_token_request_400_raises_authentication_error(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A plain 400 from the token endpoint (rejected refresh grant) is an authentication failure, not a connection error."""
    aresponses.add(HOST, "/oauth/token", "POST", aresponses.Response(status=400, text="{}"))

    with pytest.raises(ZonneplanAuthenticationError):
        await zonneplan_client._request("oauth/token", method="POST", authenticated=False)


async def test_data_request_400_raises_request_error(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A 400 from a data endpoint is a bad request, not an authentication failure that would force a new login."""
    aresponses.add(
        HOST,
        "/connections/conn-1/gas/charts/years",
        "GET",
        aresponses.Response(status=400, text=orjson.dumps({"message": "Invalid chart type."}).decode()),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(ZonneplanRequestError, match="Invalid chart type"):
        await zonneplan_client.async_get_gas_chart("conn-1", date(2026, 9, 24), interval="years")


async def test_request_error_message_includes_response_body(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """The raised error includes the response body, so the server's actual reason is visible."""
    aresponses.add(
        HOST,
        "/oauth/token",
        "POST",
        aresponses.Response(status=400, text=orjson.dumps({"error": "too_many_requests"}).decode()),
    )

    with pytest.raises(ZonneplanAuthenticationError, match="too_many_requests"):
        await zonneplan_client._request("oauth/token", method="POST", authenticated=False)


@pytest.mark.parametrize(
    ("headers", "retry_after"),
    [
        ({"Retry-After": "30"}, 30),
        ({"Retry-After": "Wed, 30 Sep 2026 07:28:00 GMT"}, None),
        ({}, None),
    ],
)
async def test_request_429_raises_rate_limit_error(aresponses: ResponsesMockServer, headers: dict[str, str], retry_after: int | None) -> None:
    """A 429 raises ZonneplanRateLimitError with the Retry-After seconds, and isn't retried."""
    aresponses.add(HOST, "/user-accounts/me", "GET", aresponses.Response(status=429, headers=headers, text="{}"))

    async with Zonneplan(email="user@example.com", max_retries=3) as client:
        with pytest.raises(ZonneplanRateLimitError) as exc_info:
            await client._request("user-accounts/me")

    assert exc_info.value.retry_after == retry_after
    aresponses.assert_plan_strictly_followed()


@pytest.mark.parametrize(
    ("status", "exception"),
    [
        (404, ZonneplanNotFoundError),
        (409, ZonneplanRequestError),
        (422, ZonneplanRequestError),
    ],
)
async def test_request_4xx_raises_request_error_without_retry(aresponses: ResponsesMockServer, status: int, exception: type[Exception]) -> None:
    """A 4xx the caller caused raises a ZonneplanRequestError, and isn't retried."""
    aresponses.add(HOST, "/connections/conn-1/pv-installation", "GET", aresponses.Response(status=status, text="{}"))

    async with Zonneplan(email="user@example.com", max_retries=3) as client:
        with pytest.raises(exception):
            await client._request("connections/conn-1/pv-installation")

    aresponses.assert_plan_strictly_followed()


async def test_request_5xx_is_retried_for_get(aresponses: ResponsesMockServer, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 5xx on a GET is retried, and raises ZonneplanConnectionError once the retries run out."""
    monkeypatch.setattr("pyzonneplan.pyzonneplan.wait_exponential", lambda **_: wait_none())
    for _ in range(2):
        aresponses.add(HOST, "/user-accounts/me", "GET", aresponses.Response(status=503, text="{}"))

    async with Zonneplan(email="user@example.com", max_retries=1) as client:
        with pytest.raises(ZonneplanConnectionError):
            await client._request("user-accounts/me")

    aresponses.assert_plan_strictly_followed()


async def test_request_5xx_is_not_retried_for_post(aresponses: ResponsesMockServer) -> None:
    """A POST is sent once, even on a 5xx: repeating it could apply an action twice."""
    aresponses.add(HOST, "/connections/conn-1/charge-points/cp-1/actions/start_boost", "POST", aresponses.Response(status=503, text="{}"))

    async with Zonneplan(email="user@example.com", max_retries=3) as client:
        with pytest.raises(ZonneplanConnectionError):
            await client._request("connections/conn-1/charge-points/cp-1/actions/start_boost", method="POST")

    aresponses.assert_plan_strictly_followed()


@pytest.mark.parametrize(
    "body",
    [
        '{"data": null}',
        "null",
        '{"data": {"user_account": {"uuid": "u-1"}}}',
        '{"data": {"user_account": "not an object"}}',
    ],
    ids=["data_null", "body_null", "missing_field", "wrong_type"],
)
async def test_unexpected_response_raises_response_error(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, body: str) -> None:
    """A response of the wrong shape raises ZonneplanResponseError, not a bare TypeError or ValueError."""
    aresponses.add(HOST, "/user-accounts/me", "GET", aresponses.Response(text=body))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(ZonneplanResponseError, match="Account"):
        await zonneplan_client.async_get_account()


async def test_invalid_json_raises_response_error(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A body that isn't JSON raises ZonneplanResponseError."""
    aresponses.add(HOST, "/user-accounts/me", "GET", aresponses.Response(text="<html>Maintenance</html>"))

    with pytest.raises(ZonneplanResponseError, match="Maintenance"):
        await zonneplan_client._request("user-accounts/me")


async def test_empty_body_returns_none(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A 200 without a body returns None, like a 204."""
    aresponses.add(HOST, "/connections/conn-1/charge-points/cp-1/actions/start_boost", "POST", aresponses.Response(text=""))

    assert await zonneplan_client._request("connections/conn-1/charge-points/cp-1/actions/start_boost", method="POST") is None


async def test_request_sends_app_headers(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Requests carry the app version and environment the API expects, with the library's own user agent."""
    aresponses.add(HOST, "/user-accounts/me", "GET", aresponses.Response(text="{}"))

    await zonneplan_client._request("user-accounts/me")

    headers = aresponses.history[0].request.headers
    assert headers["x-app-version"] == "6.23.0"
    assert headers["x-app-environment"] == "production"
    assert headers["user-agent"].startswith("pyzonneplan/")


async def test_unexpected_token_response_raises_response_error(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A token response without the expected fields raises ZonneplanResponseError."""
    aresponses.add(HOST, "/oauth/token", "POST", aresponses.Response(text=orjson.dumps({"access_token": "new-access"}).decode()))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(ZonneplanResponseError, match="token"):
        await zonneplan_client.async_refresh_token()


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


async def test_async_get_consumer_prices_for_connection(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion
) -> None:
    """A connection's own price chart (captured, values changed) is fetched from its path and parsed like the general one."""
    aresponses.add(
        HOST,
        "/api/connections/conn-1/consumer-prices/charts/electricity-quarter-hourly",
        "GET",
        aresponses.Response(text=load_fixtures("get_consumer_prices_connection_quarter_hourly.json")),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    prices = await zonneplan_client.async_get_consumer_prices(PriceChart.ELECTRICITY_QUARTER_HOURLY, connection_uuid="conn-1")

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


async def test_async_get_account_without_p1(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A real (scrubbed) account with electricity and gas contracts but no P1 meter is parsed."""
    aresponses.add(HOST, "/user-accounts/me", "GET", aresponses.Response(text=load_fixtures("get_account_no_p1.json")))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    account = await zonneplan_client.async_get_account()

    assert [connection.market_segment for connection in account.connections] == ["electricity", "gas"]
    assert not any(connection.has_p1 for connection in account.connections)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("async_get_electricity_delivered", "electricity-delivered"),
        ("async_get_electricity", "electricity"),
        ("async_get_gas", "gas"),
    ],
    ids=["electricity_delivered", "electricity", "gas"],
)
async def test_consumption_summary_without_p1_returns_none(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, method: str, path: str
) -> None:
    """Without a P1 meter the summary endpoints answer with a JSON null (captured)."""
    aresponses.add(
        HOST,
        f"/connections/conn-1/{path}",
        "GET",
        aresponses.Response(text=load_fixtures("get_consumption_null.json"), content_type="application/json"),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))

    assert await getattr(zonneplan_client, method)("conn-1") is None


async def test_async_get_electricity(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """The app's /electricity endpoint is fetched without charts and parsed like electricity-delivered."""
    aresponses.add(
        HOST,
        "/connections/conn-1/electricity?no-charts=1",
        "GET",
        aresponses.Response(text=load_fixtures("get_electricity_delivered.json")),
        match_querystring=True,
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    electricity = await zonneplan_client.async_get_electricity("conn-1")

    assert electricity == ElectricityDelivered.from_dict(orjson.loads(load_fixtures("get_electricity_delivered.json"))["data"])


async def test_async_get_electricity_home_consumption(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Home consumption is fetched without charts and parsed like electricity-delivered."""
    aresponses.add(
        HOST,
        "/connections/conn-1/electricity-home-consumption?no-charts=1",
        "GET",
        aresponses.Response(text=load_fixtures("get_electricity_delivered.json")),
        match_querystring=True,
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    consumption = await zonneplan_client.async_get_electricity_home_consumption("conn-1")

    assert consumption == ElectricityDelivered.from_dict(orjson.loads(load_fixtures("get_electricity_delivered.json"))["data"])


@pytest.mark.parametrize("body", ["null", '{"data": null}'], ids=["null", "null_data"])
async def test_async_get_electricity_home_consumption_without_p1(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, body: str) -> None:
    """Without a P1 meter home consumption is null (captured), with or without the data envelope."""
    aresponses.add(HOST, "/connections/conn-1/electricity-home-consumption", "GET", aresponses.Response(text=body))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    assert await zonneplan_client.async_get_electricity_home_consumption("conn-1") is None


async def test_async_get_recent_power(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion) -> None:
    """The recent power chart is fetched for the last 24 hours by default and flattened."""
    aresponses.add(
        HOST,
        "/api/connections/conn-1/electricity/charts/recent-power?duration=PT24H",
        "GET",
        aresponses.Response(text=load_fixtures("get_recent_power.json")),
        match_querystring=True,
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    chart = await zonneplan_client.async_get_recent_power("conn-1")

    assert chart == snapshot


@pytest.mark.parametrize("duration", [RecentPowerDuration.HOURS_12, "PT48H"], ids=["enum", "string"])
async def test_async_get_recent_power_duration(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, duration: str) -> None:
    """The duration is sent as given; a chart without a P1 meter has no measurements (captured)."""
    aresponses.add(
        HOST,
        f"/api/connections/conn-1/electricity/charts/recent-power?duration={duration}",
        "GET",
        aresponses.Response(text=load_fixtures("get_recent_power_no_p1.json")),
        match_querystring=True,
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    chart = await zonneplan_client.async_get_recent_power("conn-1", duration)

    assert chart.measurements == []
    assert chart.latest is None


async def test_async_get_recent_power_unknown_duration(zonneplan_client: Zonneplan) -> None:
    """An unsupported duration is rejected before anything is sent."""
    with pytest.raises(ValueError, match="PT6H"):
        await zonneplan_client.async_get_recent_power("conn-1", "PT6H")


@pytest.mark.parametrize(
    ("interval", "fixture"),
    [
        (ConsumptionChart.HOURS, "get_electricity_chart_hours.json"),
        (ConsumptionChart.HOURS, "get_electricity_chart_hours_pending.json"),
        (ConsumptionChart.DAYS, "get_electricity_chart_days.json"),
        (ConsumptionChart.MONTHS, "get_electricity_chart_months.json"),
    ],
)
async def test_async_get_electricity_chart(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion, interval: str, fixture: str
) -> None:
    """Captured electricity charts are parsed, with the date sent as a query parameter."""
    aresponses.add(
        HOST,
        f"/connections/conn-1/electricity-delivered/charts/{interval}?date=2026-09-24",
        "GET",
        aresponses.Response(text=load_fixtures(fixture)),
        match_querystring=True,
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    chart = await zonneplan_client.async_get_electricity_chart("conn-1", date(2026, 9, 24), interval)

    assert chart == snapshot


@pytest.mark.parametrize(
    ("interval", "fixture"),
    [
        (ConsumptionChart.HOURS, "get_gas_chart_hours.json"),
        (ConsumptionChart.HOURS, "get_gas_chart_hours_pending.json"),
        (ConsumptionChart.HOURS, "get_gas_chart_hours_today.json"),
        (ConsumptionChart.DAYS, "get_gas_chart_days.json"),
        (ConsumptionChart.MONTHS, "get_gas_chart_months.json"),
    ],
)
async def test_async_get_gas_chart(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion, interval: str, fixture: str
) -> None:
    """Captured gas charts are parsed, with the date sent as a query parameter."""
    aresponses.add(
        HOST,
        f"/connections/conn-1/gas/charts/{interval}?date=2026-09-24",
        "GET",
        aresponses.Response(text=load_fixtures(fixture)),
        match_querystring=True,
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    chart = await zonneplan_client.async_get_gas_chart("conn-1", date(2026, 9, 24), interval)

    assert chart == snapshot


@pytest.mark.parametrize(
    ("method", "product", "fixture"),
    [
        ("async_get_electricity_consumption_insight", "electricity", "get_electricity_consumption_insight.json"),
        ("async_get_gas_consumption_insight", "gas", "get_gas_consumption_insight.json"),
    ],
    ids=["electricity", "gas"],
)
async def test_async_get_consumption_insight(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion, *, method: str, product: str, fixture: str
) -> None:
    """A consumption insight chart is fetched per month for the date range, converting Wh or dm³ and 1e-7 EUR."""
    aresponses.add(
        HOST,
        f"/api/connections/conn-1/{product}/charts/consumption-insight?start_date=2026-01-01&end_date=2026-12-31&granularity=months",
        "GET",
        aresponses.Response(text=load_fixtures(fixture)),
        match_querystring=True,
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    insight = await getattr(zonneplan_client, method)("conn-1", date(2026, 1, 1), date(2026, 12, 31))

    assert insight == snapshot


async def test_async_get_consumption_insight_invalid_range(zonneplan_client: Zonneplan) -> None:
    """A start after the end is rejected before anything is sent."""
    with pytest.raises(ValueError, match="start"):
        await zonneplan_client.async_get_gas_consumption_insight("conn-1", date(2026, 12, 31), date(2026, 1, 1))


async def test_async_get_summary(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion) -> None:
    """A real /summary response is unwrapped and parsed, with the price forecast's ``datetime`` as ``start``."""
    aresponses.add(HOST, "/connections/conn-1/summary", "GET", aresponses.Response(text=load_fixtures("get_summary.json")))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    summary = await zonneplan_client.async_get_summary("conn-1")

    assert summary == snapshot


async def test_async_set_locale(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """set_locale PUTs the locale to user-accounts/locale."""
    aresponses.add(HOST, "/user-accounts/locale", "PUT", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_set_locale("nl-NL")

    assert await aresponses.history[0].request.json() == {"locale": "nl-NL"}
    aresponses.assert_plan_strictly_followed()


async def test_async_get_app_settings(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """The app settings (captured) are turned from a key/value list into a dict."""
    aresponses.add(HOST, "/user-accounts/app-settings", "GET", aresponses.Response(text=load_fixtures("get_app_settings.json")))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    settings = await zonneplan_client.async_get_app_settings()

    assert settings.settings == {"price_resolution": "60-min"}
    assert settings.price_resolution is PriceResolution.HOUR


@pytest.mark.parametrize("resolution", [PriceResolution.QUARTER_HOUR, "60-min"], ids=["enum", "string"])
async def test_async_set_price_resolution(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, resolution: str) -> None:
    """The price resolution is PUT as an app setting."""
    aresponses.add(HOST, "/user-accounts/app-settings", "PUT", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_set_price_resolution(resolution)

    assert await aresponses.history[0].request.json() == {"settings": {"price_resolution": str(resolution)}}
    aresponses.assert_plan_strictly_followed()


async def test_async_set_price_resolution_unknown(zonneplan_client: Zonneplan) -> None:
    """An unknown price resolution is rejected before anything is sent."""
    with pytest.raises(ValueError, match="30-min"):
        await zonneplan_client.async_set_price_resolution("30-min")


@pytest.mark.parametrize(
    ("method", "args", "path", "fixture"),
    [
        ("async_get_pv_installation", ("conn-1",), "/connections/conn-1/pv-installation", "get_pv_installation.json"),
        ("async_get_battery", ("conn-1", "bat-1"), "/connections/conn-1/home-battery-installation/bat-1", "get_battery_installation.json"),
        ("async_get_battery_status", ("bat-1",), "/api/contracts/bat-1/home-battery/product/status", "get_battery_status.json"),
        (
            "async_get_battery_activity_chart",
            ("bat-1",),
            "/api/contracts/bat-1/home-battery/charts/recent-activity",
            "get_battery_activity_chart.json",
        ),
        (
            "async_get_battery_activity_chart",
            ("bat-1",),
            "/api/contracts/bat-1/home-battery/charts/recent-activity",
            "get_battery_activity_chart_day_ahead.json",
        ),
        ("async_get_battery_control_mode", ("bat-1",), "/api/contracts/bat-1/home-battery/control-mode", "get_battery_control_mode.json"),
        (
            "async_get_battery_home_optimization",
            ("bat-1",),
            "/api/contracts/bat-1/home-battery/control-mode/home_optimization",
            "get_battery_home_optimization.json",
        ),
        (
            "async_get_battery_day_ahead_arbitrage",
            ("bat-1",),
            "/api/contracts/bat-1/home-battery/control-mode/day_ahead_arbitrage",
            "get_battery_day_ahead_arbitrage.json",
        ),
        ("async_get_charge_point", ("conn-1", "cp-1"), "/connections/conn-1/charge-points/cp-1", "get_charge_point.json"),
        ("async_get_p1_installation", ("p1-1",), "/contracts/p1-1/p1-installation", "get_p1_installation.json"),
        (
            "async_get_charge_point_control_mode",
            ("cp-1",),
            "/api/contracts/cp-1/charge-point/control-mode",
            "get_charge_point_control_mode.json",
        ),
        (
            "async_get_charge_point_charge_on_solar",
            ("cp-1",),
            "/api/contracts/cp-1/charge-point/control-mode/charge_on_solar",
            "get_charge_point_charge_on_solar.json",
        ),
    ],
    ids=[
        "pv_installation",
        "battery",
        "battery_status",
        "battery_activity_chart",
        "battery_activity_chart_day_ahead",
        "battery_control_mode",
        "battery_home_optimization",
        "battery_day_ahead_arbitrage",
        "charge_point",
        "p1_installation",
        "charge_point_control_mode",
        "charge_point_charge_on_solar",
    ],
)
async def test_device_reads(
    aresponses: ResponsesMockServer,
    zonneplan_client: Zonneplan,
    snapshot: SnapshotAssertion,
    *,
    method: str,
    args: tuple[str, ...],
    path: str,
    fixture: str,
) -> None:
    """Each device endpoint is fetched from its path and parsed into its model."""
    aresponses.add(HOST, path, "GET", aresponses.Response(text=load_fixtures(fixture)))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    result = await getattr(zonneplan_client, method)(*args)

    assert result == snapshot


async def test_async_get_pv_installation_without_panels(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A connection without solar panels answers null (captured), which returns None."""
    aresponses.add(HOST, "/connections/conn-1/pv-installation", "GET", aresponses.Response(text=load_fixtures("get_consumption_null.json")))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    assert await zonneplan_client.async_get_pv_installation("conn-1") is None


async def test_async_get_p1_installation_without_p1(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Without a P1 installation the API answers 404 (captured), which returns None."""
    aresponses.add(
        HOST,
        "/contracts/p1-1/p1-installation",
        "GET",
        aresponses.Response(status=404, text=orjson.dumps({"message": "Not found"}).decode()),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    assert await zonneplan_client.async_get_p1_installation("p1-1") is None


async def test_async_get_battery_chart(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, snapshot: SnapshotAssertion) -> None:
    """The battery chart is fetched for the date and interval, and taken from the response's first element."""
    aresponses.add(
        HOST,
        "/contracts/bat-1/home_battery_installation/charts/months?date=2026-01-01",
        "GET",
        aresponses.Response(text=load_fixtures("get_battery_chart_days.json")),
        match_querystring=True,
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    chart = await zonneplan_client.async_get_battery_chart("bat-1", date(2026, 1, 1), ChartInterval.MONTHS)

    assert chart == snapshot


@pytest.mark.parametrize(
    ("method", "args", "path", "fixture"),
    [
        (
            "async_get_battery_asset_costs_chart",
            ("bat-1", date(2026, 9, 1), date(2026, 9, 30)),
            "/api/contracts/bat-1/home-battery/charts/asset-costs?start_date=2026-09-01&end_date=2026-09-30&granularity=days",
            "get_battery_asset_costs_chart.json",
        ),
        (
            "async_get_battery_asset_costs_chart",
            ("bat-1", date(2026, 1, 1), date(2026, 12, 31), ChartInterval.MONTHS),
            "/api/contracts/bat-1/home-battery/charts/asset-costs?start_date=2026-01-01&end_date=2026-12-31&granularity=months",
            "get_battery_asset_costs_chart.json",
        ),
        (
            "async_get_battery_cumulative_asset_costs_chart",
            ("bat-1",),
            "/api/contracts/bat-1/home-battery/charts/asset-costs-cumulative",
            "get_battery_asset_costs_cumulative_chart.json",
        ),
        (
            "async_get_battery_cumulative_asset_costs_chart",
            ("bat-1", date(2026, 6, 27), date(2026, 9, 24)),
            "/api/contracts/bat-1/home-battery/charts/asset-costs-cumulative?start_date=2026-06-27&end_date=2026-09-24",
            "get_battery_asset_costs_cumulative_chart.json",
        ),
        (
            "async_get_battery_self_sufficiency_chart",
            ("conn-1", 2026),
            "/api/connections/conn-1/home-battery/charts/self-sufficiency?year=2026",
            "get_battery_self_sufficiency_chart.json",
        ),
        (
            "async_get_battery_self_sufficiency_stats",
            ("conn-1", 2026),
            "/api/connections/conn-1/home-battery/self-sufficiency/stats?year=2026",
            "get_battery_self_sufficiency_stats.json",
        ),
    ],
    ids=["asset_costs_days", "asset_costs_months", "cumulative", "cumulative_range", "self_sufficiency", "self_sufficiency_stats"],
)
async def test_battery_earnings_charts(
    aresponses: ResponsesMockServer,
    zonneplan_client: Zonneplan,
    snapshot: SnapshotAssertion,
    *,
    method: str,
    args: tuple[Any, ...],
    path: str,
    fixture: str,
) -> None:
    """Each battery earnings chart is fetched with its query parameters and parsed into its model."""
    aresponses.add(HOST, path, "GET", aresponses.Response(text=load_fixtures(fixture)), match_querystring=True)

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    result = await getattr(zonneplan_client, method)(*args)

    assert result == snapshot


async def test_battery_asset_costs_chart_unknown_interval(zonneplan_client: Zonneplan) -> None:
    """An unknown interval is rejected before anything is sent."""
    with pytest.raises(ValueError, match="hours"):
        await zonneplan_client.async_get_battery_asset_costs_chart("bat-1", date(2026, 9, 1), date(2026, 9, 1), "hours")


@pytest.mark.parametrize("data", [[], [None]], ids=["empty", "null_chart"])
async def test_async_get_battery_chart_empty(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, data: list[None]) -> None:
    """A response without a chart returns None."""
    aresponses.add(
        HOST,
        "/contracts/bat-1/home_battery_installation/charts/days?date=2026-09-01",
        "GET",
        aresponses.Response(text=orjson.dumps({"data": data}).decode()),
        match_querystring=True,
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    assert await zonneplan_client.async_get_battery_chart("bat-1", date(2026, 9, 1)) is None


@pytest.mark.parametrize(
    ("method", "kwargs", "action", "body"),
    [
        ("async_enable_battery_self_consumption", {}, "enable_self_consumption", {}),
        ("async_disable_battery_self_consumption", {}, "disable_self_consumption", {}),
        ("async_enable_battery_home_optimization", {}, "enable_home_optimization", {}),
        (
            "async_enable_battery_home_optimization",
            {"max_charge_power_w": 2000, "max_discharge_power_w": 800},
            "enable_home_optimization",
            {"max_desired_charge_power_w": 2000, "max_desired_discharge_power_w": 800},
        ),
        ("async_disable_battery_home_optimization", {}, "disable_home_optimization", {}),
        (
            "async_set_battery_backup_reserve",
            {"reserved_wh": 1500},
            "set_backup_power_reserved_state_of_charge",
            {"reserved_state_of_charge_wh": 1500},
        ),
    ],
    ids=[
        "enable_self_consumption",
        "disable_self_consumption",
        "enable_home_optimization",
        "enable_home_optimization_limits",
        "disable_home_optimization",
        "backup_reserve",
    ],
)
async def test_battery_actions(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, *, method: str, kwargs: dict[str, int], action: str, body: dict[str, int]
) -> None:
    """Each battery action POSTs its body to the battery's action path."""
    aresponses.add(HOST, f"/connections/conn-1/home-battery-installation/bat-1/actions/{action}", "POST", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await getattr(zonneplan_client, method)("conn-1", "bat-1", **kwargs)

    assert await aresponses.history[0].request.json() == body
    aresponses.assert_plan_strictly_followed()


async def test_enable_battery_home_optimization_needs_both_limits(zonneplan_client: Zonneplan) -> None:
    """Only one power limit is rejected before anything is sent, as the API takes both or neither."""
    with pytest.raises(ValueError, match="both"):
        await zonneplan_client.async_enable_battery_home_optimization("conn-1", "bat-1", max_charge_power_w=2000)


@pytest.mark.parametrize(
    ("mode", "enabled", "actions"),
    [
        (BatteryMode.SELF_CONSUMPTION, [BatteryMode.HOME_OPTIMIZATION], ["enable_self_consumption", "disable_home_optimization"]),
        (BatteryMode.SELF_CONSUMPTION, [], ["enable_self_consumption"]),
        (BatteryMode.HOME_OPTIMIZATION, [BatteryMode.SELF_CONSUMPTION], ["enable_home_optimization", "disable_self_consumption"]),
        (
            BatteryMode.DYNAMIC_CHARGING,
            [BatteryMode.SELF_CONSUMPTION, BatteryMode.HOME_OPTIMIZATION],
            ["disable_home_optimization", "disable_self_consumption"],
        ),
        (BatteryMode.DYNAMIC_CHARGING, [], []),
        (BatteryMode.DYNAMIC_CHARGING, [BatteryMode.DAY_AHEAD_ARBITRAGE], ["disable_home_optimization", "disable_self_consumption"]),
        (BatteryMode.SELF_CONSUMPTION, [BatteryMode.DAY_AHEAD_ARBITRAGE], ["enable_self_consumption"]),
    ],
    ids=[
        "self_consumption",
        "self_consumption_already_alone",
        "home_optimization",
        "dynamic_charging",
        "dynamic_charging_already",
        "dynamic_charging_from_day_ahead",
        "self_consumption_from_day_ahead",
    ],
)
async def test_set_battery_control_mode(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, mode: str, enabled: list[str], actions: list[str]
) -> None:
    """Switching mode enables the new one first, then disables whichever other mode was on."""
    control_mode = {
        "control_mode": "self_consumption",
        "processing": False,
        "modes": {
            name.value: {"enabled": name in enabled, "available": True}
            for name in (BatteryMode.SELF_CONSUMPTION, BatteryMode.HOME_OPTIMIZATION, BatteryMode.DAY_AHEAD_ARBITRAGE)
        },
    }
    aresponses.add(
        HOST, "/api/contracts/bat-1/home-battery/control-mode", "GET", aresponses.Response(text=orjson.dumps({"data": control_mode}).decode())
    )
    for action in actions:
        aresponses.add(HOST, f"/connections/conn-1/home-battery-installation/bat-1/actions/{action}", "POST", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_set_battery_control_mode("conn-1", "bat-1", mode)

    assert [entry.request.path.rsplit("/", 1)[-1] for entry in aresponses.history[1:]] == actions
    aresponses.assert_plan_strictly_followed()


async def test_set_battery_control_mode_day_ahead(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Day-ahead arbitrage is enabled again with its current settings."""
    path = "/api/contracts/bat-1/home-battery/control-mode/day_ahead_arbitrage"
    aresponses.add(HOST, path, "GET", aresponses.Response(text=load_fixtures("get_battery_day_ahead_arbitrage.json")))
    aresponses.add(HOST, path, "PUT", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_set_battery_control_mode("conn-1", "bat-1", BatteryMode.DAY_AHEAD_ARBITRAGE)

    assert await aresponses.history[1].request.json() == {"price_spread": 800000, "market_time_resolution": "PT15M"}
    aresponses.assert_plan_strictly_followed()


async def test_set_battery_control_mode_day_ahead_without_settings(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Day-ahead arbitrage that was never set up can't be enabled without settings."""
    aresponses.add(
        HOST,
        "/api/contracts/bat-1/home-battery/control-mode/day_ahead_arbitrage",
        "GET",
        aresponses.Response(text=orjson.dumps({"data": {"configured_price_spread": None, "market_time_resolution": None}}).decode()),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(ValueError, match="no settings"):
        await zonneplan_client.async_set_battery_control_mode("conn-1", "bat-1", BatteryMode.DAY_AHEAD_ARBITRAGE)
    aresponses.assert_plan_strictly_followed()


@pytest.mark.parametrize(
    ("price_spread", "resolution", "body"),
    [
        (Decimal("0.08"), MarketTimeResolution.QUARTER_HOUR, {"price_spread": 800000, "market_time_resolution": "PT15M"}),
        (0.29, "PT60M", {"price_spread": 2900000, "market_time_resolution": "PT60M"}),
    ],
    ids=["decimal", "float"],
)
async def test_enable_battery_day_ahead_arbitrage(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, *, price_spread: Decimal | float, resolution: str, body: dict[str, int | str]
) -> None:
    """The price spread is sent in 1e-7 EUR, without float rounding errors."""
    aresponses.add(HOST, "/api/contracts/bat-1/home-battery/control-mode/day_ahead_arbitrage", "PUT", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_enable_battery_day_ahead_arbitrage("bat-1", price_spread=price_spread, market_time_resolution=resolution)

    assert await aresponses.history[0].request.json() == body
    aresponses.assert_plan_strictly_followed()


async def test_enable_battery_day_ahead_arbitrage_unknown_resolution(zonneplan_client: Zonneplan) -> None:
    """An unknown market time resolution is rejected before anything is sent."""
    with pytest.raises(ValueError, match="PT30M"):
        await zonneplan_client.async_enable_battery_day_ahead_arbitrage("bat-1", price_spread=Decimal("0.08"), market_time_resolution="PT30M")


async def test_set_battery_control_mode_unknown(zonneplan_client: Zonneplan) -> None:
    """An unknown mode is rejected before anything is sent."""
    with pytest.raises(ValueError, match="Unknown battery control mode"):
        await zonneplan_client.async_set_battery_control_mode("conn-1", "bat-1", "manual")


@pytest.mark.parametrize(
    ("method", "action"),
    [
        ("async_start_charge_point_boost", "start_boost"),
        ("async_stop_charge_point", "stop_charging"),
        ("async_resume_charge_point_auto_charging", "unsuppress_always_flex"),
        ("async_stop_charge_point_boost", "stop_boost"),
        ("async_enable_charge_point_plug_and_charge", "enable_plug_and_charge"),
        ("async_disable_charge_point_plug_and_charge", "disable_plug_and_charge"),
        ("async_enable_charge_point_always_flex", "enable_always_flex"),
        ("async_disable_charge_point_always_flex", "disable_always_flex"),
    ],
)
async def test_charge_point_actions(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, method: str, action: str) -> None:
    """Each charge point action POSTs an empty body to the charge point's action path."""
    aresponses.add(HOST, f"/connections/conn-1/charge-points/cp-1/actions/{action}", "POST", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await getattr(zonneplan_client, method)("conn-1", "cp-1")

    assert await aresponses.history[0].request.json() == {}
    aresponses.assert_plan_strictly_followed()


@pytest.mark.parametrize("stop_status", [204, 422], ids=["session_running", "no_session"])
async def test_reset_charge_point_schedule(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, stop_status: int) -> None:
    """Resetting the schedule stops a dynamic session first, and carries on when there is none to stop."""
    actions = "/connections/conn-1/charge-points/cp-1/actions"
    aresponses.add(HOST, f"{actions}/stop_dynamic_charging_session", "POST", aresponses.Response(status=stop_status, text="{}"))
    aresponses.add(HOST, f"{actions}/reset_schedule", "POST", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_reset_charge_point_schedule("conn-1", "cp-1")

    aresponses.assert_plan_strictly_followed()


async def test_enable_charge_point_charge_on_solar(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Settings are sent the way the app sends them: whole minutes as an ISO 8601 duration, whole hours as HH:00."""
    aresponses.add(HOST, "/api/contracts/cp-1/charge-point/control-mode/charge_on_solar/enable", "PUT", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_enable_charge_point_charge_on_solar(
        "cp-1", solar_surplus_threshold_w=2000, min_charge_duration=timedelta(minutes=30), allowed_window_start=time(5), allowed_window_end=time(23)
    )

    assert await aresponses.history[0].request.json() == {
        "solar_surplus_threshold_w": 2000,
        "min_charge_duration": "PT30M",
        "allowed_window": {"start_time": "05:00", "end_time": "23:00"},
    }
    aresponses.assert_plan_strictly_followed()


async def test_enable_charge_point_charge_on_solar_keeps_current_settings(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Omitted settings keep their current value."""
    path = "/api/contracts/cp-1/charge-point/control-mode/charge_on_solar"
    aresponses.add(HOST, path, "GET", aresponses.Response(text=load_fixtures("get_charge_point_charge_on_solar.json")))
    aresponses.add(HOST, f"{path}/enable", "PUT", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_enable_charge_point_charge_on_solar("cp-1", min_charge_duration=timedelta(hours=1))

    assert await aresponses.history[1].request.json() == {
        "solar_surplus_threshold_w": 1400,
        "min_charge_duration": "PT60M",
        "allowed_window": {"start_time": "10:00", "end_time": "16:00"},
    }
    aresponses.assert_plan_strictly_followed()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"min_charge_duration": timedelta(minutes=15, seconds=30)}, "whole minutes"),
        ({"min_charge_duration": timedelta(0)}, "whole minutes"),
        ({"allowed_window_start": time(5, 30)}, "whole local hour"),
        ({"allowed_window_end": time(23, tzinfo=UTC)}, "whole local hour"),
        ({"allowed_window_start": time(16), "allowed_window_end": time(10)}, "after"),
    ],
    ids=["partial_minute", "zero_duration", "start_not_on_hour", "end_with_timezone", "end_before_start"],
)
async def test_enable_charge_point_charge_on_solar_invalid(zonneplan_client: Zonneplan, kwargs: dict[str, Any], match: str) -> None:
    """Settings the API can't take are rejected before anything is sent."""
    settings: dict[str, Any] = {
        "solar_surplus_threshold_w": 2000,
        "min_charge_duration": timedelta(minutes=15),
        "allowed_window_start": time(5),
        "allowed_window_end": time(23),
    }
    with pytest.raises(ValueError, match=match):
        await zonneplan_client.async_enable_charge_point_charge_on_solar("cp-1", **(settings | kwargs))


async def test_enable_charge_point_charge_on_solar_without_current_settings(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Omitted settings can't be kept when charge on solar has none yet."""
    aresponses.add(
        HOST,
        "/api/contracts/cp-1/charge-point/control-mode/charge_on_solar",
        "GET",
        aresponses.Response(
            text=orjson.dumps({"data": {"solar_surplus_threshold": None, "min_charge_duration": None, "allowed_window": None}}).decode()
        ),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(ValueError, match="no current settings"):
        await zonneplan_client.async_enable_charge_point_charge_on_solar("cp-1", solar_surplus_threshold_w=2000)
    aresponses.assert_plan_strictly_followed()


async def test_disable_charge_point_charge_on_solar(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Charge on solar is disabled with a PUT without a body."""
    aresponses.add(HOST, "/api/contracts/cp-1/charge-point/control-mode/charge_on_solar/disable", "PUT", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_disable_charge_point_charge_on_solar("cp-1")

    assert await aresponses.history[0].request.read() == b""
    aresponses.assert_plan_strictly_followed()


@pytest.mark.parametrize(
    ("kwargs", "vehicle"),
    [({"vehicle_uuid": "v-1"}, {"vehicle": {"vehicle_uuid": "v-1"}}), ({}, {})],
    ids=["vehicle", "no_vehicle"],
)
async def test_set_charge_point_schedule(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, kwargs: dict[str, str], vehicle: dict[str, dict[str, str]]
) -> None:
    """Windows are sent in UTC with milliseconds; ended ones are dropped and one from an earlier hour starts at the current hour."""
    aresponses.add(HOST, "/connections/conn-1/charge-points/cp-1/actions/set_schedule", "POST", aresponses.Response(status=204))
    amsterdam = ZoneInfo("Europe/Amsterdam")
    current_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    tomorrow = (current_hour + timedelta(days=1)).astimezone(amsterdam)
    schedule = [
        ChargeSchedule(start_time=current_hour - timedelta(hours=3), end_time=current_hour - timedelta(hours=1)),
        ChargeSchedule(start_time=current_hour - timedelta(hours=2), end_time=current_hour + timedelta(hours=2)),
        ChargeSchedule(start_time=tomorrow, end_time=tomorrow + timedelta(hours=2), suppress_home_battery=True),
        ChargeSchedule(start_time=tomorrow + timedelta(hours=5)),
    ]

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_set_charge_point_schedule("conn-1", "cp-1", schedule, **kwargs)

    def utc(moment: datetime) -> str:
        return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    assert await aresponses.history[0].request.json() == {
        "schedule": [
            {"start_time": utc(current_hour), "end_time": utc(current_hour + timedelta(hours=2))},
            {"start_time": utc(tomorrow), "end_time": utc(tomorrow + timedelta(hours=2)), "suppress_home_battery": True},
            {"start_time": utc(tomorrow + timedelta(hours=5))},
        ],
        **vehicle,
    }
    aresponses.assert_plan_strictly_followed()


@pytest.mark.parametrize(
    ("window", "match"),
    [
        (ChargeSchedule(end_time=datetime(2030, 1, 1, 12, tzinfo=UTC)), "timezone-aware"),
        (ChargeSchedule(start_time=datetime(2030, 1, 1, 12)), "timezone-aware"),
        (ChargeSchedule(start_time=datetime(2030, 1, 1, 12, tzinfo=UTC), end_time=datetime(2030, 1, 1, 14)), "timezone-aware"),
        (ChargeSchedule(start_time=datetime(2030, 1, 1, 12, tzinfo=UTC), end_time=datetime(2030, 1, 1, 12, tzinfo=UTC)), "after"),
        (ChargeSchedule(start_time=datetime(2020, 1, 1, 12, tzinfo=UTC), end_time=datetime(2020, 1, 1, 14, tzinfo=UTC)), "reset"),
    ],
    ids=["no_start", "naive_start", "naive_end", "end_not_after_start", "only_ended"],
)
async def test_set_charge_point_schedule_invalid(zonneplan_client: Zonneplan, window: ChargeSchedule, match: str) -> None:
    """An invalid schedule, or one with only ended windows, is rejected before anything is sent."""
    with pytest.raises(ValueError, match=match):
        await zonneplan_client.async_set_charge_point_schedule("conn-1", "cp-1", [window])


@pytest.mark.parametrize(
    ("current", "mode", "actions"),
    [
        ("AlwaysFlex", ChargePointStartMode.PLUG_AND_CHARGE, ["enable_plug_and_charge"]),
        ("AlwaysFlex", ChargePointStartMode.START_WITH_APP, ["disable_always_flex"]),
        ("PlugAndCharge", ChargePointStartMode.ALWAYS_FLEX, ["enable_always_flex"]),
        ("PlugAndCharge", ChargePointStartMode.START_WITH_APP, ["disable_plug_and_charge"]),
        ("StartWithApp", ChargePointStartMode.ALWAYS_FLEX, ["enable_always_flex"]),
        ("StartWithApp", ChargePointStartMode.PLUG_AND_CHARGE, ["enable_plug_and_charge"]),
        ("StartWithApp", ChargePointStartMode.START_WITH_APP, []),
    ],
    ids=[
        "always_flex_to_plug_and_charge",
        "always_flex_to_start_with_app",
        "plug_and_charge_to_always_flex",
        "plug_and_charge_to_start_with_app",
        "start_with_app_to_always_flex",
        "start_with_app_to_plug_and_charge",
        "already",
    ],
)
async def test_set_charge_point_start_mode(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, current: str, mode: str, actions: list[str]
) -> None:
    """Switching start mode sends the one action the app sends from the current mode."""
    installation = orjson.loads(load_fixtures("get_charge_point.json"))
    installation["data"]["contracts"][0]["state"]["start_mode"] = current
    aresponses.add(HOST, "/connections/conn-1/charge-points/cp-1", "GET", aresponses.Response(text=orjson.dumps(installation).decode()))
    for action in actions:
        aresponses.add(HOST, f"/connections/conn-1/charge-points/cp-1/actions/{action}", "POST", aresponses.Response(status=204))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_set_charge_point_start_mode("conn-1", "cp-1", mode)

    assert [entry.request.path.rsplit("/", 1)[-1] for entry in aresponses.history[1:]] == actions
    aresponses.assert_plan_strictly_followed()


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda contracts: contracts.clear(), id="no_charge_point"),
        pytest.param(lambda contracts: contracts[0]["state"].update(start_mode="Manual"), id="unknown_mode"),
    ],
)
async def test_set_charge_point_start_mode_current_unknown(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, change: Callable[[list[dict[str, Any]]], None]
) -> None:
    """Without a known current start mode, nothing is sent."""
    installation = orjson.loads(load_fixtures("get_charge_point.json"))
    change(installation["data"]["contracts"])
    aresponses.add(HOST, "/connections/conn-1/charge-points/cp-1", "GET", aresponses.Response(text=orjson.dumps(installation).decode()))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(ValueError, match="unknown"):
        await zonneplan_client.async_set_charge_point_start_mode("conn-1", "cp-1", ChargePointStartMode.ALWAYS_FLEX)
    aresponses.assert_plan_strictly_followed()


async def test_set_charge_point_start_mode_unknown(zonneplan_client: Zonneplan) -> None:
    """An unknown start mode is rejected before anything is sent."""
    with pytest.raises(ValueError, match="Unknown charge point start mode"):
        await zonneplan_client.async_set_charge_point_start_mode("conn-1", "cp-1", "boost")


@pytest.mark.parametrize(
    ("kwargs", "amount"),
    [
        ({"kilometers": 150, "vehicle_uuid": "v-1"}, {"unit": "kilometers", "value": 150}),
        ({"percentage": 40}, {"unit": "percentage", "value": 400}),
    ],
    ids=["kilometers", "percentage"],
)
async def test_start_charge_point_dynamic_session(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, kwargs: dict[str, Any], amount: dict[str, Any]
) -> None:
    """The session is started with the end time in Amsterdam local time, the amount (percentage in permille) and the vehicle."""
    aresponses.add(HOST, "/connections/conn-1/charge-points/cp-1/actions/start_dynamic_charging_session", "POST", aresponses.Response(status=204))
    end = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=10)

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_start_charge_point_dynamic_session("conn-1", "cp-1", end, **kwargs)

    body = await aresponses.history[0].request.json()
    local_end = end.astimezone(ZoneInfo("Europe/Amsterdam")).strftime("%Y-%m-%d %H:%M:00")
    assert body["user_constraints"] == {"desired_end_time": local_end, **amount}
    assert body.get("vehicle") == ({"vehicle_uuid": "v-1"} if "vehicle_uuid" in kwargs else None)


@pytest.mark.parametrize(
    ("end", "kwargs", "match"),
    [
        (timedelta(hours=10), {}, "exactly one"),
        (timedelta(hours=10), {"kilometers": 150, "percentage": 40}, "exactly one"),
        (timedelta(minutes=5), {"kilometers": 150}, "15 minutes"),
        (None, {"kilometers": 150}, "timezone-aware"),
    ],
    ids=["no_amount", "both_amounts", "too_soon", "naive_end"],
)
async def test_start_charge_point_dynamic_session_invalid(
    zonneplan_client: Zonneplan, end: timedelta | None, kwargs: dict[str, Any], match: str
) -> None:
    """Invalid constraints are rejected before anything is sent."""
    moment = datetime(2030, 1, 1, 12) if end is None else datetime.now(UTC) + end
    with pytest.raises(ValueError, match=match):
        await zonneplan_client.async_start_charge_point_dynamic_session("conn-1", "cp-1", moment, **kwargs)


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
        aresponses.Response(
            text=orjson.dumps(
                {
                    "data": {
                        "user_account": {"uuid": "u-1", "email": "user@example.com", "first_name": "Test", "full_name": "Test User", "initials": "TU"}
                    }
                }
            ).decode()
        ),
    )

    zonneplan_client._token = Token(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    account = await zonneplan_client.async_get_account()

    assert zonneplan_client._token.access_token == "new-access"
    assert account.user_account.uuid == "u-1"


async def test_concurrent_requests_refresh_the_token_once(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Requests that find the token expired together refresh it once, as the refresh token rotates."""
    aresponses.add(
        HOST,
        "/oauth/token",
        "POST",
        aresponses.Response(text=orjson.dumps({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}).decode()),
    )
    for _ in range(2):
        aresponses.add(
            HOST, "/api/consumer-prices/charts/gas-daily", "GET", aresponses.Response(text=load_fixtures("get_consumer_prices_gas_daily.json"))
        )

    zonneplan_client._token = Token(access_token="old-access", refresh_token="old-refresh", expires_at=datetime.now(UTC) - timedelta(hours=1))
    await asyncio.gather(
        zonneplan_client.async_get_consumer_prices(PriceChart.GAS_DAILY),
        zonneplan_client.async_get_consumer_prices(PriceChart.GAS_DAILY),
    )

    assert zonneplan_client._token.refresh_token == "new-refresh"
    aresponses.assert_plan_strictly_followed()


async def test_seeded_token_is_used_without_login(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A token passed in at construction time is usable without an OTP login first."""
    seeded = Token(access_token="seeded-access", refresh_token="seeded-refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    client = Zonneplan(email="user@example.com", session=zonneplan_client._session, max_retries=0, token=seeded)

    assert client.token is seeded

    aresponses.add(
        HOST,
        "/user-accounts/me",
        "GET",
        aresponses.Response(
            text=orjson.dumps(
                {
                    "data": {
                        "user_account": {"uuid": "u-1", "email": "user@example.com", "first_name": "Test", "full_name": "Test User", "initials": "TU"}
                    }
                }
            ).decode()
        ),
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


async def test_async_logout(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Logging out POSTs an empty body with the access token, then forgets the token."""
    aresponses.add(HOST, "/auth/logout", "POST", aresponses.Response(text=orjson.dumps({"data": None}).decode()))

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    await zonneplan_client.async_logout()

    request = aresponses.history[0].request
    assert request.headers["authorization"] == "Bearer access"
    assert await request.json() == {}
    assert zonneplan_client.token is None
    aresponses.assert_plan_strictly_followed()


async def test_async_logout_failure_keeps_token(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """A failed logout raises and keeps the token, so it can be retried."""
    aresponses.add(HOST, "/auth/logout", "POST", aresponses.Response(status=500, text="{}"))

    token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    zonneplan_client._token = token
    with pytest.raises(ZonneplanConnectionError):
        await zonneplan_client.async_logout()

    assert zonneplan_client.token is token


async def test_async_logout_without_login(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan) -> None:
    """Logging out before login sends nothing."""
    await zonneplan_client.async_logout()

    assert zonneplan_client.token is None
    aresponses.assert_plan_strictly_followed()


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
