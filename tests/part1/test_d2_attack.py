# ruff: noqa: F401, F811
"""D2 attack: break the feedback-API forwarding fix (adversarial, written without reading the fix first).

Reuses the real-route + real-WorkflowManager harness of test_d2_acceptance.py (only LangGraph is mocked).
"""

from __future__ import annotations

import pytest

from tests.part1.test_d2_acceptance import (
    GATE,
    _api_resume,
    _pd,
    _post_mock,
    _spool_isolation,
    band_run,
    fz,
    groups,
    pending_pairs,
)


def test_x1_reversed_record_id_order_still_matches(band_run):
    ids = list(reversed(band_run.ids["P"]))
    r, post = _api_resume(
        band_run.pre, {"decision": "approve", "pair_decisions": [_pd(ids, "merge")]}
    )
    assert r.status_code == 200 and fz(band_run.ids, "P") in groups(post)


def test_x2_non_string_ids_are_stringified(band_run):
    # the UI echoes record_ids as served; a client that re-types them as other JSON types must not crash or merge wrong
    r, post = _api_resume(
        band_run.pre,
        {"decision": "approve", "pair_decisions": [_pd([123, 456], "merge")]},
    )
    assert r.status_code == 200 and post is not None
    assert fz(band_run.ids, "P") not in groups(post) and fz(band_run.ids, "Q") not in groups(post)


def test_x3_keep_separate_beats_a_committed_threshold_below_its_score(band_run):
    p = band_run.ledger[fz(band_run.ids, "P")]
    bar = p - 0.01
    r, post = _api_resume(
        band_run.pre,
        {
            "decision": "approve",
            "pair_decisions": [_pd(band_run.ids["P"], "keep_separate")],
            "band_thresholds": {"company": {"auto_match": bar, "auto_exclude": bar / 2}},
        },
    )
    assert r.status_code == 200 and post is not None
    assert fz(band_run.ids, "P") not in groups(post), (
        "operator said keep_separate; threshold merged it anyway"
    )


def test_x4_decision_for_absent_entity_type_is_inert(band_run):
    r, post = _api_resume(
        band_run.pre,
        {
            "decision": "approve",
            "pair_decisions": [
                {"entity_type": "ghost", "record_ids": band_run.ids["P"], "action": "merge"}
            ],
        },
    )
    assert r.status_code == 200
    assert fz(band_run.ids, "P") not in groups(post)


def test_x5_malformed_items_do_not_crash_or_merge(band_run):
    items = [
        {},
        {"entity_type": "company"},
        {"entity_type": "company", "record_ids": [band_run.ids["P"][0]], "action": "merge"},
        {"entity_type": "company", "record_ids": [*band_run.ids["P"], "z"], "action": "merge"},
        {"entity_type": "company", "record_ids": band_run.ids["P"], "action": "MERGE ALL"},
        {"entity_type": "company", "record_ids": band_run.ids["P"], "action": None},
        {"entity_type": "company", "record_ids": None, "action": "merge"},
    ]
    r, post = _api_resume(band_run.pre, {"decision": "approve", "pair_decisions": items})
    assert r.status_code == 200
    assert fz(band_run.ids, "P") not in groups(post)
    assert fz(band_run.ids, "Q") not in groups(post)


def test_x6_band_thresholds_alone_resumes_and_applies(band_run):
    p = band_run.ledger[fz(band_run.ids, "P")]
    bar = p - 0.01
    r, post = _api_resume(
        band_run.pre,
        {
            "decision": "approve",
            "band_thresholds": {"company": {"auto_match": bar, "auto_exclude": bar / 2}},
        },
    )
    assert r.status_code == 200 and fz(band_run.ids, "P") in groups(post)


def test_x7_band_thresholds_for_absent_entity_is_inert(band_run):
    r, post = _api_resume(
        band_run.pre,
        {
            "decision": "approve",
            "band_thresholds": {"ghost": {"auto_match": 0.1, "auto_exclude": 0.05}},
        },
    )
    assert r.status_code == 200
    assert fz(band_run.ids, "P") not in groups(post)


def test_x8_reject_with_pair_decisions_merges_nothing_and_remembers_nothing(
    band_run, tmp_path, monkeypatch
):
    mem = tmp_path / "m" / "d.json"
    monkeypatch.setenv("MERGE_ENGINE_DECISION_MEMORY", str(mem))
    r, post = _api_resume(
        band_run.pre,
        {"decision": "reject", "pair_decisions": [_pd(band_run.ids["P"], "merge")]},
    )
    assert r.status_code == 200
    assert not mem.exists(), "a rejected gate must not teach the memory"
    # NOTE: the checkpoint state still gets the merge on reject (apply_band_decisions only skips memory); the
    # run ends as 'rejected' and nothing is loaded, so this is observed, not asserted.


def test_x9_reject_plain_unchanged(band_run):
    r, post = _api_resume(band_run.pre, {"decision": "reject"})
    assert r.status_code == 200
    if post is not None:
        assert fz(band_run.ids, "P") not in groups(post)


def test_x10_zero_pending_pairs_gate_approvable(band_run):
    pre = {**band_run.pre}
    stats = {k: {**v, "pending_merge_candidates": []} for k, v in pre["resolution_stats"].items()}
    pre["resolution_stats"] = stats
    for body in ({"decision": "approve"}, {"decision": "approve", "pair_decisions": []}):
        r, post = _api_resume(pre, body)
        assert r.status_code == 200
        assert not pending_pairs(post)


def test_x11_other_gates_bodies_unchanged_when_ui_sends_null_pair_decisions():
    # the UI always sends pair_decisions:null on non-matches gates
    for node_name in (
        "human_review_mappings",
        "human_review_deps",
        "human_review_scope",
        "human_final_approval",
    ):
        r, sent, _ = _post_mock(
            {
                "decision": "approve",
                "mapping_edits": None,
                "column_dependencies": None,
                "scope_plan": None,
                "thresholds": None,
                "pair_decisions": None,
            },
            node_name,
        )
        assert r.status_code == 200
        assert sent == {"approved": True, "decision": "approve", "node": node_name}, sent


def test_x12_pair_decisions_on_a_non_matches_gate_do_not_merge(band_run):
    r, post = _api_resume(
        band_run.pre,
        {"decision": "approve", "pair_decisions": [_pd(band_run.ids["P"], "merge")]},
        node_name="human_review_mappings",
    )
    assert r.status_code == 200
    if post is not None:
        assert fz(band_run.ids, "P") not in groups(post)


def test_x13_duplicate_conflicting_answers_never_merge_silently(band_run):
    # merge then keep_separate for the same pair: the unrecoverable action must not win by order
    for items in (
        [_pd(band_run.ids["P"], "merge"), _pd(band_run.ids["P"], "keep_separate")],
        [_pd(band_run.ids["P"], "keep_separate"), _pd(band_run.ids["P"], "merge")],
    ):
        r, post = _api_resume(band_run.pre, {"decision": "approve", "pair_decisions": items})
        assert r.status_code == 200
        assert fz(band_run.ids, "P") not in groups(post), items


@pytest.mark.parametrize("bad", ["x", 3, {"a": 1}, [1], ["a"], [[1, 2]]])
def test_x14_pair_decisions_wrong_shapes_422(bad):
    r, sent, mgr = _post_mock({"decision": "approve", "pair_decisions": bad})
    assert r.status_code == 422 and not mgr.resume_workflow.called
