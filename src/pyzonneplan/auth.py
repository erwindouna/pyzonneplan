"""Authentication for the Zonneplan API.

Zonneplan uses an OAuth2 authorization-code flow with PKCE, but the user-facing
step is a one-time password mailed to the account address instead of a browser
redirect. The flow is three calls:

1. POST /oauth/authorize-challenge  {response_type, email, code_challenge, ...}
   -> HTTP 403 with ``otp_required: true`` and an ``auth_session``.
      The 403 is the success path, not an error.
2. POST /oauth/authorize-challenge  {auth_session, otp}
   -> ``authorization_code``
3. POST /oauth/token  {grant_type: authorization_code, code, code_verifier}
   -> access + refresh token
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any, Self, cast

import orjson
from aiohttp import ClientError, ClientSession, ClientTimeout
from yarl import URL

from .const import (
    API_HOST,
    API_SCHEME,
    APP_ENVIRONMENT,
    APP_VERSION,
    AUTHORIZE_CHALLENGE_PATH,
    TOKEN_PATH,
)
from .exceptions import (
    ZonneplanAuthenticationError,
    ZonneplanConnectionError,
    ZonneplanInvalidOtpError,
    ZonneplanTimeoutError,
)

# Refresh a little before the token actually expires.
_EXPIRY_MARGIN = timedelta(seconds=60)


def generate_pkce_pair() -> tuple[str, str]:
    """Return a fresh ``(code_verifier, code_challenge)`` pair."""
    verifier = secrets.token_urlsafe(96)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@dataclass(slots=True)
class OtpChallenge:
    """State carried between requesting and submitting the one-time password."""

    auth_session: str
    code_verifier: str
    email: str


@dataclass(slots=True)
class Token:
    """An access token and its refresh token."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    token_type: str = "Bearer"  # noqa: S105 (a token type label, not a credential)

    @property
    def is_expired(self) -> bool:
        """Return whether the access token needs refreshing."""
        return datetime.now(UTC) >= self.expires_at - _EXPIRY_MARGIN

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> Self:
        """Build a token from an /oauth/token response body."""
        return cls(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=datetime.now(UTC) + timedelta(seconds=int(payload["expires_in"])),
            token_type=payload.get("token_type", "Bearer"),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage in a Home Assistant config entry."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Restore a token previously stored with :meth:`as_dict`."""
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            token_type=data.get("token_type", "Bearer"),
        )


@dataclass
class ZonneplanAuth:
    """Handles the OTP login and token refresh against the Zonneplan API."""

    session: ClientSession
    request_timeout: float = 10.0
    _headers: dict[str, str] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        """Build the static request headers the API expects."""
        self._headers = {
            "content-type": "application/json;charset=utf-8",
            "x-app-version": APP_VERSION,
            "x-app-environment": APP_ENVIRONMENT,
        }

    async def async_request_otp(self, email: str, source_name: str) -> OtpChallenge:
        """Start the login and have Zonneplan mail a one-time password."""
        code_verifier, code_challenge = generate_pkce_pair()
        payload = {
            "response_type": "code",
            "email": email,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "source_name": source_name[:255],
        }

        response = await self._post(AUTHORIZE_CHALLENGE_PATH, payload, allow_forbidden=True)

        if not response.get("otp_required") or "auth_session" not in response:
            msg = "Zonneplan did not return an OTP challenge"
            raise ZonneplanAuthenticationError(msg)

        return OtpChallenge(
            auth_session=response["auth_session"],
            code_verifier=code_verifier,
            email=email,
        )

    async def async_submit_otp(self, challenge: OtpChallenge, otp: str) -> Token:
        """Exchange the mailed one-time password for a token."""
        response = await self._post(
            AUTHORIZE_CHALLENGE_PATH,
            {"auth_session": challenge.auth_session, "otp": otp},
        )

        authorization_code = response.get("authorization_code")
        if not authorization_code:
            msg = "Zonneplan rejected the one-time password"
            raise ZonneplanInvalidOtpError(msg)

        return await self._async_request_token(
            {
                "grant_type": "authorization_code",
                "code": authorization_code,
                "code_verifier": challenge.code_verifier,
            }
        )

    async def async_refresh_token(self, token: Token) -> Token:
        """Exchange a refresh token for a new access token."""
        return await self._async_request_token({"grant_type": "refresh_token", "refresh_token": token.refresh_token})

    async def _async_request_token(self, grant: dict[str, str]) -> Token:
        """Call the token endpoint and parse the result."""
        return Token.from_response(await self._post(TOKEN_PATH, grant))

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        allow_forbidden: bool = False,
    ) -> dict[str, Any]:
        """POST to an unauthenticated auth endpoint."""
        url = URL.build(scheme=API_SCHEME, host=API_HOST, path="/").joinpath(path)

        try:
            async with self.session.post(
                url,
                json=payload,
                headers=self._headers,
                timeout=ClientTimeout(total=self.request_timeout),
            ) as response:
                if response.status == HTTPStatus.FORBIDDEN and allow_forbidden:
                    return cast("dict[str, Any]", orjson.loads(await response.read()))

                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    msg = f"Zonneplan rejected the authentication request ({response.status})"
                    raise ZonneplanAuthenticationError(msg)

                response.raise_for_status()
                return cast("dict[str, Any]", orjson.loads(await response.read()))

        except TimeoutError as exception:
            msg = "Timeout while authenticating with Zonneplan"
            raise ZonneplanTimeoutError(msg) from exception
        except (ClientError, socket.gaierror) as exception:
            msg = "Error while communicating with Zonneplan"
            raise ZonneplanConnectionError(msg) from exception
