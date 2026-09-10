"""Routing captures into the vault, and undoing it.

The highest-stakes code in the project — it creates and deletes files in a personal
knowledge vault. ADR-005 allowed writes only once an undo path existed, and these
tests are what keep that guarantee true.
"""

from __future__ import annotations

import pytest

from api.routing import RoutingError, route_capture_to_vault, undo_routing


def make_capture(db, body: str, kind: str = "idea"):
    with db.cursor() as cur:
        cur.execute(
            "insert into capture_items (body, kind) values (%s, %s) returning id",
            (body, kind),
        )
        capture_id = cur.fetchone()["id"]
    db.commit()
    return capture_id


def capture_row(db, capture_id):
    with db.cursor() as cur:
        cur.execute("select * from capture_items where id = %s", (capture_id,))
        return cur.fetchone()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_route_creates_a_note(db, provider, vault):
    capture_id = make_capture(db, "Build an AI guitar teacher")

    routed = route_capture_to_vault(db, provider, capture_id)

    written = vault / routed.provider_id
    assert written.exists()
    assert "Build an AI guitar teacher" in written.read_text()


def test_routed_note_records_provenance(db, provider, vault):
    """The vault must stay self-describing without this app (ADR-001)."""
    capture_id = make_capture(db, "A thought", kind="resource")

    routed = route_capture_to_vault(db, provider, capture_id)
    content = (vault / routed.provider_id).read_text()

    assert "tags: [resource]" in content
    assert "source: personal-os-capture" in content


def test_capture_row_survives_routing(db, provider):
    """The row becomes a record of an event, not the data. It is never deleted."""
    capture_id = make_capture(db, "A thought")

    routed = route_capture_to_vault(db, provider, capture_id)
    row = capture_row(db, capture_id)

    assert row is not None
    assert row["status"] == "routed"
    assert row["routed_at"] is not None
    assert row["routed_to_entity_id"] == routed.entity_id
    assert row["routed_to_ref"] == routed.ref


def test_routing_creates_an_entity_mapping(db, provider):
    capture_id = make_capture(db, "A thought")

    routed = route_capture_to_vault(db, provider, capture_id)

    with db.cursor() as cur:
        cur.execute("select * from entity_map where id = %s", (routed.entity_id,))
        row = cur.fetchone()

    assert row["provider"] == "obsidian"
    assert row["provider_id"] == routed.provider_id


def test_routing_takes_a_snapshot(db, provider):
    """No write happens without an undo record. That is ADR-005's condition."""
    capture_id = make_capture(db, "A thought")

    routed = route_capture_to_vault(db, provider, capture_id)

    with db.cursor() as cur:
        cur.execute(
            "select * from note_snapshots where entity_id = %s", (routed.entity_id,)
        )
        snapshot = cur.fetchone()

    assert snapshot["operation"] == "create"
    # NULL content means "did not exist before", so undo deletes rather than restores.
    assert snapshot["content"] is None
    assert snapshot["undone_at"] is None


def test_cannot_route_twice(db, provider):
    capture_id = make_capture(db, "A thought")
    route_capture_to_vault(db, provider, capture_id)

    with pytest.raises(RoutingError, match="already routed"):
        route_capture_to_vault(db, provider, capture_id)


def test_cannot_route_unknown_capture(db, provider):
    from uuid import uuid4

    with pytest.raises(RoutingError, match="No capture"):
        route_capture_to_vault(db, provider, uuid4())


def test_hostile_capture_body_cannot_escape_the_vault(db, provider, vault):
    """A capture body becomes a title becomes a filename. It is untrusted input."""
    capture_id = make_capture(db, "../../../etc/passwd")

    routed = route_capture_to_vault(db, provider, capture_id)

    assert vault.resolve() in (vault / routed.provider_id).resolve().parents


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


def test_undo_deletes_the_note_and_restores_the_capture(db, provider, vault):
    capture_id = make_capture(db, "A thought")
    routed = route_capture_to_vault(db, provider, capture_id)

    undo_routing(db, provider, capture_id)

    assert not (vault / routed.provider_id).exists()

    row = capture_row(db, capture_id)
    assert row["status"] == "inbox"
    assert row["routed_at"] is None
    assert row["routed_to_entity_id"] is None
    assert row["routed_to_ref"] is None


def test_undo_refuses_when_the_note_was_edited(db, provider, vault):
    """The safety property that matters most.

    Undo reverses OUR write. If a person edited the note afterwards, deleting it
    would destroy their work — so it refuses rather than guessing.
    """
    capture_id = make_capture(db, "A thought")
    routed = route_capture_to_vault(db, provider, capture_id)

    written = vault / routed.provider_id
    written.write_text(written.read_text() + "\nMy own additions.\n")

    with pytest.raises(RoutingError, match="edited since routing"):
        undo_routing(db, provider, capture_id)

    assert written.exists()
    assert "My own additions." in written.read_text()
    # And the capture must stay routed — a refused undo changes nothing.
    assert capture_row(db, capture_id)["status"] == "routed"


def test_undo_marks_the_snapshot_used(db, provider):
    capture_id = make_capture(db, "A thought")
    routed = route_capture_to_vault(db, provider, capture_id)

    undo_routing(db, provider, capture_id)

    with db.cursor() as cur:
        cur.execute(
            "select undone_at from note_snapshots where entity_id = %s",
            (routed.entity_id,),
        )
        assert cur.fetchone()["undone_at"] is not None


def test_undo_removes_the_search_index_row(db, provider):
    """Otherwise search returns a hit that 404s when opened."""
    capture_id = make_capture(db, "A thought")
    routed = route_capture_to_vault(db, provider, capture_id)

    with db.cursor() as cur:
        cur.execute(
            "insert into search_index (entity_id, provider, title) "
            "values (%s, 'obsidian', 'A thought')",
            (routed.entity_id,),
        )
    db.commit()

    undo_routing(db, provider, capture_id)

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from search_index where entity_id = %s",
            (routed.entity_id,),
        )
        assert cur.fetchone()["n"] == 0


def test_cannot_undo_an_unrouted_capture(db, provider):
    capture_id = make_capture(db, "A thought")

    with pytest.raises(RoutingError, match="not routed"):
        undo_routing(db, provider, capture_id)


def test_route_undo_route_round_trip(db, provider, vault):
    """A capture must be routable again after an undo."""
    capture_id = make_capture(db, "A thought")

    first = route_capture_to_vault(db, provider, capture_id)
    undo_routing(db, provider, capture_id)
    second = route_capture_to_vault(db, provider, capture_id)

    assert (vault / second.provider_id).exists()
    assert capture_row(db, capture_id)["status"] == "routed"
    # The first file was deleted, so the name is free again.
    assert first.provider_id == second.provider_id
