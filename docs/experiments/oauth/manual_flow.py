"""The OAuth flow, by hand, with nothing hidden.

No google-auth library. Every HTTP request is visible, so you can see exactly what
is sent and what comes back — which is the point. The production version uses a
library; this exists so you know what the library is doing.

    .venv/bin/python docs/experiments/oauth/manual_flow.py

It will open your browser, ask for consent, and print your next calendar event.

WHAT TO LOOK AT: step 4 prints the raw token response. Note that `refresh_token`
appears only on the FIRST consent — approve a second time and it is absent. That
surprises people and causes real bugs, so it is worth seeing once.
"""

from __future__ import annotations

import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

# Least privilege (Plan.md §22): read-only, calendar only. Not "your Google account".
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}/callback"


def rule(text: str) -> None:
    print(f"\n{'─' * 70}\n{text}\n{'─' * 70}")


# ---------------------------------------------------------------------------
# 1. Credentials
# ---------------------------------------------------------------------------


def load_client() -> tuple[str, str]:
    """Find the OAuth client JSON downloaded from Google Cloud Console."""
    candidates = [p for p in REPO_ROOT.glob("*.json") if "oauth" in p.name.lower()]
    candidates += list(REPO_ROOT.glob("client_secret*.json"))

    if not candidates:
        print("No OAuth client JSON found in the repo root.", file=sys.stderr)
        print("Download it from Google Cloud Console > Credentials.", file=sys.stderr)
        raise SystemExit(1)

    data = json.loads(candidates[0].read_text())

    # 'installed' = Desktop app. A 'web' client would need a registered redirect URI
    # and would reject the arbitrary localhost port used below.
    if "installed" not in data:
        print(f"Expected a Desktop app client, got: {list(data)}", file=sys.stderr)
        raise SystemExit(1)

    client = data["installed"]
    return client["client_id"], client["client_secret"]


# ---------------------------------------------------------------------------
# 2. Consent — catching the redirect
# ---------------------------------------------------------------------------


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches Google's redirect and pulls the one-time code out of the URL."""

    code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        CallbackHandler.code = params.get("code", [None])[0]
        CallbackHandler.state = params.get("state", [None])[0]
        CallbackHandler.error = params.get("error", [None])[0]

        message = (
            f"Authorisation failed: {CallbackHandler.error}"
            if CallbackHandler.error
            else "Authorised. Close this tab and return to the terminal."
        )
        body = f"<html><body style='font:16px sans-serif;padding:3rem'>{message}</body></html>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args) -> None:
        pass  # keep the terminal readable


def get_authorisation_code(client_id: str) -> str:
    """Send the user to Google, wait for the redirect to come back."""

    # `state` is a random value echoed back by Google. If what returns does not
    # match what we sent, the response did not originate from our request —
    # that is CSRF protection, and skipping it is a real vulnerability.
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        # offline: we want a REFRESH token, not just an access token. Without this
        # you get one hour of access and no way to renew it.
        "access_type": "offline",
        # Forces the consent screen even if already approved, which is the only way
        # to get a refresh_token back on a repeat run. Google issues one on FIRST
        # consent only.
        "prompt": "consent",
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    server = http.server.HTTPServer(("localhost", PORT), CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening your browser. If it does not open, paste this:\n")
    print(f"  {url}\n")
    webbrowser.open(url)

    thread.join(timeout=180)
    server.server_close()

    if CallbackHandler.error:
        print(f"\nGoogle refused: {CallbackHandler.error}", file=sys.stderr)
        if CallbackHandler.error == "access_denied":
            print("If you did approve, add your email under Audience > Test users.",
                  file=sys.stderr)
        raise SystemExit(1)

    if CallbackHandler.code is None:
        print("\nNo code received (timed out after 3 minutes).", file=sys.stderr)
        raise SystemExit(1)

    if CallbackHandler.state != state:
        print("\nstate mismatch — response did not match our request.", file=sys.stderr)
        raise SystemExit(1)

    return CallbackHandler.code


# ---------------------------------------------------------------------------
# 3. Exchange the code for tokens
# ---------------------------------------------------------------------------


def post_form(url: str, fields: dict[str, str]) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        print(f"\nHTTP {exc.code}: {exc.read().decode()}", file=sys.stderr)
        raise SystemExit(1) from exc


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    """Swap the one-time code for tokens.

    This happens server to server and needs the client secret, which is why
    intercepting the browser redirect gains an attacker nothing on its own.
    """
    return post_form(
        TOKEN_URL,
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Get a fresh access token without asking the user again.

    This is what runs silently forever after. A 401 HERE — rather than on an API
    call — means the refresh token is dead and the user must reconnect. That is a
    different situation from an expired access token and must not be retried.
    """
    return post_form(
        TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )


# ---------------------------------------------------------------------------
# 4. Use it
# ---------------------------------------------------------------------------


def next_events(access_token: str, count: int = 3) -> list[dict]:
    now = datetime.now(timezone.utc)
    params = {
        "timeMin": now.isoformat(),
        "timeMax": (now + timedelta(days=7)).isoformat(),
        "singleEvents": "true",  # expand recurring events into individual ones
        "orderBy": "startTime",  # only allowed when singleEvents is true
        "maxResults": str(count),
    }
    request = urllib.request.Request(
        f"{CALENDAR_URL}?{urllib.parse.urlencode(params)}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read()).get("items", [])
    except urllib.error.HTTPError as exc:
        # The distinction that matters: 401 = who are you (refresh and retry),
        # 403 = you may not (wrong scope, or the API is not enabled — retrying
        # forever will never fix it).
        meaning = {
            401: "access token expired or invalid — refresh it",
            403: "insufficient scope, or the Calendar API is not enabled",
        }.get(exc.code, "")
        print(f"\nHTTP {exc.code} {meaning}\n{exc.read().decode()[:400]}", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> int:
    client_id, client_secret = load_client()
    print(f"client_id ...{client_id[-30:]}")

    rule("1-2. Consent")
    code = get_authorisation_code(client_id)
    print(f"Got a one-time code: {code[:18]}…")
    print("Useless on its own — exchanging it needs the client secret.")

    rule("3. Exchange code for tokens")
    tokens = exchange_code(client_id, client_secret, code)

    print("Raw response from Google (secrets truncated):\n")
    for key, value in tokens.items():
        shown = f"{str(value)[:14]}… ({len(str(value))} chars)" if "token" in key else value
        print(f"  {key:16} {shown}")

    if "refresh_token" not in tokens:
        print("\n  NOTE: no refresh_token. Google issues one on FIRST consent only.")
        print("  prompt=consent should force it — if it is missing, this client was")
        print("  already authorised and not re-prompted.")

    rule("4. What the tokens mean")
    print(f"  access_token   expires in {tokens.get('expires_in')} seconds")
    print("                 sent with every API request")
    print("  refresh_token  a standing key to your data until revoked")
    print("                 -> belongs in the Keychain, never in .env")

    # Textbooks say refresh tokens do not expire. Google's own response says
    # otherwise for UNVERIFIED apps, and it is worth reading off the wire rather
    # than believing the general rule.
    if (ttl := tokens.get("refresh_token_expires_in")) is not None:
        days = int(ttl) / 86_400
        print(f"\n  refresh_token_expires_in: {ttl} seconds = {days:.1f} days")
        print("  This app is UNVERIFIED, so the refresh token DOES expire.")
        print("  Consequence: reconnecting is a normal state the UI must handle")
        print("  gracefully — not an error. (Phase 0 risk R3, confirmed.)")

    rule("5. Refreshing")
    if refresh := tokens.get("refresh_token"):
        refreshed = refresh_access_token(client_id, client_secret, refresh)
        same = refreshed["access_token"] == tokens["access_token"]
        print(f"  new access_token obtained, identical to the old one: {same}")
        print("  no browser, no consent screen — this is what runs silently forever")
        access_token = refreshed["access_token"]
    else:
        access_token = tokens["access_token"]

    rule("6. Calling the Calendar API")
    events = next_events(access_token)
    if not events:
        print("  No events in the next 7 days.")
    for event in events:
        start = event["start"].get("dateTime") or event["start"].get("date")
        print(f"  {start}  {event.get('summary', '(no title)')}")

    print("\nDone. Nothing was saved — the production version stores tokens in the")
    print("Keychain. This script exists so you have seen the flow with nothing hidden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
