"""Tests for pyzonneplan.auth."""

from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import pytest
from freezegun import freeze_time

from pyzonneplan.auth import Token, generate_pkce_pair


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
