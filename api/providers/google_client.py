"""Shared HTTP client for every Google API.

Calendar, Tasks, and later Gmail, Drive, and Sheets all sit behind one OAuth client
and hit the same infrastructure, so the awkward parts belong here once:

  · attaching the access token, and refreshing it when it expires
  · telling 401 (who are you) from 403 (you may not) — retrying the second is a bug
  · retry with exponential backoff on rate limits and server errors
  · pagination, which every Google list endpoint does the same way
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Iterator

import httpx

from api.oauth import NeedsReconnect, get_access_token

MAX_ATTEMPTS = 4
TIMEOUT = 30


class GoogleApiError(RuntimeError):
    """A Google API call failed in a way retrying will not fix."""


class PermissionDenied(GoogleApiError):
    """403. The token is valid but does not permit this.

    Almost always one of: a scope we never requested, or an API not enabled in
    Cloud Console. Refreshing cannot help — the credential is fine, the request is
    not — so this must never be retried.
    """


class GoogleClient:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def get(self, url: str, params: dict[str, Any] | None = None) -> dict:
        """One GET, with refresh-on-401 and backoff on transient failures."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        refreshed = False

        for attempt in range(MAX_ATTEMPTS):
            token = get_access_token(self.repo_root)

            try:
                response = httpx.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=TIMEOUT,
                )
            except httpx.RequestError as exc:
                # Network-level failure: no response at all. Worth one retry.
                if attempt == MAX_ATTEMPTS - 1:
                    raise GoogleApiError(f"Could not reach Google: {exc}") from exc
                self._backoff(attempt)
                continue

            if response.is_success:
                return response.json()

            if response.status_code == 401:
                # The stored token was rejected. get_access_token() only refreshes
                # on *known* expiry, so a token can still be revoked server-side
                # while looking valid locally. Force one refresh, then give up —
                # a second 401 means the credential is genuinely dead.
                if refreshed:
                    raise NeedsReconnect("Google rejected the access token twice.")
                refreshed = True
                self._force_refresh()
                continue

            if response.status_code == 403:
                raise PermissionDenied(self._explain_403(response))

            # 429 rate limit, 5xx server error: transient, back off and retry.
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_ATTEMPTS - 1:
                    raise GoogleApiError(
                        f"Google returned {response.status_code} after "
                        f"{MAX_ATTEMPTS} attempts."
                    )
                self._backoff(attempt, response)
                continue

            raise GoogleApiError(f"HTTP {response.status_code}: {response.text[:300]}")

        raise GoogleApiError("Exhausted retries.")

    def paginate(
        self, url: str, params: dict[str, Any] | None = None, *, limit: int = 250
    ) -> Iterator[dict]:
        """Walk a paginated list endpoint, yielding items.

        Google returns a page of results plus a nextPageToken; passing that token
        back asks for the following page, and its absence means the end. Every
        Google list API works this way, which is why it is written once here.

        `limit` is a stop, not a page size. Without one, a large calendar could
        page forever and a bug would look like a hang.
        """
        params = dict(params or {})
        yielded = 0

        while True:
            page = self.get(url, params)

            for item in page.get("items", []):
                yield item
                yielded += 1
                if yielded >= limit:
                    return

            token = page.get("nextPageToken")
            if not token:
                return
            params["pageToken"] = token

    def _force_refresh(self) -> None:
        """Refresh even though the stored token looks unexpired."""
        from api import tokens as token_store
        from api.oauth import PROVIDER, load_client_credentials, refresh

        current = token_store.load(PROVIDER)
        if current is None:
            raise NeedsReconnect("Google is not connected.")
        refresh(load_client_credentials(self.repo_root), current)

    @staticmethod
    def _backoff(attempt: int, response: httpx.Response | None = None) -> None:
        """Exponential backoff with jitter, respecting Retry-After when given.

        Jitter matters: without it, everything that failed together retries
        together, and the retries themselves become the next spike.
        """
        if response is not None and (header := response.headers.get("Retry-After")):
            try:
                time.sleep(min(float(header), 30))
                return
            except ValueError:
                pass

        time.sleep(min(2**attempt, 8) + random.uniform(0, 0.5))

    @staticmethod
    def _explain_403(response: httpx.Response) -> str:
        """Turn Google's 403 into something actionable.

        Its message for a disabled API does not mention enabling the API, which is
        why this failure is confusing the first time.
        """
        try:
            message = response.json()["error"].get("message", "")
        except Exception:
            message = response.text[:200]

        hint = ""
        lowered = message.lower()
        if "has not been used" in lowered or "disabled" in lowered:
            hint = " — enable this API in Google Cloud Console > APIs & Services."
        elif "insufficient" in lowered or "scope" in lowered:
            hint = " — the token lacks the required scope. Reconnect to re-consent."

        return f"{message}{hint}"
