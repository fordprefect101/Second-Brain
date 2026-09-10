"""Personal OS API.

Run from the repo root:

    .venv/bin/uvicorn api.main:app --reload

Layering (docs/architecture/phase-0.md §3):

    routes  ->  service interfaces  ->  providers  ->  filesystem / REST API

Routes speak domain vocabulary. Nothing above the provider layer knows that a note
is a file or that a task came from Google. This file holds the shell only — the
service interfaces arrive at step 7.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.captures import router as captures_router
from api.config import config
from api.github_routes import router as github_router
from api.google_routes import router as google_router
from api.notes import router as notes_router
from api.search_routes import router as search_router
from api.database import DatabaseUnavailable, connect, ensure_schema, EXPECTED_TABLES, list_tables

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("personal-os")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Apply the schema once at startup.

    Safe because every statement in schema.sql is IF NOT EXISTS (ADR-003). Doing it
    here rather than in a separate migration command means the app cannot run against
    a database whose schema was never applied — one less way to be half-configured.

    A failure here is deliberately fatal. Starting without a database would mean every
    request failing individually, which is a worse way to learn the container is down.
    """
    logger.info("Connecting to %s", config.safe_database_url)
    try:
        ensure_schema()
    except DatabaseUnavailable as exc:
        logger.error("Startup failed.\n%s", exc)
        raise
    logger.info("Schema applied. Personal OS API ready.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Personal OS",
    description="A unified control layer over existing digital tools.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(captures_router)
app.include_router(notes_router)
app.include_router(search_router)
app.include_router(google_router)
app.include_router(github_router)


@app.get("/health")
def health() -> JSONResponse:
    """Readiness, not liveness.

    A health check that returns 200 because the process is running tells you nothing
    you did not already know by connecting to it. This one actually reaches the
    database and reports 503 when it cannot, so "is the stack up?" has a real answer.

    Kept cheap on purpose: one round trip, no table scans. A health check heavy enough
    to matter becomes a load source of its own.
    """
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("select version()")
                version = cur.fetchone()[0]
            tables = list_tables(conn)
    except DatabaseUnavailable as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "database": {"connected": False, "url": config.safe_database_url},
                "detail": str(exc),
            },
        )

    missing = sorted(EXPECTED_TABLES - tables)
    return JSONResponse(
        status_code=200 if not missing else 503,
        content={
            "status": "ok" if not missing else "degraded",
            "database": {
                "connected": True,
                "url": config.safe_database_url,
                "version": version.split(",")[0],
            },
            "schema": {
                "tables": sorted(EXPECTED_TABLES & tables),
                "missing": missing,
            },
        },
    )
