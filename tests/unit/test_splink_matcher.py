"""Unit tests for Phase 7.4: the salvaged Splink optional matching engine.

B4-matching-precision's own test for this module wrapped every assertion in
``if res is not None:`` — since the model was never trained there (Splink
defaulted the match prior to 0.0001 and every real duplicate scored below
its hardcoded 0.5 predict() cutoff), that test always exercised zero
assertions and still reported green. These tests assert directly: a real
duplicate pair must actually end up in the same cluster, not "if it does".

Splink runs for real here (DuckDB backend, in-process, no mocking) — small
enough record sets that training completes in well under a second.
"""

from __future__ import annotations

import pytest

from src.mcp_server.tools.resolve_entities import _record_id
from src.mcp_server.tools.splink_matcher import (
    _HAS_SPLINK,
    resolve_with_splink,
    score_pairs_with_splink,
)

pytestmark = pytest.mark.skipif(not _HAS_SPLINK, reason="splink is an optional dependency")


def _company(
    origin: str,
    table: str,
    row: str,
    name: str,
    email: str,
    phone: str | None = None,
    website: str | None = None,
) -> dict:
    rec: dict = {
        "_origin_id": origin,
        "_origin_table": table,
        "_origin_id_row": row,
        "name": name,
        "email": email,
    }
    if phone is not None:
        rec["phone"] = phone
    if website is not None:
        rec["website"] = website
    return rec


def _fixture_records() -> list[dict]:
    """10 companies: one genuine cross-origin duplicate agreeing on every
    field (name/email/phone/website), one same-name-but-otherwise-disagreeing
    pair (so EM training sees both agreement AND disagreement, not a
    degenerate all-identical fit), plus six unrelated singletons to give
    u-estimation a real distribution to sample.

    Email alone was not enough evidence here: with only ``_MIN_COHESION``
    (0.90, ``resolve_entities.py``) required to survive clustering's
    cohesion cut, a name+email-only duplicate pair measured at 0.78-0.85
    depending on the supporting dataset — real separation from the false
    duplicate (which measured ~0.0-0.003), but not reliably above 0.90.
    Adding phone+website as further agreeing fields (the same fix Phase
    7.1's own test fixture needed, for the same reason: one shared field
    isn't strong evidence) measured 0.99 — the same shape of iteration
    this codebase's test fixtures have needed before.
    """
    return [
        _company(
            "origin1",
            "Company",
            "1",
            "Boeing Corporation",
            "contact@boeing-test.example",
            phone="+1-555-0100",
            website="www.boeing-test.example",
        ),
        _company(
            "origin2",
            "company_profile",
            "2",
            "Boeing Corporation",
            "contact@boeing-test.example",
            phone="+1-555-0100",
            website="www.boeing-test.example",
        ),
        _company(
            "origin1",
            "Company",
            "3",
            "Acme Test Holdings",
            "info@acme-test-a.example",
            phone="+1-555-0200",
            website="www.acme-a-test.example",
        ),
        _company(
            "origin2",
            "company_profile",
            "4",
            "Acme Test Holdings",
            "info@acme-test-b.example",
            phone="+1-555-0201",
            website="www.acme-b-test.example",
        ),
        _company("origin1", "Company", "5", "Northwind Traders", "hello@northwind-test.example"),
        _company("origin1", "Company", "6", "Contoso Global", "sales@contoso-test.example"),
        _company("origin1", "Company", "7", "Fabrikam Industries", "info@fabrikam-test.example"),
        _company("origin2", "company_profile", "8", "Wingtip Toys", "hi@wingtip-test.example"),
        _company(
            "origin2", "company_profile", "9", "Tailspin Aerospace", "info@tailspin-test.example"
        ),
        _company("origin2", "company_profile", "10", "Adatum Systems", "hello@adatum-test.example"),
    ]


# ── score_pairs_with_splink ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_pairs_returns_none_for_fewer_than_two_records() -> None:
    assert await score_pairs_with_splink("company", []) is None
    assert await score_pairs_with_splink("company", [_fixture_records()[0]]) is None


@pytest.mark.asyncio
async def test_score_pairs_returns_none_with_no_usable_comparison_columns() -> None:
    # Only surrogate-key-shaped fields survive _project_to_canonical's
    # _SURROGATE_KEY_HINTS filter — nothing left to compare on.
    records = [
        {"_origin_id": "o1", "_origin_table": "t", "_origin_id_row": "1", "uuid": "aaa"},
        {"_origin_id": "o2", "_origin_table": "t", "_origin_id_row": "2", "uuid": "bbb"},
    ]
    assert await score_pairs_with_splink("company", records) is None


@pytest.mark.asyncio
async def test_score_pairs_trains_and_scores_the_duplicate_pair_highly() -> None:
    records = _fixture_records()
    scored = await score_pairs_with_splink("company", records)

    assert scored is not None
    assert len(scored) > 0

    id_boeing_1 = _record_id(records[0])
    id_boeing_2 = _record_id(records[1])
    key = (id_boeing_1, id_boeing_2) if id_boeing_1 < id_boeing_2 else (id_boeing_2, id_boeing_1)
    scores_by_pair = {(a, b): conf for a, b, conf in scored}
    assert key in scores_by_pair, "the byte-identical Boeing pair must be blocked and scored"
    # This is the exact failure B4 shipped: an untrained model scored a
    # byte-identical name pair at 0.093 because the match prior defaulted
    # to 0.0001, and predict()'s hardcoded 0.5 cutoff discarded it outright.
    # A trained model must clear the builtin engine's own auto_match bar
    # (0.95, _AUTO_MATCH in resolve_entities.py) for a genuine duplicate —
    # not just "some positive number".
    assert scores_by_pair[key] > 0.95


# ── resolve_with_splink ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_with_splink_merges_the_true_duplicate_into_one_cluster() -> None:
    records = _fixture_records()
    # Default thresholds (auto_match=0.95, auto_exclude=0.70) — same bar the
    # builtin engine holds a merge to, including the 0.90 cohesion-cut floor
    # _build_clusters applies to every 2-member component regardless of
    # engine. Acme's deliberately disagreeing email/phone/website must still
    # keep it out of Boeing's cluster under these same real thresholds.
    res = await resolve_with_splink("company", records)

    assert res is not None
    assert res["entity_type"] == "company"
    assert res["total_records"] == 10

    boeing_ids = {_record_id(records[0]), _record_id(records[1])}
    matches = [c for c in res["clusters"] if boeing_ids <= set(c["record_ids"])]
    assert len(matches) == 1, "the two Boeing records must land in exactly one cluster together"
    cluster = matches[0]
    assert set(cluster["record_ids"]) == boeing_ids, "no unrelated record should be pulled in"

    # Phase 7.1 lineage consistency: a Splink-scored cluster still carries
    # match_evidence, tagged with its own method.
    assert "match_evidence" in cluster
    pairs = cluster["match_evidence"]["pairs"]
    assert len(pairs) == 1
    assert pairs[0]["method"] == "splink"
    assert pairs[0]["confidence"] > 0.95


@pytest.mark.asyncio
async def test_resolve_with_splink_result_has_the_13_key_contract() -> None:
    res = await resolve_with_splink("company", _fixture_records())
    assert res is not None
    assert set(res.keys()) == {
        "entity_type",
        "clusters",
        "total_records",
        "auto_matched",
        "auto_excluded",
        "llm_scored",
        "uncompared_pairs",
        "candidate_pairs",
        "blocks_purged",
        "clustering_seconds",
        "largest_component_size",
        "large_components",
        "pending_merge_candidates",
    }


@pytest.mark.asyncio
async def test_resolve_with_splink_returns_none_for_insufficient_records() -> None:
    assert await resolve_with_splink("company", []) is None
    assert await resolve_with_splink("company", [_fixture_records()[0]]) is None


# ── engine seam in resolve_entities_impl ─────────────────────────────────


@pytest.mark.asyncio
async def test_engine_builtin_default_is_unaffected() -> None:
    from src.mcp_server.tools.resolve_entities import resolve_entities_impl

    records = [
        _company("origin1", "Company", "1", "Boeing Corporation", "a@boeing-test.example"),
        _company("origin2", "company_profile", "2", "Airbus SE", "b@airbus-test.example"),
    ]
    res = await resolve_entities_impl("company", records, matching_rules={"engine": "builtin"})
    assert res["total_records"] == 2
    # Distinct companies, no engine configured beyond "builtin" explicitly:
    # behaviour must be identical to omitting matching_rules entirely.
    assert len(res["clusters"]) == 2


@pytest.mark.asyncio
async def test_score_pairs_blocks_on_domain_even_when_names_disagree() -> None:
    """The blocking-rule union fix (§00b): a pair whose names share no
    4-character prefix at all — so the loosened name blocking rule alone
    would never propose it — must still be BLOCKED (proposed for scoring)
    when the two records share a registrable domain. Before this fix,
    `name` being present meant no other field's blocking rule was ever
    added at all (`if not blocking_rules`), so this pair would never even
    reach the scorer regardless of how strong the domain agreement was.
    """
    records = [
        *_fixture_records(),
        _company(
            "origin1",
            "Company",
            "11",
            "Umbrella Aerodyne Holdings",
            "info@umbrella-test.example",
            website="https://shared-domain-test.example/about",
        ),
        _company(
            "origin2",
            "company_profile",
            "12",
            "Zenith Sky Ventures",
            "hi@zenith-test.example",
            website="www.shared-domain-test.example",
        ),
    ]
    scored = await score_pairs_with_splink("company", records)
    assert scored is not None

    umbrella_id = _record_id(records[-2])
    zenith_id = _record_id(records[-1])
    scores_by_pair = {(a, b) if a < b else (b, a): conf for a, b, conf in scored}
    key = (umbrella_id, zenith_id) if umbrella_id < zenith_id else (zenith_id, umbrella_id)
    assert key in scores_by_pair, (
        "a pair sharing a registrable domain but no 4-char name prefix must "
        "still be blocked together — the blocking-rule union, not just the "
        "loosened name rule alone"
    )


@pytest.mark.asyncio
async def test_engine_splink_dispatches_and_merges_the_duplicate() -> None:
    from src.mcp_server.tools.resolve_entities import resolve_entities_impl

    records = _fixture_records()
    res = await resolve_entities_impl("company", records, matching_rules={"engine": "splink"})

    assert res["total_records"] == 10
    boeing_ids = {_record_id(records[0]), _record_id(records[1])}
    matches = [c for c in res["clusters"] if boeing_ids <= set(c["record_ids"])]
    assert len(matches) == 1
    assert set(matches[0]["record_ids"]) == boeing_ids
