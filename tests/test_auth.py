"""Tests for pyzonneplan.auth."""

from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
import pytest
from aiohttp import ClientSession
from aresponses import ResponsesMockServer
from freezegun import freeze_time

from pyzonneplan.auth import Token, ZonneplanAuth, generate_pkce_pair
from pyzonneplan.exceptions import (
    ZonneplanAuthenticationError,
    ZonneplanConnectionError,
    ZonneplanInvalidOtpError,
    ZonneplanTimeoutError,
)

HOST = "app-api.zonneplan.nl"


def test_generate_pkce_pair() -> None:
    """The challenge is the base64url-encoded SHA256 digest of the verifier."""
    verifier, challenge = generate_pkce_pair()

    assert 0 < len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert challenge == urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_generate_pkce_pair_is_random() -> None:
    """Two calls must not yield the same verifier."""
    first, _ = generate_pkce_pair()
    second, _ = generate_pkce_pair()
    assert first != second


def test_token_from_response() -> None:
    """A token is built from an /oauth/token response body."""
    with freeze_time("2026-01-01T00:00:00+00:00"):
        token = Token.from_response({"access_token": "access", "refresh_token": "refresh", "expires_in": "3600"})

    assert token.access_token == "access"
    assert token.refresh_token == "refresh"
    assert token.token_type == "Bearer"
    assert token.expires_at == datetime(2026, 1, 1, 1, 0, tzinfo=UTC)


def test_token_as_dict_and_from_dict_roundtrip() -> None:
    """A token survives a dict round-trip unchanged."""
    token = Token(
        access_token="access",
        refresh_token="refresh",
        expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        token_type="Bearer",
    )

    assert Token.from_dict(token.as_dict()) == token


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(hours=1), False),
        (timedelta(seconds=30), True),
        (-timedelta(seconds=1), True),
    ],
)
def test_token_is_expired(offset: timedelta, *, expected: bool) -> None:
    """A token is considered expired inside the refresh margin."""
    with freeze_time("2026-01-01T00:00:00+00:00"):
        token = Token(
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + offset,
        )
        assert token.is_expired is expected


async def test_async_request_otp_success(aresponses: ResponsesMockServer) -> None:
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

    async with ClientSession() as session:
        auth = ZonneplanAuth(session=session)
        challenge = await auth.async_request_otp("user@example.com", "pytest")

    assert challenge.auth_session == "sess-123"
    assert challenge.email == "user@example.com"


async def test_async_request_otp_without_challenge_raises(aresponses: ResponsesMockServer) -> None:
    """A 403 that is not an OTP challenge is a real authentication failure."""
    aresponses.add(
        HOST,
        "/oauth/authorize-challenge",
        "POST",
        aresponses.Response(status=403, text=orjson.dumps({"error": "blocked"}).decode()),
    )

    async with ClientSession() as session:
        auth = ZonneplanAuth(session=session)
        with pytest.raises(ZonneplanAuthenticationError):
            await auth.async_request_otp("user@example.com", "pytest")


async def test_async_submit_otp_success(aresponses: ResponsesMockServer) -> None:
    """A valid OTP is exchanged for an authorization code, then a token."""
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

    from pyzonneplan.auth import OtpChallenge

    async with ClientSession() as session:
        auth = ZonneplanAuth(session=session)
        challenge = OtpChallenge(auth_session="sess-123", code_verifier="verifier", email="user@example.com")
        token = await auth.async_submit_otp(challenge, "123456")

    assert token.access_token == "access"
    assert token.refresh_token == "refresh"


async def test_async_submit_otp_invalid_raises(aresponses: ResponsesMockServer) -> None:
    """A rejected OTP has no authorization_code in the response."""
    aresponses.add(
        HOST,
        "/oauth/authorize-challenge",
        "POST",
        aresponses.Response(text=orjson.dumps({}).decode()),
    )

    from pyzonneplan.auth import OtpChallenge

    async with ClientSession() as session:
        auth = ZonneplanAuth(session=session)
        challenge = OtpChallenge(auth_session="sess-123", code_verifier="verifier", email="user@example.com")
        with pytest.raises(ZonneplanInvalidOtpError):
            await auth.async_submit_otp(challenge, "000000")


async def test_async_refresh_token(aresponses: ResponsesMockServer) -> None:
    """A refresh token is exchanged for a new access token."""
    aresponses.add(
        HOST,
        "/oauth/token",
        "POST",
        aresponses.Response(text=orjson.dumps({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}).decode()),
    )

    async with ClientSession() as session:
        auth = ZonneplanAuth(session=session)
        old_token = Token(access_token="old-access", refresh_token="old-refresh", expires_at=datetime.now(UTC))
        new_token = await auth.async_refresh_token(old_token)

    assert new_token.access_token == "new-access"


async def test_post_unauthorized_raises_authentication_error(aresponses: ResponsesMockServer) -> None:
    """A 401 response is surfaced as an authentication error."""
    aresponses.add(HOST, "/oauth/token", "POST", aresponses.Response(status=401, text="{}"))

    async with ClientSession() as session:
        auth = ZonneplanAuth(session=session)
        with pytest.raises(ZonneplanAuthenticationError):
            await auth._async_request_token({"grant_type": "refresh_token", "refresh_token": "x"})


class _RaisingPost:
    """A ``session.post`` stand-in whose context manager raises on entry."""

    def __init__(self, exception: BaseException) -> None:
        self._exception = exception

    def __call__(self, *_args: Any, **_kwargs: Any) -> _RaisingPost:
        return self

    async def __aenter__(self) -> None:
        raise self._exception

    async def __aexit__(self, *_args: object) -> bool:
        return False


async def test_post_timeout_raises_zonneplan_timeout_error() -> None:
    """A network timeout is translated to ZonneplanTimeoutError."""
    async with ClientSession() as session:
        session.post = _RaisingPost(TimeoutError())  # type: ignore[assignment]
        auth = ZonneplanAuth(session=session)
        with pytest.raises(ZonneplanTimeoutError):
            await auth._post("oauth/token", {})


async def test_post_connection_error_raises_zonneplan_connection_error() -> None:
    """A connection failure is translated to ZonneplanConnectionError."""
    from aiohttp import ClientConnectionError

    async with ClientSession() as session:
        session.post = _RaisingPost(ClientConnectionError())  # type: ignore[assignment]
        auth = ZonneplanAuth(session=session)
        with pytest.raises(ZonneplanConnectionError):
            await auth._post("oauth/token", {})
