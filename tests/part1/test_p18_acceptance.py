"""P18 acceptance (derived from the report's "P18" Evidence + Fix text only; no implementation seen).

Report: "Survivorship defects that need no new storage" (five sub-fixes).
  (1) higher priority number wins -- DOCUMENT in place, do not reverse
  (2) priority keyed by (origin, field), falling back to the origin value; the dead
      `measured_reliability` path resolved (used or removed)
  (3) `completeness` (= string length): rename to `longest` for text fields with a config
      alias kept, OR replace with Profisee's record-level fewest-NULLs meaning
  (4) `first_seen` gets a TOTAL order: ... origin ASC, source id ASC
  (5) `numeric_max` still resolves to the max but RECORDS the disagreement and its
      magnitude and surfaces it in the run report (`build_reconciliation_report`)

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p18_acceptance.py -q -p no:cacheprovider

ADAPTATION POINTS (the report fixes behaviour, not spelling -- retarget here only):
  PRIORITY_ENCODINGS   : how a per-(origin, field) priority is written on an origin dict
                         (env P18_PRIORITY_KEY forces one key name)
  RECORD_LEVEL_RULES   : candidate names for the record-level fewest-NULLs rule (option 3B)
  SOURCE_ID_KEY        : the record key that carries the source row id (unify_entities.py builds
                         `{_origin_id}:{_origin_table}:{_origin_id_row}`)
  DISAGREE_KEY         : regex for the conflict-record / run-report key naming the disagreement
Tests marked [A|B] are TWO ALTERNATIVES: exactly one option must pass (see test_3_exactly_one).
"""

from __future__ import annotations

import ast
import asyncio
import itertools
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from src.mcp_server.tools import unify_entity_group as ueg
from src.orchestrator import profiles
from src.orchestrator.nodes.load_data import build_reconciliation_report

ROOT = Path(__file__).resolve().parents[2]
UEG_PATH = Path(ueg.__file__)
OWNED = {
    "unify_entity_group": UEG_PATH,
    "survivorship": ROOT / "src/mcp_server/tools/survivorship.py",
    "unify_entities": ROOT / "src/orchestrator/nodes/unify_entities.py",
    "load_data": ROOT / "src/orchestrator/nodes/load_data.py",
}

# ── adaptation points ─────────────────────────────────────────────────────────
_ENV_KEY = os.environ.get("P18_PRIORITY_KEY")
PRIORITY_ENCODINGS = (
    [_ENV_KEY]
    if _ENV_KEY
    else ["field_priorities", "field_priority", "priority_by_field", "priorities", "fields"]
)
RECORD_LEVEL_RULES = (
    "most_complete_record",
    "record_completeness",
    "most_complete",
    "fewest_nulls",
)
SOURCE_ID_KEY = "_origin_id_row"
DISAGREE_KEY = re.compile(
    r"disagree|magnitude|spread|delta|diff|ratio|variance|deviation|gap", re.I
)


def _unify(
    records: list[dict[str, Any]],
    origins: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    **kw: Any,
) -> dict[str, Any]:
    return asyncio.run(
        ueg.unify_entity_group_impl("c1", records, origins=origins, conflict_policy=policy, **kw)
    )


def _conflict(result: dict[str, Any], field: str) -> dict[str, Any]:
    return next(c for c in result["conflicts"] if c["field"] == field)


def _origin(oid: str, base: int, enc: str | None = None, per_field: dict[str, int] | None = None):
    o: dict[str, Any] = {"id": oid, "priority": base}
    if per_field:
        if enc == "__priority_mapping__":
            o["priority"] = {"_default": base, **per_field}
        else:
            assert enc is not None
            o[enc] = dict(per_field)
    return o


# ── (1) priority direction: documented in place + pinned behaviour ───────────
def _comment_and_doc_text(path: Path) -> str:
    out = []
    src = path.read_text(encoding="utf-8")
    for line in src.splitlines():
        if "#" in line:
            out.append(line.split("#", 1)[1])
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node)
            if d:
                out.append(d)
    return "\n".join(out)


def test_1_direction_documented_in_place() -> None:
    """Report: 'documented in place rather than reversed'. The comment/docstring must say that
    the HIGHER NUMBER wins and that it must not be reversed."""
    text = " ".join(_comment_and_doc_text(UEG_PATH).split())
    higher_wins = re.search(
        r"higher\s+(priority\s+)?(number|value|integer)\b.{0,80}\b(wins?|preferred|prevails?)\b",
        text,
        re.I,
    ) or re.search(r"\b(larger|greater)\s+(number|value)\b.{0,80}\b(wins?|preferred)\b", text, re.I)
    no_reverse = re.search(
        r"(do\s*not|don't|never|must\s+not)\s+(be\s+)?(reverse|invert|flip)", text, re.I
    )
    assert higher_wins, "no comment/docstring states that the HIGHER priority NUMBER wins"
    assert no_reverse, "no comment/docstring says the direction must not be reversed"


def test_1b_higher_number_wins_today() -> None:
    """Pin: diffbot:1, zoominfo:2 -> zoominfo wins contested identity fields (report ①)."""
    recs = [
        {"_origin_id": "diffbot", "name": "Acme", "website": "a.example"},
        {"_origin_id": "zoominfo", "name": "ACME Inc", "website": "z.example"},
    ]
    for order in (recs, recs[::-1]):
        res = _unify(order, [{"id": "diffbot", "priority": 1}, {"id": "zoominfo", "priority": 2}])
        for f in ("name", "website"):
            assert _conflict(res, f)["selected_origin"] == "zoominfo"
            assert _conflict(res, f)["winning_rule"] == "priority"


def test_1c_unset_priority_is_zero_below_positive() -> None:
    res = _unify(
        [{"_origin_id": "x", "name": "N1"}, {"_origin_id": "y", "name": "N2"}],
        [{"id": "y", "priority": 1}],  # x unlisted -> 0
    )
    assert _conflict(res, "name")["selected_origin"] == "y"


# ── (2) priority keyed by (origin, field) ────────────────────────────────────
FIELDS_2 = ("name", "legal_name", "ticker", "domain")  # several, so nothing is keyed on one name


def _scenario_override_wins(enc: str, field: str) -> bool:
    """a(base 1, field=9) vs b(base 2): field override makes a win `field`; the OTHER identity
    field falls back to origin-level (b wins)."""
    other = "website"
    recs = [
        {"_origin_id": "a", field: "from-a", other: "a.example"},
        {"_origin_id": "b", field: "from-b", other: "b.example"},
    ]
    origins = [_origin("a", 1, enc, {field: 9}), _origin("b", 2)]
    ok = True
    for order in (recs, recs[::-1]):
        res = _unify(order, origins)
        ok &= _conflict(res, field)["selected_origin"] == "a"
        ok &= _conflict(res, other)["selected_origin"] == "b"
    return bool(ok)


def _scenario_override_lowers(enc: str, field: str) -> bool:
    """a(base 5, field=0) vs b(base 1): field override LOWERS a on `field` (b wins) while the
    origin-level 5 still wins elsewhere."""
    other = "website"
    recs = [
        {"_origin_id": "a", field: "from-a", other: "a.example"},
        {"_origin_id": "b", field: "from-b", other: "b.example"},
    ]
    origins = [_origin("a", 5, enc, {field: 0}), _origin("b", 1)]
    res = _unify(recs, origins)
    return _conflict(res, field)["selected_origin"] == "b" and (
        _conflict(res, other)["selected_origin"] == "a"
    )


@pytest.mark.parametrize("field", FIELDS_2)
def test_2_field_priority_overrides_origin_priority(field: str) -> None:
    results = {}
    for enc in [*PRIORITY_ENCODINGS, "__priority_mapping__"]:
        try:
            results[enc] = _scenario_override_wins(enc, field)
        except Exception as exc:  # an unsupported encoding raising is simply "does not work"
            results[enc] = f"raised {type(exc).__name__}"
    assert any(v is True for v in results.values()), (
        f"no per-(origin, field) priority encoding changed the winner for {field!r}: {results}"
    )


@pytest.mark.parametrize("field", FIELDS_2)
def test_2b_field_priority_can_lower_and_falls_back(field: str) -> None:
    results = {}
    for enc in [*PRIORITY_ENCODINGS, "__priority_mapping__"]:
        try:
            results[enc] = _scenario_override_lowers(enc, field)
        except Exception as exc:
            results[enc] = f"raised {type(exc).__name__}"
    assert any(v is True for v in results.values()), results


def test_2c_without_field_entry_origin_value_is_used() -> None:
    """The fallback half of the Fix, valid on old AND new code (guards against a rewrite that
    drops origin-level priority)."""
    recs = [
        {"_origin_id": "a", "name": "A", "website": "a.example"},
        {"_origin_id": "b", "name": "B", "website": "b.example"},
    ]
    res = _unify(recs, [{"id": "a", "priority": 1}, {"id": "b", "priority": 2}])
    assert {_conflict(res, f)["selected_origin"] for f in ("name", "website")} == {"b"}


def _mr_removed() -> list[str]:
    """Option R: `measured_reliability` is gone everywhere (rule, default chains, output)."""
    bad = []
    if "measured_reliability" in ueg._KNOWN_RULES:
        bad.append("still in _KNOWN_RULES")
    if any("measured_reliability" in chain for _, chain in ueg._DEFAULT_FIELD_POLICIES):
        bad.append("still in _DEFAULT_FIELD_POLICIES")
    recs = [{"_origin_id": "a", "name": "A"}, {"_origin_id": "b", "name": "B"}]
    rel = {"a": {"name": 0.1}, "b": {"name": 0.9}}
    try:
        res = _unify(recs, None, None, field_reliability=rel)
    except TypeError:
        return bad  # parameter removed too: fine
    c = _conflict(res, "name")
    if c.get("winning_rule") == "measured_reliability" or "reliability_scores" in c:
        bad.append("still produces measured_reliability decisions/evidence")
    return bad


def _mr_used() -> list[str]:
    """Option U: the rule is genuinely live on the MCP-handler path, i.e. when the caller does
    NOT hand in a precomputed `field_reliability` (today a silent no-op -> first_seen)."""
    recs = [
        {"_origin_id": "a", "name": "Acme"},
        {"_origin_id": "a", "name": None},
        {"_origin_id": "b", "name": "Acme Inc"},
    ]
    args = {"cluster_id": "c1", "records": recs}
    res = asyncio.run(ueg.unify_entity_group_handler(args))
    c = _conflict(res, "name")
    bad = []
    if c.get("selected_origin") != "b":
        bad.append("origin b (100% name-complete) did not beat a (50%)")
    if c.get("winning_rule") != "measured_reliability":
        bad.append(f"winning_rule={c.get('winning_rule')!r}")
    if "reliability_scores" not in c:
        bad.append("no reliability_scores evidence")
    return bad


def test_2d_measured_reliability_resolved_R_or_U() -> None:
    """[R|U] Report: 'measured_reliability is dead code ... resolve it (used or removed)'.
    Exactly one of R (removed) / U (live without a caller-supplied table) must hold."""
    r, u = _mr_removed(), _mr_used()
    assert (not r) != (not u), f"need exactly one of R/U; R problems={r}; U problems={u}"


def test_2e_reliability_on_priority_tie_characterisation() -> None:
    """Report says the path is dead 'whenever priority is set'. Verified false on old code: with
    TIED priorities and a table supplied it decides. Skipped (not failed) if option R removed it."""
    if _mr_removed() == []:
        pytest.skip("option R chosen: rule removed")
    recs = [{"_origin_id": "a", "name": "A"}, {"_origin_id": "b", "name": "B"}]
    res = _unify(
        recs,
        [{"id": "a", "priority": 3}, {"id": "b", "priority": 3}],
        field_reliability={"a": {"name": 0.2}, "b": {"name": 0.8}},
    )
    assert _conflict(res, "name")["selected_origin"] == "b"


# ── (3) completeness: [A] rename to longest + alias  |  [B] record-level fewest NULLs ─
LONG = "Acme International Holdings of the Northern Territories"


def _text_conflict(rule: str) -> dict[str, Any]:
    recs = [
        {"_origin_id": "a", "notes": "Acme"},
        {"_origin_id": "b", "notes": LONG},
    ]
    return _unify(recs, None, {"notes": [rule]})


def _option_a_problems() -> list[str]:
    bad = []
    if "longest" not in ueg._KNOWN_RULES:
        return ["'longest' is not a known rule"]
    if ueg.resolve_policy("notes", {"notes": ["longest"]}) != ("longest",):
        bad.append("resolve_policy drops/renames 'longest'")
    # new name works on text
    res = _text_conflict("longest")
    if res["unified_record"]["notes"] != LONG:
        bad.append("longest does not pick the longest text")
    # alias: the OLD name still loads and means the same on text
    res_old = _text_conflict("completeness")
    if res_old["unified_record"]["notes"] != LONG:
        bad.append("old name 'completeness' no longer loads / means something else")
    chain = ueg.resolve_policy("notes", {"notes": ["completeness"]})
    if not chain or chain[0] not in ("longest", "completeness"):
        bad.append(f"alias resolves to {chain!r}")
    # restricted to TEXT: must not rank numbers by digit-string length (9.5 is 3 chars, 10 is 2)
    num = _unify(
        [{"_origin_id": "a", "score": 9.5}, {"_origin_id": "b", "score": 10}],
        None,
        {"score": ["longest", "numeric_max"]},
    )
    if num["unified_record"]["score"] != 10:
        bad.append("longest still ranks numerics by string length")
    return bad


def _record_level_rule() -> str | None:
    names = (*RECORD_LEVEL_RULES, "completeness")
    return next((n for n in names if n in ueg._KNOWN_RULES), None)


def _option_b_problems() -> list[str]:
    """Record with FEWER NULLs wins even when its value is SHORTER (opposite of string length)."""
    full = {  # many non-null fields, SHORT value for `notes`
        "_origin_id": "a",
        "notes": "Acme",
        "email": "e@x",
        "phone": "1",
        "city": "Paris",
        "zip": "75",
    }
    sparse = {  # 4 NULLs, LONG value for `notes`
        "_origin_id": "b",
        "notes": LONG,
        "email": None,
        "phone": None,
        "city": None,
        "zip": None,
    }
    bad = []
    tried = [n for n in (*RECORD_LEVEL_RULES, "completeness") if n in ueg._KNOWN_RULES]
    if not tried:
        return ["no record-level rule name is known"]
    ok = False
    for rule in tried:
        for order in ([full, sparse], [sparse, full]):
            res = _unify(order, None, {"notes": [rule]})
            c = _conflict(res, "notes")
            ok_here = c["selected_origin"] == "a" and res["unified_record"]["notes"] == "Acme"
            if not ok_here:
                break
        else:
            ok = True
    if not ok:
        bad.append(f"none of {tried} selects the fewest-NULLs record over the longer string")
    # and string length must no longer decide under the OLD name (name must not lie)
    if "completeness" in ueg._KNOWN_RULES:
        res = _unify([full, sparse], None, {"notes": ["completeness"]})
        if res["unified_record"]["notes"] != "Acme":
            bad.append("'completeness' still means string length")
    return bad


def test_3a_option_A_longest_with_alias() -> None:
    """[A] `longest` exists, works on text, ignores numerics, old name `completeness` still loads
    and means the same on text."""
    problems = _option_a_problems()
    if problems:
        pytest.fail("; ".join(problems))


@pytest.mark.xfail(
    strict=True,
    reason="lead: option A (rename to longest) chosen for P18-3; B must stay unimplemented",
)
def test_3b_option_B_record_level_fewest_nulls() -> None:
    """[B] a record-level fewest-NULLs rule wins over the longer string (fails on old code)."""
    problems = _option_b_problems()
    if problems:
        pytest.fail("; ".join(problems))


def test_3_exactly_one_option() -> None:
    """The implementer picks ONE of A/B (plan: 'pick one, record why'). Both = incoherent;
    neither = defect unfixed. FAILS on old code (neither)."""
    a, b = not _option_a_problems(), not _option_b_problems()
    assert a != b, f"A passes={a}, B passes={b}; exactly one is required"


def test_3c_name_must_not_imply_what_it_does_not_do() -> None:
    """Report: 'Do not leave a rule whose name implies one thing and whose implementation does
    another.' If a rule named `completeness` is still the DEFAULT-chain rule it must not be the
    old string-length measure on non-text values."""
    res = _unify(
        [
            {"_origin_id": "a", "revenue_note_count": 9.5},
            {"_origin_id": "b", "revenue_note_count": 10},
        ]
    )
    assert res["unified_record"]["revenue_note_count"] == 10  # numeric_max, not length


# ── (4) first_seen: total order, ties by origin ASC then source id ASC ───────
def _fs_records() -> list[dict[str, Any]]:
    base = {"updated_at": "2026-01-01T00:00:00"}  # equal timestamps everywhere
    return [
        {**base, "_origin_id": "zeta", SOURCE_ID_KEY: "r1", "city": "Zeta-r1"},
        {**base, "_origin_id": "alpha", SOURCE_ID_KEY: "r9", "city": "Alpha-r9"},
        {**base, "_origin_id": "alpha", SOURCE_ID_KEY: "r2", "city": "Alpha-r2"},
        {**base, "_origin_id": "mid", SOURCE_ID_KEY: "r0", "city": "Mid-r0"},
    ]


def test_4_first_seen_is_order_independent_and_origin_then_source_asc() -> None:
    recs = _fs_records()
    winners = set()
    for perm in itertools.permutations(recs):  # all 24 input orders
        res = _unify(list(perm), None, {"city": ["first_seen"]})
        winners.add(res["unified_record"]["city"])
    assert winners == {"Alpha-r2"}, f"winner depends on input order / wrong tiebreak: {winners}"


def test_4b_chain_fallthrough_is_also_deterministic() -> None:
    """Same latent bug in `_resolve_conflict`'s final `return candidates[0]`: with default chain
    (priority, completeness, recency) and values of EQUAL length nothing separates them."""
    recs = [
        {"_origin_id": "b", SOURCE_ID_KEY: "r1", "foo": "bbb"},
        {"_origin_id": "a", SOURCE_ID_KEY: "r2", "foo": "aaa"},
        {"_origin_id": "a", SOURCE_ID_KEY: "r1", "foo": "ccc"},
    ]
    winners = {
        _unify(list(p), None, {"foo": ["priority", "completeness", "recency"]})["unified_record"][
            "foo"
        ]
        for p in itertools.permutations(recs)
    }
    assert winners == {"ccc"}, winners  # origin a, source r1


def test_4c_origin_beats_source_id() -> None:
    recs = [
        {"_origin_id": "b", SOURCE_ID_KEY: "r0", "city": "B-r0"},
        {"_origin_id": "a", SOURCE_ID_KEY: "r9", "city": "A-r9"},
    ]
    for p in itertools.permutations(recs):
        assert _unify(list(p), None, {"city": ["first_seen"]})["unified_record"]["city"] == "A-r9"


def test_4d_explicit_rule_still_beats_first_seen() -> None:
    """first_seen is the LAST resort; an earlier rule that separates candidates must still win
    (here priority: z=2 beats a=1 even though origin 'a' sorts first)."""
    recs = [
        {"_origin_id": "a", SOURCE_ID_KEY: "r1", "city": "from-a"},
        {"_origin_id": "z", SOURCE_ID_KEY: "r1", "city": "from-z"},
    ]
    ori = [{"id": "a", "priority": 1}, {"id": "z", "priority": 2}]
    for p in itertools.permutations(recs):
        res = _unify(list(p), ori, {"city": ["priority", "first_seen"]})
        assert res["unified_record"]["city"] == "from-z"


# ── (5) numeric_max records disagreement + magnitude, surfaced in the run report ──────
def _numbers(obj: Any, path: str = "") -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append((path, float(obj)))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += _numbers(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out += _numbers(v, f"{path}[{i}]")
    return out


def _magnitudes(lo: float, hi: float) -> list[float]:
    """Every reasonable definition of 'magnitude of disagreement' (any one is accepted)."""
    d = hi - lo
    return [d, hi / lo, d / hi, d / lo, 100 * d / hi, 100 * d / lo]


def _has_magnitude(conflict: dict[str, Any], lo: float, hi: float) -> list[str]:
    cands = _magnitudes(lo, hi)
    hits = []
    for path, v in _numbers({k: x for k, x in conflict.items() if k != "values"}):
        if not DISAGREE_KEY.search(path):
            continue
        if any(v == pytest.approx(c, rel=1e-3, abs=1e-12) for c in cands):
            hits.append(path)
    return hits


@pytest.mark.parametrize(("lo", "hi"), [(100, 250), (1000, 1100), (3, 4_000_000)])
def test_5_numeric_max_resolves_and_records_magnitude(lo: float, hi: float) -> None:
    recs = [{"_origin_id": "a", "revenue": lo}, {"_origin_id": "b", "revenue": hi}]
    for order in (recs, recs[::-1]):
        res = _unify(order)
        assert res["unified_record"]["revenue"] == hi  # still resolves to the max
        c = _conflict(res, "revenue")
        assert c["winning_rule"] == "numeric_max"
        assert _has_magnitude(c, lo, hi), (
            f"no key matching /{DISAGREE_KEY.pattern}/ holds a magnitude of {lo} vs {hi}: {c}"
        )


def test_5b_magnitude_tracks_the_inputs_not_a_constant() -> None:
    """Two different disagreements must record different magnitudes (kills a hard-coded flag)."""

    def mags(lo: float, hi: float) -> set[float]:
        res = _unify([{"_origin_id": "a", "revenue": lo}, {"_origin_id": "b", "revenue": hi}])
        c = _conflict(res, "revenue")
        return {
            v
            for p, v in _numbers({k: x for k, x in c.items() if k != "values"})
            if DISAGREE_KEY.search(p)
        }

    assert mags(100, 250) and mags(100, 250) != mags(100, 101)


def test_5c_no_threshold_tiny_disagreement_is_still_recorded() -> None:
    """A noise floor (e.g. 'ignore < 5%') would be a fitted constant (plan 2.4)."""
    res = _unify([{"_origin_id": "a", "revenue": 100_000}, {"_origin_id": "b", "revenue": 100_001}])
    c = _conflict(res, "revenue")
    assert [p for p, _ in _numbers(c) if DISAGREE_KEY.search(p)], c


def test_5d_parsed_magnitudes_money_strings() -> None:
    """'$6.1 Million' vs '$6.7 Billion' -> magnitude on the PARSED numbers, not on strings."""
    recs = [
        {"_origin_id": "a", "revenue": "$6.1 Million"},
        {"_origin_id": "b", "revenue": "$6.7 Billion"},
    ]
    res = _unify(recs)
    assert res["unified_record"]["revenue"] == "$6.7 Billion"
    assert _has_magnitude(_conflict(res, "revenue"), 6.1e6, 6.7e9)


def test_5e_agreement_records_no_disagreement() -> None:
    res = _unify([{"_origin_id": "a", "revenue": 500}, {"_origin_id": "b", "revenue": 500}])
    assert res["conflicts"] == []
    assert res["unified_record"]["revenue"] == 500


def test_5f_other_rules_and_unparseable_numeric_unchanged() -> None:
    """numeric_max over non-numbers still defers to the next rule (no crash, no magnitude)."""
    res = _unify(
        [{"_origin_id": "a", "weird_count": "n/a"}, {"_origin_id": "b", "weird_count": "unknown"}],
        [{"id": "a", "priority": 1}, {"id": "b", "priority": 2}],
    )
    assert res["unified_record"]["weird_count"] == "unknown"  # priority decided, b = 2


def _report_for(conflicts: list[dict[str, Any]]) -> dict[str, Any]:
    state = {
        "run_id": "r1",
        "target_entities": ["company"],
        "unified_entities": {"company": [{"name": "Acme", "_conflicts": conflicts}]},
    }
    return build_reconciliation_report(state, {"company": {"inserted": 1, "updated": 0}}, [])


OLD_ENTITY_KEYS = {
    "extracted_per_origin", "extracted_total", "clusters_formed", "uncompared_pairs",
    "candidate_pairs_compared", "auto_merged_clusters", "human_reviewed_clusters",
    "unified_records", "conflicts", "conflict_fields", "conflict_winning_rules",
    "rows_inserted", "rows_updated", "rows_dropped", "type_coercion_failures",
    "fields_dropped_from_mapping",
}  # fmt: skip
OLD_TOP_KEYS = {
    "run_id", "generated_at", "entities", "new_columns", "errors", "load_failed_entities",
    "dropped_inferred_entities", "families_requested", "families_loaded", "completeness_pct",
    "data_quality", "timings",
}  # fmt: skip


def test_5g_run_report_surfaces_field_and_magnitude() -> None:
    """End to end: unify -> feed the REAL conflict records to build_reconciliation_report.
    The report must name the field and carry the magnitude in NEW content (a new entity key, a
    new top-level key, or a red flag) -- the old keys only count conflicts."""
    res = _unify([{"_origin_id": "a", "revenue": 100}, {"_origin_id": "b", "revenue": 250}])
    report = _report_for(res["conflicts"])
    ent = report["entities"]["company"]
    new = {k: v for k, v in ent.items() if k not in OLD_ENTITY_KEYS}
    new |= {k: v for k, v in report.items() if k not in OLD_TOP_KEYS}
    new["red_flags"] = report["data_quality"]["red_flags"]
    text = json.dumps(new, default=str)
    assert "revenue" in text, f"run report does not name the disagreeing field: {text}"
    cands = _magnitudes(100, 250)
    found = any(v == pytest.approx(c, rel=1e-3) for _, v in _numbers(new) for c in cands) or any(
        re.search(rf"(?<![\d.]){re.escape(f'{c:g}')}(?![\d])", text) for c in cands
    )
    assert found, f"no magnitude (one of {cands}) in new report content: {text}"


def test_5h_report_without_numeric_conflicts_adds_nothing_alarming() -> None:
    """Negative: an ordinary priority conflict must not be reported as a numeric disagreement."""
    res = _unify(
        [{"_origin_id": "a", "name": "A"}, {"_origin_id": "b", "name": "B"}],
        [{"id": "a", "priority": 1}, {"id": "b", "priority": 2}],
    )
    report = _report_for(res["conflicts"])
    new = {k: v for k, v in report["entities"]["company"].items() if k not in OLD_ENTITY_KEYS}
    assert not [v for _, v in _numbers(new) if v], f"spurious disagreement content: {new}"


# ── existing configs still load and mean the same thing (snapshot taken on OLD code) ─────────
_NORM = {
    "completeness": "longest"
}  # option A alias; under B the snapshot scenarios agree either way


def _norm(chain: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_NORM.get(r, r) for r in chain)


# resolve_policy(field, policy) -> chain, recorded on the parent commit
SNAPSHOT_CHAINS = {
    ("revenue", None): ("numeric_max", "priority"),
    ("employee_count", None): ("numeric_max", "priority"),
    ("x_count", None): ("numeric_max", "priority"),
    ("name", None): ("priority", "measured_reliability", "non_null", "recency"),
    ("some_id", None): ("priority", "measured_reliability", "non_null", "recency"),
    ("description", None): ("longest", "priority"),
    ("summary", None): ("longest", "priority"),
    ("created_at", None): ("recency",),
    ("other", None): ("priority", "longest", "recency"),
    ("email", ("_default", ("priority",))): ("priority",),
    ("email", ("email", ("completeness",))): ("longest",),
    ("email", ("email", "prefer_origin")): None,  # unknown rule -> default chain for the field
    ("email", ("email", ("newest", "completeness"))): ("longest",),
}


def _snapshot_policy(spec: Any) -> dict[str, Any] | None:
    if spec is None:
        return None
    key, val = spec
    return {key: list(val) if isinstance(val, tuple) else val}


@pytest.mark.parametrize(("key", "expected"), list(SNAPSHOT_CHAINS.items()))
def test_cfg_resolved_chains_unchanged(key: tuple[str, Any], expected: Any) -> None:
    field, spec = key
    got = _norm(ueg.resolve_policy(field, _snapshot_policy(spec)))
    if expected is None:  # unknown-rule config: falls back to the field's built-in default
        expected = _norm(ueg.resolve_policy(field, None))
    if "measured_reliability" not in ueg._KNOWN_RULES:  # option R of (2): removal is allowed
        expected = tuple(r for r in expected if r != "measured_reliability")
    assert got == expected


def test_cfg_default_policy_constant_means_the_same() -> None:
    assert _norm(ueg.DEFAULT_POLICY) == ("priority", "longest", "recency")


def test_cfg_profile_roundtrip_conflict_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved profile (the only on-disk carrier of conflict_policy) still loads and resolves."""
    monkeypatch.setattr(profiles, "PROFILE_DIR", tmp_path)
    pol = {
        "revenue": ["numeric_max"],
        "_default": ["priority", "recency"],
        "notes": ["completeness"],
    }
    prof = profiles.build_profile(
        "p18", user_request="x", origins=[], destination={}, state={"conflict_policy": pol}
    )
    profiles.save_profile(prof)
    state = profiles.apply_profile({}, profiles.load_profile("p18") or {})
    assert state["conflict_policy"] == pol
    assert ueg.resolve_policy("revenue", pol) == ("numeric_max",)
    assert ueg.resolve_policy("city", pol) == ("priority", "recency")
    assert _norm(ueg.resolve_policy("notes", pol)) == ("longest",)


def test_cfg_scenarios_same_winners() -> None:
    """Behavioural snapshot on default chains (same winners as the parent commit)."""
    ori = [{"id": "a", "priority": 1}, {"id": "b", "priority": 2}]
    recs = [
        {
            "_origin_id": "a",
            "name": "A",
            "revenue": 900,
            "description": "short",
            "email": "e1",
            "updated_at": "2020",
            "x_count": 3,
        },
        {
            "_origin_id": "b",
            "name": "B",
            "revenue": 100,
            "description": "much longer description text",
            "email": "e2",
            "updated_at": "2024",
            "x_count": 30,
        },
    ]
    for order in (recs, recs[::-1]):
        got = _unify(order, ori)["unified_record"]
        assert (got["name"], got["revenue"], got["description"]) == (
            "B",
            900,
            "much longer description text",
        )
        assert (got["updated_at"], got["x_count"], got["email"]) == ("2024", 30, "e2")


def test_cfg_files_discovered_on_disk() -> None:
    """Every on-disk file that configures survivorship must be in this set (and so be covered by
    the snapshots above). Baseline today: NONE (config/ has no survivorship keys; profiles are
    written to config/profiles/*.json at runtime and none are committed)."""
    keys = re.compile(r"conflict_policy|origin_priorit|survivorship|numeric_max|first_seen")
    found = set()
    for pat in ("*.yaml", "*.yml", "*.json"):
        for p in (ROOT / "config").rglob(pat):
            if keys.search(p.read_text(encoding="utf-8", errors="ignore")):
                found.add(str(p.relative_to(ROOT)))
    assert found == set(), f"new survivorship config files need a snapshot: {sorted(found)}"


# ── plan 2.4: no fitted constants ─────────────────────────────────────────────
LITERALS = (
    "France", "US", "SAS", "Berhad", "Global", "Group", "Holdings", "companyname",
    "main_entity_id", "company_relation", "staff", "org_title",
)  # fmt: skip
# baseline on parent commit over the four owned files: ZERO hits
BASELINE_HITS: set[tuple[str, str, str]] = set()


def _hits() -> set[tuple[str, str, str]]:
    out = set()
    for name, p in OWNED.items():
        for line in p.read_text(encoding="utf-8").splitlines():
            for w in LITERALS:
                if re.search(rf"(?<![A-Za-z0-9_]){w}(?![A-Za-z0-9_])", line):
                    out.add((name, w, line.strip()))
    return out


def test_6_no_new_corpus_literals_in_owned_files() -> None:
    new = sorted(_hits() - BASELINE_HITS)
    assert not new, f"new corpus literals: {new}"


# distinct numeric literal VALUES per owned file/function today (parent commit); structural 0/1/2/-1
BASELINE_NUMS = {
    "unify_entity_group": {0, 1},
    "survivorship": {0, 1, 4},  # round(..., 4)
    "unify_entities": {0, 12},  # cluster_id[:12] log slice
    "load_data": {0, 1, 4, 0.95, 100},  # build_reconciliation_report: round 4, 0.95 band, 100*
}
STRUCTURAL = {0, 1, 2, -1}


def _num_values(name: str) -> set[float]:
    tree: ast.AST = ast.parse(OWNED[name].read_text(encoding="utf-8"))
    if name == "load_data":
        tree = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "build_reconciliation_report"
        )
    c = Counter(
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and type(n.value) in (int, float)
    )
    return set(c)


@pytest.mark.parametrize("name", list(BASELINE_NUMS))
def test_6b_no_new_numeric_literals(name: str) -> None:
    """'Would a corpus count produce this?' A new threshold (e.g. a 10% noise floor for the (5)
    disagreement, a 0.5 tie weight) shows up here as a literal not in the baseline."""
    new = _num_values(name) - BASELINE_NUMS[name] - STRUCTURAL
    assert not new, f"{name}: new numeric literal(s) {sorted(new)} -- derive from data or justify"


def test_6c_no_new_module_level_numeric_constants() -> None:
    new = {}
    for name, p in OWNED.items():
        for node in ast.parse(p.read_text(encoding="utf-8")).body:
            v = node.value if isinstance(node, ast.Assign) else None
            if isinstance(v, ast.Constant) and type(v.value) in (int, float):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        new.setdefault(name, set()).add(t.id)
    # baseline: only load_data._SCOPED_MERGE_GUARDRAIL_MIN_CLUSTERS
    assert new == {"load_data": {"_SCOPED_MERGE_GUARDRAIL_MIN_CLUSTERS"}}, new
