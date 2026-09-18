"""Assistant routes — asking questions of your own indexed data."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from api.assistant import AssistantUnavailable, ask
from api.captures import ConnDep
from api.notes import get_note_service

router = APIRouter(tags=["assistant"])


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AskRequest(CamelModel):
    question: str = Field(min_length=1, max_length=500)


class SourceOut(CamelModel):
    """One retrieved item, shown beneath the answer as attribution.

    Deliberately not a link. Citations here name where a claim came from so the
    answer can be checked; turning them into navigation is a different feature and
    only notes have anywhere to go.
    """

    id: UUID
    title: str
    source: str


class AskResponse(CamelModel):
    answer: str
    sources: list[SourceOut]


@router.post("/ask", response_model=AskResponse)
def ask_endpoint(payload: AskRequest, conn: ConnDep) -> AskResponse:
    """Answer a question from the vault, captures, calendar, tasks and repos.

    Retrieval is keyword-only, because that is all that exists — semantic search
    arrives in the AI layer once the keyword baseline's limits are demonstrable
    (ADR-007). The visible consequence is that asking in words the notes do not
    use returns nothing, and the model correctly says so. That is the evidence,
    not a defect.
    """
    try:
        answer = ask(conn, payload.question.strip(), get_note_service())
    except AssistantUnavailable as exc:
        # 503, not 500: a dependency is down, the request was fine. The UI can say
        # "start Ollama" rather than "something went wrong" — the same distinction
        # google_routes draws for an expired token.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return AskResponse(
        answer=answer.text,
        sources=[
            SourceOut(id=s.id, title=s.title, source=s.source) for s in answer.sources
        ],
    )
