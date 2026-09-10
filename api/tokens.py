"""OAuth token storage, in the macOS Keychain.

A refresh token is a standing key to your Google data. It does not belong in .env:
that file is plaintext, gets swept into backups, and shows up whenever anything
cats the directory. The Keychain is encrypted at rest and access-controlled by the
OS (Plan.md §22).

`keyring` picks the right backend per platform — Keychain on macOS, libsecret on
Linux, Credential Manager on Windows — so nothing here is mac-specific.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import keyring
from keyring.errors import KeyringError

SERVICE = "personal-os"

# Refresh this many seconds before actual expiry. Without a margin, a token that
# passes the check and then expires mid-flight produces a spurious 401.
EXPIRY_MARGIN = timedelta(seconds=60)


class TokenStoreError(RuntimeError):
    """The Keychain could not be read or written."""


@dataclass
class StoredTokens:
    access_token: str
    refresh_token: str
    # ISO 8601. Stored as an absolute instant rather than the `expires_in` seconds
    # Google returns, because a duration is meaningless once written to disk.
    expires_at: str
    scope: str

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= (
            datetime.fromisoformat(self.expires_at) - EXPIRY_MARGIN
        )

    @classmethod
    def from_response(cls, payload: dict, *, previous: "StoredTokens | None" = None):
        """Build from Google's token response.

        The important subtlety: a REFRESH response contains no refresh_token.
        Google issues that once, on first consent. Overwriting the stored one with
        the missing value would silently destroy the credential and force a
        reconnect on the next run — so the previous value is carried forward.
        """
        refresh = payload.get("refresh_token") or (previous.refresh_token if previous else "")
        if not refresh:
            raise TokenStoreError(
                "No refresh token available. Reconnect with prompt=consent."
            )

        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(payload.get("expires_in", 3600))
        )

        return cls(
            access_token=payload["access_token"],
            refresh_token=refresh,
            expires_at=expires_at.isoformat(),
            scope=payload.get("scope", previous.scope if previous else ""),
        )


def save(provider: str, tokens: StoredTokens) -> None:
    try:
        keyring.set_password(SERVICE, provider, json.dumps(asdict(tokens)))
    except KeyringError as exc:
        raise TokenStoreError(f"Could not write to the keychain: {exc}") from exc


def load(provider: str) -> StoredTokens | None:
    """Stored tokens, or None if this provider was never connected."""
    try:
        raw = keyring.get_password(SERVICE, provider)
    except KeyringError as exc:
        raise TokenStoreError(f"Could not read the keychain: {exc}") from exc

    if not raw:
        return None

    try:
        return StoredTokens(**json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        # Corrupt or written by an older version. Treat as disconnected rather than
        # crashing — reconnecting is cheap, and a hard failure here would block
        # startup over a recoverable problem.
        return None


def delete(provider: str) -> None:
    """Disconnect. Note this only removes OUR copy.

    Google keeps the grant until it is revoked at
    myaccount.google.com/permissions — worth surfacing in the UI, since users
    reasonably assume "disconnect" revokes access everywhere.
    """
    try:
        keyring.delete_password(SERVICE, provider)
    except keyring.errors.PasswordDeleteError:
        pass  # already gone
    except KeyringError as exc:
        raise TokenStoreError(f"Could not delete from the keychain: {exc}") from exc
