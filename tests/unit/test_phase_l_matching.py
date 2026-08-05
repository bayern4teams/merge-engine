"""Phase L: matching precision.

Before Phase L the matcher averaged string similarities against two
hand-picked constants, and clustering unioned any pair above the exclude
threshold. Measured on 400 real Pagila staff records — all distinct people,
1500 rows with 1500 distinct emails — that destroyed 26 of them, chaining
three different people named Lynn into a single cluster.

These tests pin the pieces that fixed it.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.mcp_server.tools.fellegi_sunter import (
    LEVEL_DISAGREE,
    LEVEL_EXACT,
    LEVEL_NULL,
    FieldModel,
    MatchModel,
    build_term_frequencies,
    estimate_model,
)
from src.mcp_server.tools.resolve_entities import (
    _generate_candidate_pairs,
    _normalize,
    _phonetic_key,
    resolve_entities_impl,
)


def _person(
    row: str, first: str, last: str, email: str | None = None, origin: str = "pg"
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "_origin_id": origin,
        "_origin_table": "staff",
        "_origin_id_row": row,
        "first_name": first,
        "last_name": last,
    }
    if email is not None:
        rec["email"] = email
    return rec


# ── L.1 Term-frequency weighting ─────────────────────────────────────


def test_rare_value_outweighs_common_one() -> None:
    """The core idea: agreeing on a rare value is stronger evidence."""
    model = FieldModel(
        name="last_name",
        term_frequency={
            "smith": 0.20,  # 1 in 5 records
            "zaentz": 0.001,  # 1 in 1000
        },
    )
    common = model.weight(LEVEL_EXACT, "smith")
    rare = model.weight(LEVEL_EXACT, "zaentz")
    assert rare > common, "a rare surname must carry more weight than a common one"


def test_unknown_value_falls_back_to_the_base_rate() -> None:
    model = FieldModel(name="last_name", term_frequency={"smith": 0.2})
    assert model.weight(LEVEL_EXACT, "not-in-the-data") != 0.0


def test_null_comparison_contributes_nothing() -> None:
    """A missing field is not evidence for OR against a match."""
    model = FieldModel(name="email")
    assert model.weight(LEVEL_NULL, None) == 0.0


def test_disagreement_is_negative_evidence() -> None:
    model = FieldModel(name="email")
    assert model.weight(LEVEL_DISAGREE) < 0


def test_term_frequencies_reflect_the_real_distribution() -> None:
    records = [_person(str(i), "Rory", f"Last{i}") for i in range(4)]
    records.append(_person("9", "Zebediah", "Unique"))
    freqs = build_term_frequencies(records, _normalize)
    assert freqs["first_name"]["rory"] == pytest.approx(0.8)
    assert freqs["first_name"]["zebediah"] == pytest.approx(0.2)


def test_estimate_model_survives_no_pairs() -> None:
    model = estimate_model([], {})
    assert isinstance(model, MatchModel)
    assert 0.0 < model.match_prior < 1.0


# ── L.2 Blocking ─────────────────────────────────────────────────────


def test_phonetic_key_groups_spelling_variants() -> None:
    assert _phonetic_key("Smith") == _phonetic_key("Smyth")
    assert _phonetic_key("Robert") != _phonetic_key("Xylophone")


def test_blocking_still_pairs_across_differently_named_fields() -> None:
    """Origins name the same concept differently — pairs must survive that.

    A blocking key tied to the field name would drop every cross-origin
    pair, which is the whole reason multi-origin merges exist.
    """
    records = [
        {
            "_origin_id": "pg",
            "_origin_table": "companies",
            "_origin_id_row": "1",
            "company_name": "Titan Labs",
        },
        {
            "_origin_id": "ch",
            "_origin_table": "organizations",
            "_origin_id_row": "2",
            "org_title": "Titan Labs",
        },
    ]
    assert len(_generate_candidate_pairs(records)) == 1


def test_blocking_pairs_on_a_typo_via_phonetic_key() -> None:
    """An exact-match block would miss this pair entirely."""
    records = [
        _person("1", "Katherine", "Smith", "k@x.com", origin="pg"),
        _person("2", "Katherine", "Smyth", "k@x.com", origin="crm"),
    ]
    assert len(_generate_candidate_pairs(records)) == 1


def test_oversized_approximate_blocks_are_admitted_but_dont_merge() -> None:
    """BUG C / C1: a non-discriminative block is admitted, not annihilated.

    A phonetic code shared by dozens of records is weak evidence, but the
    block must still be proposed for scoring (budget-limited, honestly
    counted) rather than silently dropped — that silent drop is exactly how
    a real scoped merge went from hundreds of candidate pairs to zero.
    Scoring, not blocking, is what keeps this from producing a false merge
    (see test_no_unique_id_and_a_constant_field_does_not_merge).
    """
    records = [_person(str(i), f"n{i}xx", "yy", f"user{i}@x.com") for i in range(40)]
    stats: dict[str, int] = {}
    pairs = _generate_candidate_pairs(records, stats=stats)
    assert len(pairs) > 0
    assert stats["blocks_purged"] >= 1
    assert stats["uncompared_pairs"] == 0


def test_exact_full_value_blocks_are_never_purged() -> None:
    """An exact match on the complete value is evidence at any block size."""
    records = [
        {"_origin_id": "a", "_origin_table": "t", "_origin_id_row": str(i), "name": "Acme Corp"}
        for i in range(30)
    ]
    assert len(_generate_candidate_pairs(records)) > 0


# ── L.3 Clustering ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_people_sharing_a_first_name_are_not_merged() -> None:
    """The Rory case: four different people, four different surnames."""
    records = [
        _person("1", "Rory", "Ledner", "kihn146@a.com"),
        _person("2", "Rory", "Lowe", "feeney9572@b.com"),
        _person("3", "Rory", "Orn", "dubuque1621@c.com"),
        _person("4", "Rory", "Spencer", "stracke8760@d.com"),
    ]
    result = await resolve_entities_impl("person", records)
    assert len(result["clusters"]) == 4


@pytest.mark.asyncio
async def test_conflicting_email_splits_a_cluster() -> None:
    """Same name, different unique identifier -> different entities."""
    records = [
        _person("1", "Jordan", "Miller", "jordan.miller@a.com"),
        _person("2", "Jordan", "Miller", "totally.different@b.com"),
    ]
    result = await resolve_entities_impl("person", records)
    assert len(result["clusters"]) == 2


@pytest.mark.asyncio
async def test_genuine_duplicates_still_merge() -> None:
    """Precision must not come from refusing to merge anything."""
    records = [
        _person("1", "Marina", "Kowalczyk", "marina.k@example.com", origin="pg"),
        _person("1b", "Marina", "Kowalczyk", "marina.k@example.com", origin="crm"),
    ]
    result = await resolve_entities_impl("person", records)
    assert len(result["clusters"]) == 1
    assert len(result["clusters"][0]["record_ids"]) == 2


@pytest.mark.asyncio
async def test_weak_chain_does_not_fuse_three_entities() -> None:
    """A-B and B-C similar, A-C not: connected components would merge all 3."""
    records = [
        _person("1", "Lynn", "Becker", "koss9779@a.com"),
        _person("2", "Lynn", "Durgan", "conn9320@b.com"),
        _person("3", "Lynn", "Farrell", "hermiston6990@c.com"),
    ]
    result = await resolve_entities_impl("person", records)
    assert len(result["clusters"]) == 3


@pytest.mark.asyncio
async def test_no_unique_id_and_a_constant_field_does_not_merge() -> None:
    """The Pagila actor case, found live.

    That table has no email and stamps every row with the same
    ``last_update``. Two actors then agree on a first name and on a value
    identical across the whole table, and the accumulated weight crossed the
    auto-merge threshold on evidence that identifies nobody — 25 pairs of
    different actors were merged this way.
    """
    records = [
        {
            "_origin_id": "pg",
            "_origin_table": "actor",
            "_origin_id_row": str(i),
            "first_name": first,
            "last_name": last,
            "last_update": "2022-02-15 09:34:33+00",
        }
        for i, (first, last) in enumerate(
            [
                ("ANGELA", "HUDSON"),
                ("ANGELA", "WITHERSPOON"),
                ("GRETA", "KEITEL"),
                ("GRETA", "MALDEN"),
                ("SUSAN", "DAVIS"),
                ("JENNIFER", "DAVIS"),
            ]
            + [(f"NAME{i}", f"SUR{i}") for i in range(40)]
        )
    ]
    result = await resolve_entities_impl("person", records)
    assert len(result["clusters"]) == len(records)


@pytest.mark.asyncio
async def test_full_name_agreement_still_merges_without_an_id() -> None:
    """Precision must not come from refusing every merge.

    "Rory" alone identifies four people; "Rory Ledner" identifies one. A
    complete name match is different evidence from a partial one.
    """
    records = [
        {
            "_origin_id": "pg",
            "_origin_table": "a",
            "_origin_id_row": "1",
            "first_name": "Marina",
            "last_name": "Kowalczyk",
        },
        {
            "_origin_id": "crm",
            "_origin_table": "b",
            "_origin_id_row": "2",
            "first_name": "Marina",
            "last_name": "Kowalczyk",
        },
    ] + [
        {
            "_origin_id": "pg",
            "_origin_table": "a",
            "_origin_id_row": str(i + 10),
            "first_name": f"Other{i}",
            "last_name": f"Person{i}",
        }
        for i in range(40)
    ]
    result = await resolve_entities_impl("person", records)
    merged = [c for c in result["clusters"] if len(c["record_ids"]) == 2]
    assert len(merged) == 1


def test_uncertain_pairs_are_surfaced_not_dropped() -> None:
    """Not merging an uncertain pair must not mean hiding it.

    Uncertain pairs form no multi-record cluster, so before this the matches
    gate had nothing to show and auto-skipped — the reviewer never learned
    the system had hesitated.
    """
    from src.mcp_server.tools.resolve_entities import _pending_merge_candidates

    scored = [
        ("a", "b", 0.99),  # confident match — merged, not pending
        ("c", "d", 0.85),  # uncertain
        ("e", "f", 0.92),  # uncertain, stronger
        ("g", "h", 0.10),  # confident non-match
    ]
    pending = _pending_merge_candidates(scored)
    assert [p["record_ids"] for p in pending] == [["e", "f"], ["c", "d"]]
    assert pending[0]["confidence"] == 0.92


def test_pending_candidates_are_capped() -> None:
    """The payload and the reviewer both have limits."""
    from src.mcp_server.tools.resolve_entities import (
        _MAX_PENDING_CANDIDATES,
        _pending_merge_candidates,
    )

    scored = [(f"a{i}", f"b{i}", 0.8) for i in range(_MAX_PENDING_CANDIDATES + 25)]
    assert len(_pending_merge_candidates(scored)) == _MAX_PENDING_CANDIDATES


def test_matches_gate_fires_for_unmerged_uncertain_pairs() -> None:
    """The routing must stop for uncertainty even with no flagged cluster."""
    from src.orchestrator.nodes.resolve_entities import has_low_confidence_clusters

    state = {
        "entity_clusters": {
            "person": [
                {"cluster_id": "c1", "record_ids": ["r1"], "required_human_review": False},
            ]
        },
        "resolution_stats": {
            "person": {
                "pending_merge_candidates": [
                    {"record_ids": ["r1", "r2"], "confidence": 0.88},
                ],
            }
        },
    }
    assert has_low_confidence_clusters(state) is True


def test_matches_gate_still_skips_when_nothing_is_uncertain() -> None:
    from src.orchestrator.nodes.resolve_entities import has_low_confidence_clusters

    state = {
        "entity_clusters": {
            "person": [
                {"cluster_id": "c1", "record_ids": ["r1"], "required_human_review": False},
            ]
        },
        "resolution_stats": {"person": {"pending_merge_candidates": []}},
    }
    assert has_low_confidence_clusters(state) is False


@pytest.mark.asyncio
async def test_blocking_shortfall_is_reported_not_hidden() -> None:
    """A truncated comparison budget must be a number the report carries."""
    records = [_person(str(i), "Sam", "Jones", f"sam{i}@x.com") for i in range(5)]
    result = await resolve_entities_impl("person", records)
    assert "uncompared_pairs" in result
    assert result["uncompared_pairs"] == 0


# ── D.1 / D.2 / D.3 Clustering profile instrumentation ───────────────


@pytest.mark.asyncio
async def test_clustering_stats_are_reported() -> None:
    """BUG D.1: every run must report a clustering cost profile, not just a
    log line, so a real run's component-size distribution is measurable
    without re-reading logs."""
    records = [_person(str(i), "Sam", "Jones", f"sam{i}@x.com") for i in range(5)]
    result = await resolve_entities_impl("person", records)
    assert "clustering_seconds" in result
    assert result["clustering_seconds"] >= 0.0
    assert "largest_component_size" in result
    assert "large_components" in result
    # 5 distinct people, no shared field beyond first/last name pattern —
    # they should not all fuse into one component.
    assert result["largest_component_size"] <= 5


@pytest.mark.asyncio
async def test_large_component_is_logged_not_silently_slow(caplog) -> None:
    """BUG D.1: a component over the visibility threshold must be logged,
    not just silently take longer — the whole point of the instrumentation
    is that a pathologically dense component is discoverable without a
    profiler attached."""
    import logging

    from src.mcp_server.tools import resolve_entities as re_mod

    original_threshold = re_mod._LARGE_COMPONENT_THRESHOLD
    re_mod._LARGE_COMPONENT_THRESHOLD = 2
    try:
        # Everyone shares the same name + email -> genuine duplicates,
        # auto-merged into one component of 4, above the lowered threshold
        # of 2 (mirrors test_genuine_duplicates_still_merge's shape).
        records = [
            _person(str(i), "Sam", "Jones", "sam.jones@x.com", origin=f"o{i}") for i in range(4)
        ]
        with caplog.at_level(logging.INFO, logger="merge-engine.tools.resolve_entities"):
            result = await resolve_entities_impl("person", records)
        assert result["largest_component_size"] >= 3
        assert result["large_components"] >= 1
        assert any(
            "exceeds the" in r.message and "visibility threshold" in r.message
            for r in caplog.records
        )
    finally:
        re_mod._LARGE_COMPONENT_THRESHOLD = original_threshold


def test_partition_component_hoist_matches_unhoisted_semantics() -> None:
    """BUG D.2: hoisting the fields_a/fields_b dict comprehensions out of
    the inner conflict-check loop must not change which components get
    split — same conflicting-identity outcome, just computed once per
    member instead of once per pair."""
    from src.mcp_server.tools.resolve_entities import _partition_component

    records_by_id = {
        "a": {"email": "x@x.com", "first_name": "A"},
        "b": {"email": "y@x.com", "first_name": "B"},
        "c": {"first_name": "C"},
    }
    edge_weight = {("a", "b"): 0.9, ("a", "c"): 0.9, ("b", "c"): 0.9}
    parts, repaired, cut = _partition_component(["a", "b", "c"], edge_weight, records_by_id)
    # Conflicting emails (a vs b) must split every member out, same as
    # before the hoist.
    assert repaired == 1
    assert cut == 0
    assert sorted(parts) == [["a"], ["b"], ["c"]]


# ── L.4 / K.4 Stable ids under incremental extraction ────────────────


@pytest.mark.asyncio
async def test_previously_assigned_cluster_id_is_reused() -> None:
    """An incremental run must update the existing row, not insert a new one.

    A later run sees only the origins without a usable watermark, so a
    record that previously clustered with others now stands alone. Deriving
    the id from membership alone would change it and duplicate the row.
    """
    records = [_person("1", "Ada", "Lovelace", "ada@x.com")]
    known = {"pg:staff:1": "aaaaaaaabbbbccccddddeeeeffff0000"}
    result = await resolve_entities_impl("person", records, known_cluster_ids=known)
    assert result["clusters"][0]["cluster_id"] == known["pg:staff:1"]


@pytest.mark.asyncio
async def test_new_records_still_get_a_derived_id() -> None:
    records = [_person("1", "Ada", "Lovelace", "ada@x.com")]
    result = await resolve_entities_impl("person", records, known_cluster_ids={})
    assert len(result["clusters"][0]["cluster_id"]) == 32
