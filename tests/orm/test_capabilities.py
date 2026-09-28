"""Capability honesty — the registry claims only what the framework emits.

``db.capabilities`` gates every feature API (``vector_search``,
``full_text_search``); a True the query builder cannot back turns the
explicit-degradation contract into a runtime crash instead (blueprint §8).
"""

from __future__ import annotations

from fastplace.orm.capabilities import CAPABILITY_GROUPS


def test_mongodb_claims_only_wired_capabilities():
    """The Mongo document adapter serves JSON documents — and that is all.

    No session/transaction API and no read-preference wiring exist on the
    Document surface, so those stay False alongside the queries the
    framework never emits (``$vectorSearch`` is Atlas-only, ``$text``
    unused). A True here would pass the supports() gate into code that
    has no Mongo path at all.
    """
    mongo = CAPABILITY_GROUPS["mongodb"]
    assert mongo["json"] is True
    assert mongo["json_path"] is True
    assert mongo["transactions"] is False
    assert mongo["read_replicas"] is False
    assert mongo["vector"] is False
    assert mongo["similarity_search"] is False
    assert mongo["full_text"] is False


def test_relational_groups_never_claim_unwired_vector_paths():
    """Only PostgreSQL has a vector path the ORM actually emits; the JSON
    fallback column on other backends is not a searchable vector index."""
    for driver in ("sqlite", "mysql"):
        assert CAPABILITY_GROUPS[driver]["vector"] is False
    assert CAPABILITY_GROUPS["postgresql"]["vector"] is True
