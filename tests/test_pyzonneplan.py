"""Tests for pyzonneplan.pyzonneplan."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import orjson
import pytest
from aiohttp import ClientConnectionError
from aresponses import ResponsesMockServer
from tenacity import wait_none

from pyzonneplan import Zonneplan
from pyzonneplan.auth import OtpChallenge, Token
from pyzonneplan.const import BatteryMode, ChartInterval, ConsumptionChart, PriceChart
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


@pytest.mark.parametrize("path", ["electricity-delivered", "gas"])
async def test_consumption_summary_without_p1_returns_none(aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, path: str) -> None:
    """Without a P1 meter the summary endpoints answer with a JSON null."""
    aresponses.add(
        HOST,
        f"/connections/conn-1/{path}",
        "GET",
        aresponses.Response(text=load_fixtures("get_consumption_null.json"), content_type="application/json"),
    )

    zonneplan_client._token = Token(access_token="access", refresh_token="refresh", expires_at=datetime.now(UTC) + timedelta(hours=1))
    method = zonneplan_client.async_get_electricity_delivered if path == "electricity-delivered" else zonneplan_client.async_get_gas

    assert await method("conn-1") is None


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


@pytest.mark.parametrize(
    ("method", "args", "path", "fixture"),
    [
        ("async_get_pv_installation", ("conn-1",), "/connections/conn-1/pv-installation", "get_pv_installation.json"),
        ("async_get_battery", ("conn-1", "bat-1"), "/connections/conn-1/home-battery-installation/bat-1", "get_battery_installation.json"),
        ("async_get_battery_control_mode", ("bat-1",), "/api/contracts/bat-1/home-battery/control-mode", "get_battery_control_mode.json"),
        (
            "async_get_battery_home_optimization",
            ("bat-1",),
            "/api/contracts/bat-1/home-battery/control-mode/home_optimization",
            "get_battery_home_optimization.json",
        ),
        ("async_get_charge_point", ("conn-1", "cp-1"), "/connections/conn-1/charge-points/cp-1", "get_charge_point.json"),
    ],
    ids=["pv_installation", "battery", "battery_control_mode", "battery_home_optimization", "charge_point"],
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
    ],
    ids=["self_consumption", "self_consumption_already_alone", "home_optimization", "dynamic_charging", "dynamic_charging_already"],
)
async def test_set_battery_control_mode(
    aresponses: ResponsesMockServer, zonneplan_client: Zonneplan, mode: str, enabled: list[str], actions: list[str]
) -> None:
    """Switching mode enables the new one first, then disables whichever other mode was on."""
    control_mode = {
        "control_mode": "self_consumption",
        "processing": False,
        "modes": {name: {"enabled": name in enabled, "available": True} for name in (BatteryMode.SELF_CONSUMPTION, BatteryMode.HOME_OPTIMIZATION)},
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


async def test_set_battery_control_mode_unknown(zonneplan_client: Zonneplan) -> None:
    """An unknown mode is rejected before anything is sent."""
    with pytest.raises(ValueError, match="Unknown battery control mode"):
        await zonneplan_client.async_set_battery_control_mode("conn-1", "bat-1", "manual")


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
