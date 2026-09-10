"""GitHub provider — repositories and recent activity. Read-only.

Satisfies ActivityService structurally, the same way ObsidianVaultProvider and the
Google providers satisfy theirs.

THREE WAYS GITHUB DIFFERS FROM GOOGLE, all handled here so nothing above notices:

  1. Auth is a personal access token. No refresh, no expiry we manage, no consent
     screen. Simpler — but it never rotates, so a leak lasts until revoked by hand.

  2. Pagination uses a `Link` header with rel="next", not a nextPageToken in the
     body. Same concept, different mechanism.

  3. Rate limiting returns 403, not 429 — with x-ratelimit-remaining: 0. So a 403
     here can be TRANSIENT, where a 403 from Google never is. Treating them the
     same would mean either retrying a permission error forever or giving up on a
     rate limit that would have cleared.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from api import tokens as token_store
from api.services import Activity, Repository

API = "https://api.github.com"
SECRET_NAME = "github_token"
TIMEOUT = 30

# GitHub asks clients to pin the API version; without it the shape can change
# under you when they ship a new one.
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubNotConnected(RuntimeError):
    """No personal access token stored."""


class GitHubError(RuntimeError):
    """A GitHub API call failed in a way retrying will not fix."""


class GitHubRateLimited(GitHubError):
    """Rate limit hit. Transient — unlike an ordinary 403."""


class GitHubProvider:
    source_id = "github"

    def __init__(self, token: str | None = None):
        self._token = token

    @property
    def token(self) -> str:
        token = self._token or token_store.load_secret(SECRET_NAME)
        if not token:
            raise GitHubNotConnected("GitHub is not connected.")
        return token

    # -- HTTP ------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        try:
            response = httpx.get(
                url,
                params=params,
                headers={**HEADERS, "Authorization": f"Bearer {self.token}"},
                timeout=TIMEOUT,
                follow_redirects=True,
            )
        except httpx.RequestError as exc:
            raise GitHubError(f"Could not reach GitHub: {exc}") from exc

        if response.is_success:
            return response

        if response.status_code == 401:
            raise GitHubNotConnected(
                "GitHub rejected the token. It may have been revoked or expired — "
                "create a new one and reconnect."
            )

        if response.status_code == 403:
            # The distinction Google does not have: 403 here might just be a rate
            # limit, which clears on its own.
            if response.headers.get("x-ratelimit-remaining") == "0":
                reset = response.headers.get("x-ratelimit-reset", "")
                when = ""
                if reset.isdigit():
                    at = datetime.fromtimestamp(int(reset), tz=timezone.utc)
                    when = f" Resets at {at:%H:%M UTC}."
                raise GitHubRateLimited(f"GitHub rate limit reached.{when}")
            raise GitHubError(
                "GitHub refused the request — the token likely lacks the required "
                "scope. A classic token needs 'repo'; a fine-grained one needs "
                "read access to repository contents and metadata."
            )

        raise GitHubError(f"HTTP {response.status_code}: {response.text[:200]}")

    def _paginate(self, url: str, params: dict | None = None, *, limit: int = 100):
        """Follow Link: rel="next" until exhausted or `limit` items are collected.

        The URL from the Link header already carries its own query string, so
        params are sent only on the FIRST request — re-sending them would fight
        with what GitHub put in the link.
        """
        collected: list[dict] = []
        next_url: str | None = url
        first = True

        while next_url and len(collected) < limit:
            response = self._get(next_url, params if first else None)
            first = False

            payload = response.json()
            items = payload if isinstance(payload, list) else payload.get("items", [])
            collected.extend(items)

            match = NEXT_LINK.search(response.headers.get("link", ""))
            next_url = match.group(1) if match else None

        return collected[:limit]

    # -- ActivityService -------------------------------------------------

    def whoami(self) -> str:
        """The authenticated username. Also the cheapest way to validate a token."""
        return self._get(f"{API}/user").json()["login"]

    def list_repositories(self, limit: int = 30) -> list[Repository]:
        """Repos you can push to, most recently pushed first."""
        items = self._paginate(
            f"{API}/user/repos",
            {"sort": "pushed", "per_page": 100, "affiliation": "owner,collaborator"},
            limit=limit,
        )
        return [_to_repository(item) for item in items]

    def list_activity(self, limit: int = 30) -> list[Activity]:
        """Your recent public events.

        Caveat worth knowing rather than discovering: this endpoint only returns
        PUBLIC events, and GitHub keeps roughly 90 days or 300 events, whichever
        is smaller. Private-repo work will not appear here at all — which is why
        repositories (with pushed_at) are the more reliable activity signal.
        """
        items = self._paginate(
            f"{API}/users/{self.whoami()}/events", {"per_page": 100}, limit=limit
        )
        return [event for item in items if (event := _to_activity(item))]


def _to_repository(item: dict) -> Repository:
    return Repository(
        provider_id=item["full_name"],
        name=item["name"],
        description=item.get("description"),
        pushed_at=_parse(item.get("pushed_at") or item["updated_at"]),
        language=item.get("language"),
        private=item.get("private", False),
        url=item.get("html_url"),
    )


def _to_activity(item: dict) -> Activity | None:
    """Map a GitHub event onto the domain type.

    GitHub has ~20 event types with completely different payload shapes. Only the
    few that mean 'I did some work' are mapped; the rest return None and are
    dropped, because a feed full of WatchEvents is noise rather than activity.
    """
    kind = item.get("type", "")
    repo = item.get("repo", {}).get("name", "unknown")
    payload = item.get("payload", {})

    if kind == "PushEvent":
        count = payload.get("size", 0)
        commits = payload.get("commits") or []
        first = commits[0]["message"].splitlines()[0] if commits else ""
        summary = f"{count} commit{'s' if count != 1 else ''}"
        if first:
            summary += f": {first[:70]}"
        mapped, url = "push", f"https://github.com/{repo}"

    elif kind == "PullRequestEvent":
        pr = payload.get("pull_request", {})
        summary = f"{payload.get('action', 'updated')} PR: {pr.get('title', '')[:70]}"
        mapped, url = "pull_request", pr.get("html_url")

    elif kind == "IssuesEvent":
        issue = payload.get("issue", {})
        summary = f"{payload.get('action', 'updated')} issue: {issue.get('title', '')[:70]}"
        mapped, url = "issue", issue.get("html_url")

    elif kind == "CreateEvent":
        ref_type = payload.get("ref_type", "")
        summary = f"created {ref_type} {payload.get('ref') or ''}".strip()
        mapped, url = "create", f"https://github.com/{repo}"

    else:
        return None

    return Activity(
        provider_id=item["id"],
        kind=mapped,
        summary=summary,
        repository=repo,
        occurred_at=_parse(item["created_at"]),
        url=url,
    )


def _parse(value: str) -> datetime:
    """GitHub returns RFC3339 with a Z suffix."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
