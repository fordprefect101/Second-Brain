"""Tag rules: one spelling, existing tags suggested by meaning, new ones guarded."""

from __future__ import annotations

from api import embeddings, tags

# A fake model: each word is a direction, so "same meaning" is under test control.
MEANINGS = {
    "postgres": (1.0, 0.0, 0.0),
    "database": (0.9, 0.436, 0.0),  # close to postgres
    "frontend": (0.0, 1.0, 0.0),
    "music": (0.0, 0.0, 1.0),
}


def fake_embed(words: list[str]) -> list[list[float]]:
    return [list(MEANINGS[w]) for w in words]


def unreachable(_words):
    raise embeddings.EmbeddingsUnavailable("asleep")


def test_normalize_gives_one_spelling():
    assert tags.normalize("#Machine Learning") == "machine-learning"
    assert tags.normalize("  Etsy_Automation ") == "etsy-automation"
    assert tags.normalize("C++!") == "c"


def test_a_new_tag_close_in_meaning_is_matched_to_the_existing_one():
    match, checked = tags.similar_existing(
        "database", ["postgres", "frontend"], embed=fake_embed
    )
    assert match.tag == "postgres" and match.by == "meaning"
    assert checked


def test_an_unrelated_new_tag_has_no_match():
    match, checked = tags.similar_existing(
        "music", ["postgres", "frontend"], embed=fake_embed
    )
    assert match is None and checked


def test_spelling_variants_match_without_the_model():
    match, checked = tags.similar_existing(
        "postgresql", ["postgres", "frontend"], embed=unreachable
    )
    assert match.tag == "postgres" and match.by == "spelling"
    assert checked


def test_model_unreachable_is_reported_not_hidden():
    match, checked = tags.similar_existing("database", ["postgres"], embed=unreachable)
    assert match is None
    assert checked is False


def test_type_tags_are_never_offered_as_similar():
    match, _ = tags.similar_existing("notes", ["note", "adr"], embed=unreachable)
    assert match is None


def test_suggests_existing_tags_that_fit_the_note():
    found, checked = tags.suggest_for_note(
        "Why Postgres",
        "tsvector and GIN indexes",
        ["postgres", "frontend", "music", "adr"],
        embed_note=lambda _text: [1.0, 0.0, 0.0],
        embed=fake_embed,
    )
    assert [s.tag for s in found] == ["postgres"]
    assert checked


def test_suggestions_fall_back_to_words_in_the_note():
    def no_model(_text):
        raise embeddings.EmbeddingsUnavailable("asleep")

    found, checked = tags.suggest_for_note(
        "Why Postgres",
        "full text search",
        ["postgres", "frontend"],
        embed_note=no_model,
        embed=unreachable,
    )
    assert [s.tag for s in found] == ["postgres"]
    assert checked is False
