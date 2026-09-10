"""Google OAuth — consent, code exchange, and silent refresh.

The production version of docs/experiments/oauth/manual_flow.py. Same flow; the
difference is that tokens are persisted and refresh happens automatically.

The distinction this module exists to get right:

    access token expired   -> refresh silently, the user never knows
    refresh token dead     -> the user must reconnect, and must be TOLD

Conflating them produces the two classic bugs: retrying forever on a dead
credential, or bouncing the user to a consent screen every hour.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import httpx

from api import tokens as token_store
from api.tokens import StoredTokens, TokenStoreError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Least privilege (Plan.md §22). Calendar is read-only — nothing here creates
# events. Tasks is read/write because completing a task from the Today view is a
# core capability (§24), and viewing without completing would be half a feature.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks",
]

CALLBACK_PORT = 8000
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/google/callback"

PROVIDER = "google"


class NeedsReconnect(RuntimeError):
    """The refresh token is dead. Only the user can fix this.

    Deliberately distinct from a transient error: nothing should retry on it, and
    the UI must present it as a normal state rather than a failure. Unverified apps
    get 7-day refresh tokens, so this happens roughly weekly by design.
    """


class OAuthNotConfigured(RuntimeError):
    """No client credentials file found."""


@dataclass
class ClientCredentials:
    client_id: str
    client_secret: str


def load_client_credentials(repo_root: Path) -> ClientCredentials:
    """Find the OAuth client JSON downloaded from Google Cloud Console.

    Matched by content rather than filename: Google names the download after
    whatever you called the client, so there is no reliable name to look for.
    """
    for path in sorted(repo_root.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        # 'installed' marks a Desktop app client, which is what allows an
        # arbitrary localhost redirect port.
        if isinstance(data, dict) and "installed" in data:
            client = data["installed"]
            if "client_id" in client and "client_secret" in client:
                return ClientCredentials(client["client_id"], client["client_secret"])

    raise OAuthNotConfigured(
        "No Google OAuth client JSON in the project root. Download it from "
        "Google Cloud Console > Credentials (Desktop app)."
    )


def build_consent_url(credentials: ClientCredentials, state: str) -> str:
    """The URL the user visits to approve access."""
    return f"{AUTH_URL}?" + urlencode(
        {
            "client_id": credentials.client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # offline: issue a refresh token, not just a one-hour access token.
            "access_type": "offline",
            # Force the consent screen. Google returns a refresh_token only on
            # first consent, so without this a reconnect yields no refresh token
            # and the connection breaks again in an hour.
            "prompt": "consent",
            "state": state,
        }
    )


def new_state() -> str:
    """Random value echoed back by Google, to prove the response is ours (CSRF)."""
    return secrets.token_urlsafe(24)


def exchange_code(credentials: ClientCredentials, code: str) -> StoredTokens:
    """Swap the one-time code for tokens and store them."""
    response = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    response.raise_for_status()

    stored = StoredTokens.from_response(response.json())
    token_store.save(PROVIDER, stored)
    return stored


def refresh(credentials: ClientCredentials, current: StoredTokens) -> StoredTokens:
    """Get a new access token. No browser, no user interaction.

    A 400 or 401 here means the refresh token itself is dead — revoked, or expired
    because this app is unverified. That is NeedsReconnect, never a retry.
    """
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "refresh_token": current.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )

    if response.status_code in (400, 401):
        detail = response.json().get("error_description", response.text[:200])
        raise NeedsReconnect(f"Google rejected the refresh token: {detail}")

    response.raise_for_status()

    # `previous` matters: a refresh response has no refresh_token, and overwriting
    # the stored one with nothing would destroy the credential.
    stored = StoredTokens.from_response(response.json(), previous=current)
    token_store.save(PROVIDER, stored)
    return stored


def get_access_token(repo_root: Path) -> str:
    """A valid access token, refreshing if needed.

    The single entry point for every Google API call. Callers never think about
    expiry — they either get a token or a clear reason they cannot have one.
    """
    current = token_store.load(PROVIDER)
    if current is None:
        raise NeedsReconnect("Google is not connected.")

    if not current.is_expired:
        return current.access_token

    credentials = load_client_credentials(repo_root)
    return refresh(credentials, current).access_token


def connection_status(repo_root: Path) -> dict:
    """What Settings shows. Never raises — an unconfigured integration is normal."""
    try:
        load_client_credentials(repo_root)
    except OAuthNotConfigured as exc:
        return {"connected": False, "state": "not_configured", "detail": str(exc)}

    try:
        current = token_store.load(PROVIDER)
    except TokenStoreError as exc:
        return {"connected": False, "state": "error", "detail": str(exc)}

    if current is None:
        return {"connected": False, "state": "disconnected", "detail": None}

    return {
        "connected": True,
        "state": "expired" if current.is_expired else "connected",
        "detail": None,
        "scopes": current.scope.split(),
        "expiresAt": current.expires_at,
    }
