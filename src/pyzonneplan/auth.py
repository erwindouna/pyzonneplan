"""Authentication value objects for the Zonneplan API.

The actual login and refresh calls live on :class:`pyzonneplan.Zonneplan`, so
they share its request handling (retries, timeouts, error mapping). This
module only holds the data carried through that flow:

1. POST /oauth/authorize-challenge  {response_type, email, code_challenge, ...}
   -> HTTP 403 with ``otp_required: true`` and an ``auth_session``.
      The 403 is the success path, not an error.
2. POST /oauth/authorize-challenge  {auth_session, otp}
   -> ``authorization_code``
3. POST /oauth/token  {grant_type: authorization_code, code, code_verifier}
   -> access + refresh token
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self

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
