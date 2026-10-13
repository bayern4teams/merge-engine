"""D15 acceptance: the matches gate must say how many band pairs there are, not only the 50 it lists.

Derived from docs/plans/part1/LOCAL_VERIFY_REPORT.md section D15 (Observed + Fix shape) only; the fix was not read.

Defect: ``_pending_merge_candidates`` slices the band to ``_MAX_PENDING_CANDIDATES`` (50) and the gate payload
says "50 pair(s) looked similar ..." with no hint that 1,330 pairs scored in the band (Abt-Buy: ``band_summary.
predicted_counts.possible`` 1,330, ``pending_merge_candidates`` 50).  The other 1,280 stay separate, unseen.
Fix shape: page the band at the gate, or say "50 of N shown" in the payload.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d15_acceptance.py -q -p no:cacheprovider

How: the REAL ``node.resolve_entities`` runs on synthetic records whose pair scores are planted by wrapping
``_score_pair`` (the test_p15 technique); the planted ledger (every pair's final score) is the independent
ground truth.  The payload is the real ``human_nodes._build_matches_interrupt`` output.  Entity types and
record counts are parameters, so a constant in the fix cannot pass them all.

The report fixes semantics, not key names, so the machine-readable field is FOUND, not named: for each entity
a dict in the payload (outside the pair list, the clusters and ``band_summary.predicted_counts``/``distribution``)
holding BOTH the listed count and the total as integers, reached via the entity name; and, for the whole gate,
one that holds the summed pair.  ``band_summary.predicted_counts`` is excluded on purpose: it already carries
``possible`` today, and it is NOT next to the number listed.

Groups
  A  more pairs than the cap: per-entity + overall total, machine-readable and in the note ("N of M")
  B  fewer than / exactly the cap: total == listed, the note must not say "N of M"
  C  strongest first, cap constant unchanged, listed count == min(cap, total)
  D  total == count of pairs in [auto_exclude, auto_match) == band_summary predicted "possible"
  E  UI (static check on Gates.tsx; no browser)
  F  old checkpoints / empty runs / memory-resolved pairs do not break the arithmetic
  G  plan 2.4 "no fitted constants": diff of the touched files vs BASE adds no number other than 0/1
"""

from __future__ import annotations

import asyncio
import importlib
import os
import random
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import src.orchestrator.spool as spool_mod
from src.mcp_server.tools import resolve_entities as tre
from src.orchestrator.nodes import human_nodes as hn
from tests.part1 import fixtures_matching as fx

node = importlib.import_module("src.orchestrator.nodes.resolve_entities")
ROOT = Path(__file__).resolve().parents[2]
BASE = "673a7d6"  # HEAD when D15 was handed to the worker; the diff below is "the D15 fix"
AM, AE = 0.95, 0.70  # the report's band edges (P15 table), written independently of src
ENTITIES = ["company", "person", "product"]
OLD_KEYS = {  # payload keys that exist before D15
    "low_confidence_clusters",
    "total_clustering_decisions",
    "auto_match_stats",
    "pending_merge_candidates",
    "pending_merge_note",
    "band_summary",
}


@pytest.fixture(autouse=True)
def _spool_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    monkeypatch.delenv("MERGE_ENGINE_DECISION_MEMORY", raising=False)
    yield


# ── harness ──────────────────────────────────────────────────────────────────────────────────
class World:
    def __init__(self, pre, ledger):
        self.pre, self.ledger = pre, ledger  # ledger[entity][frozenset({rid, rid})] = final score

    def band(self, entity):
        return {k: s for k, s in self.ledger[entity].items() if AE <= s < AM}


def build(sizes, seed=15, extras=True, scores=None):
    """Run the real resolve_entities node: ``sizes[entity]`` pairs planted in the band (distinct scores spread
    over [0.70, 0.95)), plus (``extras``) a clear Match and a clear Exclude pair per entity.  ``scores`` maps
    entity -> explicit list of band scores (overrides the spread)."""
    rng = random.Random(seed)
    toks = fx._tokens(rng, 2 * (sum(sizes.values()) + 4 * len(sizes)))
    data, planted, ledger = {}, {}, {e: {} for e in sizes}
    cur = 0
    for ent, n in sizes.items():
        recs = []
        plan = (scores or {}).get(ent) or [AE + (AM - AE) * (i + 0.5) / max(n, 1) for i in range(n)]
        plan = list(plan) + ([0.99, 0.30] if extras else [])
        rng.shuffle(plan)
        for i, s in enumerate(plan):
            a, b = toks[cur], f"{toks[cur]} {toks[cur + 1]}"
            cur += 2
            ra = fx._rec("src_a", f"{ent}{i}", name=a)
            rb = fx._rec("src_b", f"{ent}{i}", name=b)
            recs += [ra, rb]
            planted[(ent, frozenset({a, b}))] = (s, frozenset({fx.rid(ra), fx.rid(rb)}))
        data[ent] = recs
    orig = tre._score_pair
    ent_of = {fx.rid(r): e for e, rs in data.items() for r in rs}

    def spy(r1, r2, *a, **k):
        e = ent_of[fx.rid(r1)]
        hit = planted.get((e, frozenset({r1["name"], r2["name"]})))
        s = orig(r1, r2, *a, **k) if hit is None else hit[0]
        ledger[e][frozenset({fx.rid(r1), fx.rid(r2)})] = s
        return s

    state = {
        "run_id": f"d15run{os.getpid()}",
        "target_entities": list(sizes),
        "extracted_data": data,
        "entity_clusters": {},
        "errors": [],
    }
    # inline, not the process pool (>= _PROCESS_POOL_MIN_RECORDS records would score in a child the spy cannot see)
    with (
        patch.object(tre, "_score_pair", spy),
        patch.object(tre, "_PROCESS_POOL_MIN_RECORDS", 10**9),
    ):
        out = asyncio.run(node.resolve_entities(state))
    return World({**state, **out}, ledger)


def gate(world_or_state):
    pre = world_or_state.pre if isinstance(world_or_state, World) else world_or_state
    return hn._build_matches_interrupt(pre)["data"]


def listed_by_entity(data):
    out: dict[str, list] = {}
    for p in data.get("pending_merge_candidates", []):
        out.setdefault(p["entity_type"], []).append(p)
    return out


def _isint(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _nodes(o, path=()):
    """Every dict in the payload with the keys leading to it; skips the pair list, the clusters and the two
    ``band_summary`` blocks that already exist (see module docstring)."""
    if isinstance(o, dict):
        yield path, o
        for k, v in o.items():
            if k in ("pending_merge_candidates", "low_confidence_clusters", "distribution"):
                continue
            if k == "predicted_counts":
                continue
            yield from _nodes(v, path + (k,))
    elif isinstance(o, list):
        for v in o:
            yield from _nodes(v, path + ("[]",))


def _holds(d, *wanted):
    vals = {v for v in d.values() if _isint(v)}
    return all(w in vals for w in wanted)


def entity_node(data, entity, listed, total):
    """A dict reached via the entity's name (key or entity_type field) holding both numbers."""
    for path, d in _nodes(data):
        if (entity in path or d.get("entity_type") == entity) and _holds(d, listed, total):
            return d
    return None


def overall_node(data, entities, listed, total):
    for path, d in _nodes(data):
        if (
            not any(e in path for e in entities)
            and d.get("entity_type") is None
            and _holds(d, listed, total)
        ):
            return d
    return None


def note_of(data):
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", data.get("pending_merge_note", ""))


def n_of_m(note):
    return [(int(a), int(b)) for a, b in re.findall(r"\b(\d+)\s+of\s+(\d+)\b", note)]


# ── A. more band pairs than the cap ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def big():
    """The report's scale: 1,330 band pairs over 3 entities, every one over the cap."""
    return build({"company": 800, "person": 400, "product": 130})


def test_a0_harness_sanity_the_cap_binds_and_ledger_is_the_planted_band(big):
    assert {e: len(big.band(e)) for e in big.ledger} == {
        "company": 800,
        "person": 400,
        "product": 130,
    }
    listed = {e: len(v) for e, v in listed_by_entity(gate(big)).items()}
    assert listed == dict.fromkeys(big.ledger, tre._MAX_PENDING_CANDIDATES), listed


def test_a1_total_per_entity_is_machine_readable_next_to_the_listed_count(big):
    """FAILS today: nothing beside the 50 says 800 / 400 / 130."""
    data, cap = gate(big), tre._MAX_PENDING_CANDIDATES
    missing = [
        (e, len(big.band(e)))
        for e in big.ledger
        if entity_node(data, e, cap, len(big.band(e))) is None
    ]
    assert not missing, (
        f"no per-entity field holding (listed={cap}, total) in the payload: {missing}"
    )


def test_a2_overall_total_is_machine_readable_next_to_the_listed_count(big):
    """FAILS today.  Overall = 3 x cap listed of 1,330."""
    data = gate(big)
    listed = sum(len(v) for v in listed_by_entity(data).values())
    total = sum(len(big.band(e)) for e in big.ledger)
    assert (listed, total) == (3 * tre._MAX_PENDING_CANDIDATES, 1330)
    assert overall_node(data, list(big.ledger), listed, total) is not None, (
        f"no overall field holding (listed={listed}, total={total})"
    )


def test_a3_note_says_n_of_m_with_the_real_total(big):
    """FAILS today: the note says '150 pair(s) looked similar ...' and nothing about 1,330."""
    data = gate(big)
    listed = len(data["pending_merge_candidates"])
    assert (listed, 1330) in n_of_m(note_of(data)), (
        f"note must say '{listed} of 1330': {data['pending_merge_note']!r}"
    )


def test_a4_the_total_tracks_the_data_not_a_constant():
    """A hard-coded total (or one copied from the cap) cannot satisfy sizes that differ per call."""
    seen = set()
    for sizes in ({"company": 61}, {"company": 143, "person": 7}, {"person": 99}):
        w = build(sizes, seed=sum(sizes.values()))
        data = gate(w)
        total = sum(len(w.band(e)) for e in sizes)
        listed = len(data["pending_merge_candidates"])
        seen.add(total)
        assert (listed, total) in n_of_m(note_of(data)), (sizes, data["pending_merge_note"])
        for e in sizes:
            assert entity_node(
                data, e, min(len(w.band(e)), tre._MAX_PENDING_CANDIDATES), len(w.band(e))
            )
    assert len(seen) == 3


def test_a5_the_old_failure_is_reproduced_by_this_harness(big):
    """GUARD: the defect is real in the harness -- the unlisted pairs exist, are scored in the band and are not
    in the listed set (so a fix that 'lists all' by raising the cap would still be asked for the number)."""
    data = gate(big)
    listed_ids = {frozenset(p["record_ids"]) for p in data["pending_merge_candidates"]}
    unlisted = {k for e in big.ledger for k in big.band(e)} - listed_ids
    assert len(unlisted) == 1330 - 3 * tre._MAX_PENDING_CANDIDATES


# ── B. at or under the cap ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n", [1, 2, 49, 50])
def test_b1_total_equals_listed_and_the_note_has_no_misleading_of(n):
    cap = tre._MAX_PENDING_CANDIDATES
    assert n <= cap
    w = build({"company": n}, seed=n)
    data = gate(w)
    assert len(data["pending_merge_candidates"]) == n
    # guard (passes today): no "N of M" when nothing is hidden
    assert not n_of_m(note_of(data)), (
        f"misleading 'of' with nothing hidden: {data['pending_merge_note']!r}"
    )
    assert str(n) in data["pending_merge_note"]


@pytest.mark.parametrize("n", [1, 2, 49, 50])
def test_b2_machine_readable_total_equals_listed_when_nothing_is_hidden(n):
    """FAILS today: no field states the total next to the listed count."""
    w = build({"company": n}, seed=n)
    assert entity_node(gate(w), "company", n, n) is not None


def test_b3_one_past_the_cap_flips_to_n_of_m():
    cap = tre._MAX_PENDING_CANDIDATES
    w = build({"company": cap + 1}, seed=3)
    data = gate(w)
    assert (cap, cap + 1) in n_of_m(note_of(data)), data["pending_merge_note"]
    assert entity_node(data, "company", cap, cap + 1) is not None


def test_b4_mixed_entities_cap_is_per_entity_and_so_is_the_total():
    cap = tre._MAX_PENDING_CANDIDATES
    w = build({"company": cap + 25, "person": 12}, seed=4)
    data = gate(w)
    by = listed_by_entity(data)
    assert (len(by["company"]), len(by["person"])) == (cap, 12)
    assert entity_node(data, "company", cap, cap + 25) is not None
    assert entity_node(data, "person", 12, 12) is not None
    assert (cap + 12, cap + 25 + 12) in n_of_m(note_of(data)), data["pending_merge_note"]


def test_b5_no_band_pairs_means_no_note_and_no_crash():
    w = build({"company": 0}, seed=5)
    data = gate(w)
    assert data["pending_merge_candidates"] == [] and data["pending_merge_note"] == ""


# ── C. strongest first, cap unchanged ────────────────────────────────────────────────────────
def test_c1_the_cap_constant_is_unchanged():
    """The report keeps the cap (it says: page, or say 'N of M'); raising it would just move the cliff."""
    src = (ROOT / "src/mcp_server/tools/resolve_entities.py").read_text(encoding="utf-8")
    assert re.search(r"^_MAX_PENDING_CANDIDATES = 50$", src, re.M), "cap constant changed"
    assert tre._MAX_PENDING_CANDIDATES == 50


def test_c2_listed_pairs_are_the_strongest_per_entity_and_sorted(big):
    cap = tre._MAX_PENDING_CANDIDATES
    for e, items in listed_by_entity(gate(big)).items():
        assert len(items) == cap
        confs = [p["confidence"] for p in items]
        assert confs == sorted(confs, reverse=True), f"{e}: not strongest first"
        truth = sorted((round(s, 3) for s in big.band(e).values()), reverse=True)
        assert confs == truth[:cap], f"{e}: the listed pairs are not the top {cap} of the band"
        by_id = big.ledger[e]
        kth = min(by_id[frozenset(p["record_ids"])] for p in items)
        listed_ids = {frozenset(p["record_ids"]) for p in items}
        assert all(s <= kth + 5e-4 for k, s in big.band(e).items() if k not in listed_ids), (
            f"{e}: a stronger pair was left out"
        )


def test_c3_listed_count_is_min_of_cap_and_total_for_any_size():
    cap = tre._MAX_PENDING_CANDIDATES
    for n in (cap - 1, cap, cap + 1, 2 * cap + 7):
        w = build({"company": n}, seed=n)
        assert len(gate(w)["pending_merge_candidates"]) == min(n, cap), n


# ── D. what "total" means ────────────────────────────────────────────────────────────────────
def test_d1_total_equals_pairs_in_the_band_and_the_gate_agrees_with_band_summary(big):
    """The new total, ``band_summary.predicted_counts.possible`` and the ledger count of scores in
    [auto_exclude, auto_match) are the same number.  (Both are computed from the same ``scored_pairs`` with the
    same comparison; the only difference is a NaN score, which neither counts.)  PASSES today for the last two;
    the first is asserted through ``entity_node`` and FAILS today."""
    data, cap = gate(big), tre._MAX_PENDING_CANDIDATES
    for e in big.ledger:
        truth = len(big.band(e))
        assert data["band_summary"][e]["predicted_counts"]["possible"] == truth  # guard
        assert entity_node(data, e, cap, truth) is not None, f"{e}: field does not equal {truth}"


def test_d2_boundaries_are_inclusive_below_exclusive_above():
    """GUARD (passes today): 0.70 is in the band, 0.95 is not, and the epsilons either side are not counted."""
    inside = [AE, AE + 1e-4, 0.8, AM - 1e-4]
    outside = [AE - 1e-4, AM, AM + 1e-4]
    w = build({"company": 4}, seed=9, scores={"company": inside + outside}, extras=False)
    assert len(w.band("company")) == 4
    data = gate(w)
    assert len(data["pending_merge_candidates"]) == 4
    assert data["band_summary"]["company"]["predicted_counts"]["possible"] == 4
    assert not n_of_m(note_of(data))


def test_d2b_the_new_total_respects_the_same_boundaries():
    """FAILS today (no field).  The total must be 4, not 3 (0.70 dropped) / 5 (0.95 counted) / 7 (all)."""
    inside = [AE, AE + 1e-4, 0.8, AM - 1e-4]
    outside = [AE - 1e-4, AM, AM + 1e-4]
    w = build({"company": 4}, seed=9, scores={"company": inside + outside}, extras=False)
    assert entity_node(gate(w), "company", 4, 4) is not None


def test_d3_equal_scores_all_count():
    """Ties: 70 pairs all at the same score -- the total is 70, not 1 and not the cap.  FAILS today (note)."""
    w = build({"company": 70}, seed=70, scores={"company": [0.8] * 70})
    data = gate(w)
    assert len(data["pending_merge_candidates"]) == 50  # guard
    assert (50, 70) in n_of_m(note_of(data)), data["pending_merge_note"]
    assert entity_node(data, "company", 50, 70) is not None


# ── E. the UI (static) ───────────────────────────────────────────────────────────────────────
GATES = ROOT / "src/ui/frontend/src/components/Gates.tsx"


def _match_body():
    txt = GATES.read_text(encoding="utf-8")
    m = re.search(r"function MatchBody\b(.*?)(?=\n// ──|\nfunction [A-Z])", txt, re.S)
    assert m, "MatchBody not found in Gates.tsx"
    return m.group(1)


def test_e1_ui_renders_the_note_in_the_pending_section():
    """GUARD (passes today): whatever the note says is shown."""
    body = _match_body()
    assert "pending_merge_note" in body and "pending.length > 0" in body


def test_e2_ui_shows_n_of_m(big):
    """FAILS today.  The text the reviewer reads must carry the total: either the note (rendered by e1) says
    'N of M' for the 1,330 case, or MatchBody renders a payload key that did not exist before D15 next to an
    'of' (or a 'shown'/'total' label).  A static check -- no browser -- like D2's group C."""
    data, body = gate(big), _match_body()
    note_ok = (
        len(data["pending_merge_candidates"]),
        1330,
    ) in n_of_m(note_of(data)) and "pending_merge_note" in body
    new_keys = sorted(set(data) - OLD_KEYS)
    key_ok = any(re.search(rf"\b{re.escape(k)}\b", body) for k in new_keys) and re.search(
        r"\bof\b|\btotal\b|\bshown\b", body
    )
    assert note_ok or key_ok, (
        f"neither the note ('N of M') nor a new payload key {new_keys} reaches MatchBody's JSX"
    )


def test_e3_ui_does_not_re_cap_or_hardcode_a_total():
    body = _match_body()
    assert not re.search(r"\.slice\(", body), "MatchBody must show the pairs it is given"
    nums = [
        t
        for t in re.findall(
            r"(?<![\w.\"'#-])\d+(?![\w.]*[\"'%])", re.sub(r"toFixed\(\d\)|//.*", "", body)
        )
        if t not in {"0", "1"}
    ]
    assert not nums, f"numeric literal in MatchBody: {nums}"


# ── F. old checkpoints, empty runs, memory-resolved pairs ────────────────────────────────────
def _fake_pairs(n):
    return [
        {"record_ids": [f"a:t:{i}", f"b:t:{i}"], "confidence": round(0.94 - i * 1e-3, 3)}
        for i in range(n)
    ]


@pytest.mark.parametrize(
    "stats",
    [
        {"pending_merge_candidates": _fake_pairs(50)},  # pre-P15 checkpoint: no band_summary at all
        {
            "pending_merge_candidates": _fake_pairs(50),
            "band_summary": {"predicted_counts": {"match": 1, "possible": 1330, "exclude": 9}},
        },
        {"pending_merge_candidates": _fake_pairs(3)},
        {"pending_merge_candidates": [], "band_summary": {}},
        {},
    ],
    ids=["old-no-summary", "old-with-summary", "old-small", "empty", "no-stats"],
)
def test_f1_old_state_never_crashes_and_never_claims_more_than_it_knows(stats):
    state = {
        "resolution_stats": {"company": stats},
        "extracted_data": {"company": []},
        "entity_clusters": {},
    }
    data = gate(state)
    listed = len(data["pending_merge_candidates"])
    for n, m in n_of_m(note_of(data)):
        assert n == listed and m >= listed, f"'{n} of {m}' with {listed} listed"


def test_f2_memory_resolved_pairs_keep_the_arithmetic_coherent():
    """Decision memory (P9) resolves k of the listed pairs before the gate (keep_undecided=True).  Afterwards
    the gate lists 50-k.  Its total must be >= what it lists, and must either drop by k (the k are no longer
    waiting for a decision) or stay (they were band pairs of this run); never 'listed of fewer'."""
    from src.orchestrator.nodes.resolve_entities import apply_band_decisions

    w = build({"company": 80}, seed=80)
    before = gate(w)
    k = 3
    hits = [
        {"entity_type": "company", "record_ids": p["record_ids"], "action": "keep_separate"}
        for p in before["pending_merge_candidates"][:k]
    ]
    pre = {**w.pre, **apply_band_decisions(w.pre, {"pair_decisions": hits}, keep_undecided=True)}
    after = gate(pre)
    listed = len(after["pending_merge_candidates"])
    assert listed == tre._MAX_PENDING_CANDIDATES - k
    pairs = n_of_m(note_of(after))
    assert pairs, f"no 'N of M' although 80 band pairs exist: {after['pending_merge_note']!r}"
    assert all(n == listed and m in (80, 80 - k) for n, m in pairs), (listed, pairs)


# ── G. plan 2.4: no fitted constants in the D15 diff ─────────────────────────────────────────
TOUCHED = [
    "src/orchestrator/nodes/human_nodes.py",
    "src/mcp_server/tools/resolve_entities.py",
    "src/orchestrator/nodes/resolve_entities.py",
    "src/ui/frontend/src/components/Gates.tsx",
]


def _added_lines(paths):
    out = subprocess.run(
        ["git", "diff", "-U0", BASE, "--", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ln[1:] for ln in out.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def _code(line):
    line = re.sub(r"#.*$|//.*$", "", line)
    return re.sub(r"(\"[^\"]*\"|'[^']*')", '""', line)  # numbers in strings/comments are prose


def test_g1_no_new_numeric_literal_in_the_touched_files():
    """The fix is plumbing: a count and a sentence.  A 50, a 1330, a page size or a 'top 20' would be a number a
    corpus count would not produce.  (0 and 1 are the only allowed literals; rounding digits count too.)"""
    bad = []
    for ln in _added_lines(TOUCHED):
        for tok in re.findall(r"(?<![\w.])(\d+\.\d+|\d+)(?![\w.])", _code(ln)):
            if tok not in {"0", "1"}:
                bad.append((tok, ln.strip()[:80]))
    assert not bad, f"new numeric literals in the D15 diff (plan 2.4): {bad}"


def test_g2_no_corpus_names_and_no_restated_cap_in_the_diff():
    added = "\n".join(_added_lines(TOUCHED))
    assert not re.search(r"febrl|abt|buy|dblp|amazon|musicbrainz|pagila|leipzig", added, re.I)
    assert not re.search(r"\b50\b|\b1,?330\b", "\n".join(_code(ln) for ln in _added_lines(TOUCHED)))


def test_g3_only_the_named_files_changed():
    out = subprocess.run(
        ["git", "diff", "--name-only", BASE, "--", "src/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    extra = [f for f in out if f not in TOUCHED and not f.endswith((".pyc", "types.ts", "api.ts"))]
    assert not extra, f"files outside the D15 scope changed: {extra}"
