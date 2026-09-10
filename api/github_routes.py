"""GitHub routes.

Connecting is a POST with a token, not a redirect dance — GitHub uses a personal
access token here, so there is no consent screen to bounce through. The token is
validated before it is stored: saving an unusable credential just moves the failure
somewhere less obvious.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from api import tokens as token_store
from api.providers.github import (
    SECRET_NAME,
    GitHubError,
    GitHubNotConnected,
    GitHubProvider,
    GitHubRateLimited,
)
from api.services import ActivityService

router = APIRouter(prefix="/github", tags=["github"])


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ConnectRequest(CamelModel):
    token: str = Field(min_length=8, max_length=255)


class RepositoryOut(CamelModel):
    id: str
    name: str
    description: str | None
    pushed_at: datetime
    language: str | None
    private: bool
    url: str | None
    source: str


class ActivityOut(CamelModel):
    id: str
    kind: str
    summary: str
    repository: str
    occurred_at: datetime
    url: str | None
    source: str


def _handle(exc: Exception) -> HTTPException:
    """Map provider failures onto status codes the UI can act on.

    429 for a rate limit rather than 403: it is transient, and the UI should say
    "try again shortly" rather than "you lack permission". GitHub itself conflates
    them; we do not.
    """
    if isinstance(exc, GitHubNotConnected):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, GitHubRateLimited):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, GitHubError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def get_activity_service() -> ActivityService:
    """The one place a concrete activity provider is named."""
    return GitHubProvider()


@router.get("/status")
def github_status() -> dict:
    """Never raises — an unconnected integration is a normal state."""
    if not token_store.load_secret(SECRET_NAME):
        return {"connected": False, "state": "disconnected"}
    try:
        return {
            "connected": True,
            "state": "connected",
            "username": GitHubProvider().whoami(),
        }
    except GitHubNotConnected as exc:
        # A stored-but-rejected token: revoked, or expired. Reporting it as
        # connected would be a lie the user cannot act on.
        return {"connected": False, "state": "invalid", "detail": str(exc)}
    except GitHubError as exc:
        return {"connected": True, "state": "error", "detail": str(exc)}


@router.post("/connect")
def github_connect(payload: ConnectRequest) -> dict:
    """Validate the token, then store it. Never the other way round."""
    try:
        username = GitHubProvider(token=payload.token).whoami()
    except Exception as exc:
        raise _handle(exc) from exc

    token_store.save_secret(SECRET_NAME, payload.token)
    return {"connected": True, "username": username}


@router.post("/disconnect")
def github_disconnect() -> dict:
    token_store.delete_secret(SECRET_NAME)
    return {
        "disconnected": True,
        "note": (
            "Token deleted locally. It remains valid on GitHub until you delete it "
            "at github.com/settings/tokens"
        ),
    }


@router.get("/repos", response_model=list[RepositoryOut])
def list_repos(limit: Annotated[int, Query(ge=1, le=100)] = 30) -> list[RepositoryOut]:
    service = get_activity_service()
    try:
        repos = service.list_repositories(limit=limit)
    except Exception as exc:
        raise _handle(exc) from exc

    return [
        RepositoryOut(
            id=r.provider_id,
            name=r.name,
            description=r.description,
            pushed_at=r.pushed_at,
            language=r.language,
            private=r.private,
            url=r.url,
            source=service.source_id,
        )
        for r in repos
    ]


@router.get("/activity", response_model=list[ActivityOut])
def list_activity(limit: Annotated[int, Query(ge=1, le=100)] = 30) -> list[ActivityOut]:
    service = get_activity_service()
    try:
        events = service.list_activity(limit=limit)
    except Exception as exc:
        raise _handle(exc) from exc

    return [
        ActivityOut(
            id=a.provider_id,
            kind=a.kind,
            summary=a.summary,
            repository=a.repository,
            occurred_at=a.occurred_at,
            url=a.url,
            source=service.source_id,
        )
        for a in events
    ]
