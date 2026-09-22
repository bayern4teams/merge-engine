"""BP-B4 — the measured A/B gate that decides whether Splink becomes the default engine.

The plan (Branch_Plan B.4) and ADR 0004 say, in one sentence: *Splink becomes the
default engine only when, over the 560 golden labelled pairs, it beats the builtin
Fellegi-Sunter engine on BOTH precision AND recall, AND the Airbus golden-parity
score exceeds the 12.0% Boeing baseline* — and the verdict must be **measured**
here, not asserted. ADR 0004 is therefore "accepted, **gated**": the engine is
available (UI per-entity ``engine: splink``) but not the default until both legs
of this file pass.

Two slow measurement legs (each writes its own outcome to ``last_gate.json``):

* ``test_splink_beats_builtin_on_golden_pairs`` — the A/B leg. The assertion IS the
  gate; on failure it reports the measured numbers so the branch owner sees *why*
  it was not met (a decision record, not a flaky test).
* ``test_airbus_parity_exceeds_boeing_baseline`` — the parity leg. Reuses the Phase-7
  ``compare_to_golden`` mechanism (``SideData``/``compare_table``/overall parity).
  Skips (not fails) when no live Airbus app run exists — a missing number is not a
  gate pass.

The fast promotion guard that makes the suite honest in the current gated state
lives in ``tests/unit/test_splink_default_invariant.py`` (runs on every ``make test``):
*Splink may not be the default engine unless both legs have been measured to pass.*
It PASSES now (default = builtin, gate not met) and only FAILS if someone promotes
Splink without passing the gate.

Run the slow legs explicitly (they are excluded from ``make test`` / ``check.py
test``, which scope to ``tests/unit tests/integration``; they train a real EM model
over the golden record set):

    .venv/Scripts/python.exe -m pytest tests/eval/test_matcher_recall.py -m slow -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tests.eval import score  # noqa: E402
from tests.eval.golden import build_golden_pairs  # noqa: E402

# ── gate constants (ADR 0004 / Branch_Plan B.4) ────────────────────────────────

# Canonical golden set the gate is defined over. build_golden_pairs() returns
# 200 match + 200 shared-first + 60 shared-surname + 100 random = 560.
EXPECTED_GOLDEN_PAIRS = 560
_GOLDEN_CSV = _ROOT / "tests" / "eval" / "pagila_staff.csv"

# Boeing's measured golden parity: overall row-level parity (12.0%) and
# 50.9% person-level (13/33 exact email). The parity leg gates on the OVERALL
# figure (compare_to_golden.totals.overall_parity_pct), which is the number
# BUDGETS.yaml / Branch_Plan cite as the Boeing baseline Splink must beat.
BOEING_OVERALL_PARITY_PCT = 12.0
BOEING_PERSON_PARITY_PCT = 50.9

# Where the measured gate outcomes land. The unit invariant reads this file.
_GATE_RECORD = Path(__file__).resolve().parent / "last_gate.json"

# ── shared gate-record plumbing ────────────────────────────────────────────────


def _merge_gate_record(**fields: object) -> dict:
    """Read-merge-write the last measured gate outcome so each leg records its own
    result without clobbering the other. ``gate_passed`` is recomputed: BOTH legs
    must be measured and met. Returns the merged record."""
    record: dict = {}
    if _GATE_RECORD.exists():
        try:
            record = json.loads(_GATE_RECORD.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            record = {}
    record.update(fields)
    a_met = bool(record.get("a_met"))
    b_met = bool(record.get("b_met"))
    a_meas = bool(record.get("a_measurable"))
    b_meas = bool(record.get("b_measurable"))
    record["gate_passed"] = a_meas and a_met and b_meas and b_met
    _GATE_RECORD.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record


def _default_engine() -> str:
    """The default engine from ``config.yaml`` (the promotion surface ADR 0004
    names). Returns ``"builtin"`` when no default engine is declared — matching
    the code path: ``resolve_entities`` falls back to builtin Fellegi-Sunter when
    an entity has no ``engine`` override.

    The config currently declares no matching section, so the default is builtin.
    If a future PR adds a default-engine declaration, this reader finds it in the
    conventional locations so the guard keeps working.
    """
    import yaml

    cfg_path = _ROOT / "config" / "config.yaml"
    if not cfg_path.exists():
        return "builtin"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    for key in ("entity_resolution", "matching_rules", "matching", "engine"):
        section = cfg.get(key)
        if isinstance(section, dict):
            engine = section.get("default_engine") or section.get("engine")
            if isinstance(engine, str) and engine:
                return engine.lower()
            for _entity, rules in section.items():
                if isinstance(rules, dict) and rules.get("engine") == "splink":
                    return "splink"
    return "builtin"


# ── A/B measurement helpers ────────────────────────────────────────────────────


def _builtin_scorer(records: list[dict], pairs: list[tuple[dict, dict, bool, float]]):
    """A builtin pairwise scorer over the exact record set + candidate pairs,
    mirroring ``tests/eval/run_matcher_eval.py``'s A/B comparison so the builtin
    leg is the production Fellegi-Sunter path (not a reimplementation)."""
    from src.mcp_server.tools.resolve_entities import _fit_match_model, _record_id, _score_pair

    candidate_pairs = [(str(_record_id(a)), str(_record_id(b)), a, b) for a, b, _s, _w in pairs]
    model, _ = _fit_match_model(records, candidate_pairs)

    def scorer(rec_a: dict, rec_b: dict) -> float:
        return _score_pair(rec_a, rec_b, model)

    return scorer


def _pair_report_via_bscorer(scorer, pairs: list[tuple[dict, dict, bool, float]]):
    """Run the A/B pair through ``evaluate``'s decision bands, returning the
    PairReport. One helper so each engine leg is measured identically."""
    return score.evaluate(
        scorer,
        pairs,
        auto_match=0.95,
        auto_exclude=0.70,
    )


# ── the measured gate legs ─────────────────────────────────────────────────────


@pytest.mark.slow
def test_splink_beats_builtin_on_golden_pairs() -> None:
    """Measure both engines over the 560 golden pairs; assert the A/B gate.

    The outcome (met or not) is always recorded to ``last_gate.json`` so the unit
    invariant and the docs reflect the last real measurement — the failure message
    then reports the numbers for the branch owner's decision.
    """
    if not _GOLDEN_CSV.exists():
        _merge_gate_record(a_measurable=False, a_met=False)
        pytest.skip(
            f"golden data {_GOLDEN_CSV.name} is not present in this worktree; the A/B leg "
            "cannot be measured. Restore the 1500-row pagila_staff.csv (see the other "
            "worktrees / main) before asserting the gate."
        )

    pairs = build_golden_pairs()
    n_same = sum(1 for _a, _b, same, _w in pairs if same)
    n_diff = len(pairs) - n_same
    if len(pairs) != EXPECTED_GOLDEN_PAIRS:
        _merge_gate_record(a_measurable=False, a_met=False)
        pytest.fail(
            f"golden set is {len(pairs)} pairs ({n_same} match / {n_diff} non-match), not the "
            f"{EXPECTED_GOLDEN_PAIRS} the BP-B4 gate is defined over. Do not assert a gate on "
            "the wrong set — restore the full pagila_staff.csv (1500 distinct staff) and re-run."
        )

    records = [r for pair in pairs for r in (pair[0], pair[1])]

    builtin = _pair_report_via_bscorer(_builtin_scorer(records, pairs), pairs)

    splink_scorer = score.splink_scorer_from_records("person", records)
    if splink_scorer is None:
        _merge_gate_record(a_measurable=False, a_met=False)
        pytest.fail(
            "the Splink scorer is unavailable (splink not importable in this interpreter, or "
            "training failed over the golden record set). The gate cannot be measured without "
            "a Splink side — this is not a pass."
        )
    splink = _pair_report_via_bscorer(splink_scorer, pairs)

    a_met = (splink.precision > builtin.precision) and (splink.recall > builtin.recall)
    _merge_gate_record(a_measurable=True, a_met=a_met)

    summary = (
        f"BP-B4 golden-pair A/B over {len(pairs)} pairs ({n_same} match / {n_diff} non-match)\n"
        f"  builtin  precision={builtin.precision:.4f} recall={builtin.recall:.4f} "
        f"fp={builtin.false_positive} fn={builtin.false_negative} review_same={builtin.review_same}\n"
        f"  splink   precision={splink.precision:.4f} recall={splink.recall:.4f} "
        f"fp={splink.false_positive} fn={splink.false_negative} review_same={splink.review_same}\n"
        f"  gate (beat builtin on BOTH precision and recall): {a_met}"
    )

    if not a_met:
        pytest.fail(
            summary
            + "\n\nSplink did NOT beat the builtin on both precision and recall, so per BP-B4 "
            "it must not become the default engine on this evidence. (This is the gate doing "
            "its job — a measured no, not a bug. To promote Splink, recover its recall: the "
            "exact-name blocking rule never scores ``name`` and misses corrupted-name match "
            "pairs, which is precisely what B.4's costed blocking (S-E) and threshold-from-"
            "labels (S-L) are built to fix. Re-measure here after that work.)"
        )
    print("\n" + summary)


@pytest.mark.slow
def test_airbus_parity_exceeds_boeing_baseline() -> None:
    """Measure Airbus golden parity against the 12.0% Boeing baseline.

    Reuses the Phase-7 ``compare_to_golden`` mechanism directly (``SideData`` /
    ``compare_table`` / overall parity) — no reimplementation of the comparison.
    Skips (not fails) when no live Airbus app run exists, because a missing number
    is not a gate pass: the branch owner must run the Airbus subject before this
    leg can be measured.
    """
    try:
        import psycopg2
    except ImportError:
        _merge_gate_record(b_measurable=False, b_met=False)
        pytest.skip("psycopg2 not importable; the parity leg cannot connect to PostgreSQL")

    # Defaults match compare_to_golden.main() and BUDGETS.yaml's eval harness.
    DSN = dict(host="localhost", port=55201, user="postgres", password="pass")

    try:
        app_conn = psycopg2.connect(dbname="global_warehouse", **DSN)
    except psycopg2.OperationalError as e:
        _merge_gate_record(b_measurable=False, b_met=False)
        pytest.skip(f"cannot reach global_warehouse for the parity leg: {e}")

    try:
        with app_conn.cursor() as cur:
            cur.execute("SELECT name FROM public.company ORDER BY name")
            app_companies = [r[0] for r in cur.fetchall()]
    finally:
        app_conn.close()

    if not any("airbus" in c.lower() for c in app_companies):
        _merge_gate_record(b_measurable=False, b_met=False)
        pytest.skip(
            f"Airbus parity leg not measurable yet: 'global_warehouse.public.company' "
            f"holds {app_companies} but no Airbus subject — the parity leg measures an "
            f"*Airbus* app run against the Airbus golden baseline. The current run is a "
            f"different subject, so there is no Airbus number to gate. "
            f"(Boeing overall-parity baseline: {BOEING_OVERALL_PARITY_PCT}%.)"
        )

    # ── an Airbus app run exists: measure the real parity via the Phase-7 mechanism ─
    from scripts.diagnostics.compare_to_golden import TABLES, SideData, compare_table

    try:
        golden_conn = psycopg2.connect(dbname="global_warehouse_golden", **DSN)
        app_conn = psycopg2.connect(dbname="global_warehouse", **DSN)
    except psycopg2.OperationalError as e:
        _merge_gate_record(b_measurable=False, b_met=False)
        pytest.skip(f"cannot reach the golden/app DBs for the parity comparison: {e}")

    try:
        golden = SideData(golden_conn, "airbus")
        app = SideData(app_conn, "public")
    except Exception as e:  # a missing golden schema / table is a "not measurable" case
        golden_conn.close()
        app_conn.close()
        _merge_gate_record(b_measurable=False, b_met=False)
        pytest.skip(f"Airbus golden baseline unavailable for the parity leg: {e}")

    table_diffs = [compare_table(t, golden, app) for t in TABLES]
    total_golden = sum(d["golden_rows"] for d in table_diffs)
    total_matched = sum(d["matched"] for d in table_diffs)
    overall_parity = round(100.0 * total_matched / total_golden, 1) if total_golden else 0.0
    person_parity = next((d["parity_pct"] for d in table_diffs if d["table"] == "person"), 0.0)
    golden_conn.close()
    app_conn.close()

    b_met = overall_parity > BOEING_OVERALL_PARITY_PCT
    _merge_gate_record(b_measurable=True, b_met=b_met, overall_parity_pct=overall_parity)

    summary = (
        f"BP-B4 Airbus golden parity (measured via compare_to_golden):\n"
        f"  overall parity = {overall_parity}%  (person-level = {person_parity}%)\n"
        f"  Boeing baseline to exceed: {BOEING_OVERALL_PARITY_PCT}% overall "
        f"({BOEING_PERSON_PARITY_PCT}% person-level)\n"
        f"  gate (overall parity > Boeing): {b_met}\n"
        + "\n".join(
            f"    {d['table']:<22} golden={d['golden_rows']:<5} matched={d['matched']:<5} "
            f"parity={d['parity_pct']}%"
            for d in table_diffs
        )
    )
    if not b_met:
        pytest.fail(
            summary
            + "\n\nAirbus golden parity did NOT exceed the Boeing baseline, so per BP-B4 the "
            "parity leg is not met. Do not promote Splink on this evidence."
        )
    print("\n" + summary)


def test_stub_staff_dataset_is_rejected_before_splink_blocking() -> None:
    """The 2-row load_staff() fallback must be skipped, not fed to Splink.

    Regression guard for the CI hang: a blanket `*.csv` in .gitignore kept
    tests/eval/pagila_staff.csv untracked, so load_staff() fell back to its
    stub and the resulting 204 records carried 3 distinct names. Splink's
    blocking never returned on that, hanging `check.py eval` indefinitely.
    """
    from tests.eval.golden import build_golden_pairs
    from tests.eval.score import is_degenerate_for_splink

    records = [r for pair in build_golden_pairs() for r in (pair[0], pair[1])]
    assert is_degenerate_for_splink(records) is True

    # A healthy set (every record a distinct person) must NOT be rejected.
    healthy = [{"first_name": f"First{i}", "last_name": f"Last{i}"} for i in range(200)]
    assert is_degenerate_for_splink(healthy) is False
