"""Text -> vectors, via nomic-embed-text on Ollama (ADR-012), local or on the M4.

The only file that knows the model, its URL, its size and its prefixes. Swapping the
embedding model means changing this file and letting the next reindex re-embed —
nothing else imports Ollama for embeddings.

What step 2 of the embeddings experiment established, and this relies on:

  · vectors come back normalized (length 1), so cosine similarity = dot product
  · the model expects task prefixes; without them rankings got worse
  · inputs over the context limit are silently cut unless `truncate: false` is
    sent — so it always is, and an oversized chunk fails loudly instead
"""

from __future__ import annotations

import httpx

from api.config import config

OLLAMA_EMBED_URL = f"{config.ollama_url}/api/embed"
MODEL = "nomic-embed-text"
DIMENSIONS = 768  # must match vector(768) in schema.sql

DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

BATCH = 32  # texts per request: one request per text is dozens of round trips
TIMEOUT = 300.0
# Ollama may be on another machine that is asleep. Without this, a search would wait
# the full TIMEOUT to connect before falling back to keyword-only.
CONNECT_TIMEOUT = 3.0


class EmbeddingsUnavailable(RuntimeError):
    """The embedding model could not be used.

    Distinct from a bug, on purpose, like AssistantUnavailable: "Ollama is not
    running" is fixed in five seconds, and search must fall back to keyword-only
    rather than fail when it happens.
    """


def signature() -> str:
    """Identifies what produced a vector. Vectors from different signatures are not
    comparable, so a changed signature marks every stored vector as pending."""
    return f"{MODEL}|{DOC_PREFIX.strip()}|{QUERY_PREFIX.strip()}"


def _embed(inputs: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(inputs), BATCH):
        batch = inputs[start : start + BATCH]
        try:
            response = httpx.post(
                OLLAMA_EMBED_URL,
                json={"model": MODEL, "input": batch, "truncate": False},
                timeout=httpx.Timeout(TIMEOUT, connect=CONNECT_TIMEOUT),
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise EmbeddingsUnavailable(
                f"Ollama is not reachable at {config.ollama_url}. Check that the "
                "machine running it is awake and `ollama serve` is running."
            ) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingsUnavailable(f"Embedding request failed: {exc}") from exc

        if response.status_code == 404:
            raise EmbeddingsUnavailable(
                f"The embedding model is not installed: `ollama pull {MODEL}`."
            )
        if response.status_code != 200:
            # With truncate off, the likely 400 is an input over the context limit —
            # a chunking bug, since chunks are sized far below it.
            raise EmbeddingsUnavailable(
                f"Embedding failed ({response.status_code}): {response.text[:200]}"
            )
        vectors.extend(response.json()["embeddings"])
    return vectors


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Vectors for stored text (chunks), in the same order."""
    return _embed([DOC_PREFIX + t for t in texts])


def embed_query(text: str) -> list[float]:
    """The vector for one search question."""
    return _embed([QUERY_PREFIX + text])[0]
