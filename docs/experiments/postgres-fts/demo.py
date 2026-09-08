"""Postgres full-text search, demonstrated on your own notes.

Six questions, answered by running the queries rather than describing them:

    1. What does to_tsvector actually produce?
    2. What does stemming throw away?
    3. Where does FTS beat LIKE?
    4. Where does FTS LOSE to LIKE?
    5. How does ts_rank score, and what is it biased toward?
    6. What is the GIN index for?

Run:
    .venv/bin/python docs/experiments/postgres-fts/demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# This script lives three levels down, so the repo root is not on sys.path when
# it is run directly. Experiments are allowed this shortcut; production code uses
# proper package imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from api.config import config  # noqa: E402
from api.database import connect  # noqa: E402
from api.providers.obsidian import ObsidianVaultProvider  # noqa: E402


def rule(title: str) -> None:
    print(f"\n{'─' * 72}\n{title}\n{'─' * 72}")


def main() -> int:
    if config.vault_path is None:
        print("Set OBSIDIAN_VAULT_PATH in .env first.")
        return 1

    notes = ObsidianVaultProvider(config.vault_path).list_notes()
    print(f"{len(notes)} notes from your vault\n")

    with connect() as conn, conn.cursor() as cur:

        # ------------------------------------------------------------------
        rule("1. What to_tsvector produces")
        # ------------------------------------------------------------------
        sample = "The transcription systems were transcribing music quickly in 2024."
        print(f"input:  {sample}\n")

        cur.execute("select to_tsvector('english', %s)", (sample,))
        print(f"output: {cur.fetchone()[0]}\n")

        print("Three things happened:")
        print("  · words became LEXEMES — reduced to a root form (stemming)")
        print("  · numbers after each lexeme are POSITIONS in the document")
        print("  · 'the', 'were', 'in' vanished entirely — stop words")

        # ------------------------------------------------------------------
        rule("2. What stemming throws away")
        # ------------------------------------------------------------------
        for word in [
            "optimized",
            "optimizing",
            "transcription",
            "transcribe",
            "microservices",
            "service",
        ]:
            cur.execute("select to_tsvector('english', %s)", (word,))
            print(f"  {word:16} -> {cur.fetchone()[0]}")

        print("\nStemming is mechanical suffix-stripping, NOT meaning:")
        print("  optimized / optimizing     -> same stem. Search one, find the other.")
        print("  transcription / transcribe -> DIFFERENT stems. Search one, miss the other.")
        print("  microservices / service    -> different stems, despite the substring.")
        print("\nThat second line is the important one. No rule about meaning is")
        print("being applied — only suffix rules, which sometimes disagree with")
        print("what you would call related.")

        # ------------------------------------------------------------------
        rule("3. Where FTS beats LIKE")
        # ------------------------------------------------------------------
        def compare(text: str, query: str) -> tuple[bool, bool]:
            cur.execute(
                """
                select %s ilike %s,
                       to_tsvector('english', %s) @@ plainto_tsquery('english', %s)
                """,
                (text, f"%{query}%", text, query),
            )
            return cur.fetchone()

        text, query = "The systems were optimized", "optimizing"
        like_m, fts_m = compare(text, query)
        print(f"  text:   {text!r}")
        print(f"  search: {query!r}\n")
        print(f"  LIKE -> {like_m}   no literal substring, so it misses")
        print(f"  FTS  -> {fts_m}    both stem to 'optim', so it matches")
        print("\nThis is the whole case for FTS: people do not search using the")
        print("exact word form that happens to be in the document.")

        # ------------------------------------------------------------------
        rule("4. Where FTS LOSES to LIKE")
        # ------------------------------------------------------------------
        for text, query, why in [
            ("We run microservices here", "service", "substring inside a longer word"),
            ("Notes on PostgreSQL tuning", "greSQL", "partial identifier"),
        ]:
            like_m, fts_m = compare(text, query)
            print(f"  {text!r}")
            print(f"      search {query!r:12} LIKE -> {like_m!s:6} FTS -> {fts_m!s:6} ({why})")

        print("\nFTS matches WHOLE lexemes. Substrings, partial identifiers, and")
        print("typos are exactly what it cannot do — the index stores stems, and")
        print("half a stem is not in it.")
        print("\nNeither of these is 'better'. They fail differently, which is why")
        print("real systems run both. And neither handles 'why did I choose")
        print("Postgres?' when the note never uses those words — that gap is what")
        print("embeddings address, and you now know precisely what it is.")

        # ------------------------------------------------------------------
        rule("5. ts_rank on your real notes")
        # ------------------------------------------------------------------
        # Full bodies, not excerpts — excerpts are all truncated to the same
        # length, which would hide exactly the effect this demo is about.
        provider = ObsidianVaultProvider(config.vault_path)
        full = [provider.get_note(n.provider_id) for n in notes]

        cur.execute("create temp table demo (title text, body text, doc tsvector)")
        cur.executemany(
            "insert into demo values (%s, %s, to_tsvector('english', %s))",
            [(n.title, n.body or "", f"{n.title} {n.body or ''}") for n in full if n],
        )

        for term in ["project", "llm"]:
            cur.execute(
                """
                select title,
                       round(ts_rank(doc, query)::numeric, 5)    as default_rank,
                       round(ts_rank(doc, query, 2)::numeric, 7) as length_norm,
                       length(body) as body_chars
                  from demo, plainto_tsquery('english', %s) query
                 where doc @@ query
                 order by default_rank desc
                """,
                (term,),
            )
            rows = cur.fetchall()
            print(f"\n  search '{term}' -> {len(rows)} hit(s)")
            print(f"      {'default':>9}  {'norm=2':>9}  {'chars':>6}  title")
            for title, default_rank, length_norm, chars in rows:
                print(f"      {default_rank:>9}  {length_norm:>9}  {chars:>6}  {title}")

        print("\n  THE SURPRISE: by default, ts_rank IGNORES document length.")
        print("  Look at the 'default' column — a 4700-character note and a")
        print("  1300-character note score identically. Term frequency counts;")
        print("  length does not.")
        print("\n  Normalization is opt-in, via a third argument (a bitmask):")
        print("      0  ignore length          (the default)")
        print("      1  divide by 1+log(length)")
        print("      2  divide by length       (the 'norm=2' column above)")
        print("     32  rank/(rank+1), scaling into 0..1")
        print("\n  Under norm=2 the ordering changes: short notes rise. Neither")
        print("  is correct in the abstract — it depends on whether a long note")
        print("  mentioning a term twice is more relevant than a short one")
        print("  mentioning it twice. That is a product decision, not a default")
        print("  to accept without looking.")

        # ------------------------------------------------------------------
        rule("6. What the GIN index is for")
        # ------------------------------------------------------------------
        print("A tsvector column without an index means Postgres re-parses every")
        print("row on every query — a sequential scan.\n")
        print("A GIN index inverts it: lexeme -> list of rows containing it. That")
        print("is what an 'inverted index' means, and it is why search engines are")
        print("fast. B-tree cannot do this: it indexes whole values in order, and")
        print("a tsvector needs one entry per lexeme INSIDE the value.\n")
        print("search_index already has it — see db/schema.sql:")
        print("  create index search_index_document_idx")
        print("      on search_index using gin (document);\n")
        print("At 8 notes a sequential scan wins; the planner will ignore the")
        print("index until the table is big enough to be worth it.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
