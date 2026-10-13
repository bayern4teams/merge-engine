"""P19 attack (verifier): edge cases, engine agreement and static no-label checks for blocking_report.

Passing tests are hand-computed. Strict xfails are defects found by the attack: each one fails today and
flips to XPASS (error) once fixed, so the fix must remove the marker.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p19_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import ast
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MERGE_ENGINE_DISABLE_RESOLVE_CACHE", "1")

from src.mcp_server.tools import resolve_entities as re_
from tests.eval.score import blocking_report as br
from tests.part1 import fixtures_matching as fx

ROOT = Path(__file__).resolve().parents[2]
BASE_REF = "eeb280b"
GT_KEYS = {"pair_completeness", "pairs_quality"}


def I(row: str) -> str:  # noqa: E743
    return f"s:t:{row}"


def R(row: str, text: str) -> dict:
    return fx._rec("s", row, v=text)


def P(a: str, b: str) -> tuple[str, str]:
    return (I(a), I(b))


# N=5 a{x,y} b{x,z} c{y,z} d{w} e{x}: blocks x={a,b,e} y={a,c} z={b,c}; sum|b|=7, K=3.5, k=1.4, all-pairs=10
EX1 = [R("a", "x y"), R("b", "x z"), R("c", "y z"), R("d", "w"), R("e", "x")]
CAND = [P("a", "b"), P("a", "c")]


# ---------------------------------------------------------------- hand-checked edge cases (pass)
def test_candidates_self_pairs_duplicates_both_orders_collapse():
    rep = br(EX1, [P("a", "a"), P("a", "b"), P("b", "a"), P("a", "b"), P("a", "c"), P("c", "c")])
    assert rep["candidate_pairs"] == 2
    assert rep["reduction_ratio"] == pytest.approx(0.8)
    assert not GT_KEYS & set(rep)


def test_truth_duplicates_both_orders_and_self_pairs():
    # truth normalises to {ab, cd}; hit = {ab}; PC = 1/2, PQ = 1/2
    truth = [P("a", "b"), P("b", "a"), P("c", "d"), P("d", "c"), P("a", "b"), P("e", "e")]
    rep = br(EX1, CAND, truth)
    assert rep["pair_completeness"] == pytest.approx(0.5)
    assert rep["pairs_quality"] == pytest.approx(0.5)


def test_truth_unknown_ids_stay_in_denominator():
    # documented reading: a truth pair that names no corpus record is a miss; truth {ab, zz-yy} -> PC 1/2
    rep = br(EX1, [P("a", "b")], [P("a", "b"), P("zz", "yy")])
    assert rep["pair_completeness"] == pytest.approx(0.5)
    assert rep["pairs_quality"] == pytest.approx(1.0)


def test_truth_four_tuples_and_generators_accepted_once():
    cand = ((x, y, {}, {}) for x, y in CAND)
    truth = ((x, y) for x, y in [P("a", "b")])
    rep = br(EX1, cand, truth)
    assert rep["candidate_pairs"] == 2 and rep["pair_completeness"] == pytest.approx(1.0)


def test_one_record_and_duplicate_ids():
    one = br([R("a", "x")], [])
    assert one["records"] == 1 and one["total_comparisons"] == 0
    assert "reduction_ratio" not in one and one["cep_K"] == 0 and not GT_KEYS & set(one)
    # the same id twice is one record (the engine keeps one per id): N=2, one block {a,b}
    dup = br([R("a", "x"), R("a", "x"), R("b", "x")], [])
    assert dup["records"] == 2 and dup["total_comparisons"] == 1
    assert dup["block_assignments"] == 2 and dup["cep_K"] == 1.0 and dup["cnp_k"] == 1.0


def test_zero_records_no_crash_rr_absent_no_pc_without_truth():
    rep = br([], [])
    assert rep["records"] == 0 and rep["total_comparisons"] == 0
    assert "reduction_ratio" not in rep and not GT_KEYS & set(rep)
    # truth=[] with no candidates and no records: nothing defined
    rep = br([], [], [])
    assert not GT_KEYS & set(rep)


def test_empty_candidates_and_empty_truth_are_absent_not_zero():
    # empty candidates: RR = 1.0 (defined), PQ is 0/0 -> absent; PC defined (0.0 is genuine) only with truth
    rep = br(EX1, [], [P("a", "b")])
    assert rep["reduction_ratio"] == 1.0
    assert "pairs_quality" not in rep
    assert rep["pair_completeness"] == 0.0  # genuine: one true pair, none retained
    # empty truth: PC is 0/0 -> absent (never 0.0)
    rep = br(EX1, CAND, [])
    assert "pair_completeness" not in rep
    # empty truth AND empty candidates: neither
    assert not GT_KEYS & set(br(EX1, [], []))
    # truth that is only self-pairs normalises to empty -> PC absent
    assert "pair_completeness" not in br(EX1, CAND, [P("a", "a")])


def test_no_truth_never_emits_recall_like_keys():
    rep = br(EX1, CAND)
    assert not GT_KEYS & set(rep)
    assert not [k for k in rep if re.search(r"recall|precision|f1|complete|quality|miss", k, re.I)]
    assert all(v is not None and v == v for v in rep.values())


def test_truth_changes_only_the_two_keys():
    base = br(EX1, CAND)
    for truth in ([P("a", "b")], [P("c", "d")], [], [P("x", "y")]):
        rep = br(EX1, CAND, truth)
        assert {k: v for k, v in rep.items() if k not in GT_KEYS | {"truth_ids_unmatched"}} == base


def test_deterministic_shuffled_and_dict_order_and_hashseed():
    want = br(EX1, CAND, [P("a", "b")])
    rng = random.Random(7)
    for _ in range(10):
        recs = [dict(reversed(list(r.items()))) for r in EX1]
        rng.shuffle(recs)
        cand = CAND[:]
        rng.shuffle(cand)
        assert br(recs, cand, [P("b", "a")]) == want
    code = (
        "import os;os.environ['MERGE_ENGINE_DISABLE_RESOLVE_CACHE']='1';"
        "from tests.eval.score import blocking_report as b;"
        "from tests.part1 import fixtures_matching as fx;"
        "R=lambda r,t: fx._rec('s',r,v=t);"
        "e=[R('a','x y'),R('b','x z'),R('c','y z'),R('d','w'),R('e','x')];"
        "print(sorted(b(e,[('s:t:a','s:t:b')],[('s:t:a','s:t:b')]).items()))"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": s, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=True,
        ).stdout.strip()
        for s in ("0", "1", "4242")
    }
    assert len(outs) == 1


def test_real_engine_candidates_feed_the_report():
    recs = [R(str(i), f"acme {i % 3} u{i}") for i in range(12)]
    stats: dict = {}
    cand = re_._generate_candidate_pairs(recs, stats=stats)
    rep = br(recs, cand)
    assert rep["candidate_pairs"] == stats["candidate_pairs"] == len(cand)
    assert rep["records"] == 12 and rep["total_comparisons"] == 66
    assert rep["reduction_ratio"] == pytest.approx(1 - len(cand) / 66)
    assert 0 <= rep["reduction_ratio"] <= 1


# ---------------------------------------------------------------- static: no labels, no fitted constants
def test_no_label_reaches_src_and_blocking_report_is_not_imported_by_src():
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                mods = [n.module or ""]
            for m in mods:
                assert not re.match(r"(tests|tests\.eval)(\.|$)", m), f"{path}: imports {m}"
                assert "blocking_report" not in [a.name for a in getattr(n, "names", [])], path


def test_blocking_report_numeric_literals_and_corpus_greps():
    src = (ROOT / "tests/eval/score.py").read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "blocking_report"
    )
    nums = {
        n.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
    }
    assert nums <= {0, 1, 2}, nums
    corpus = re.compile(
        r"\b(France|US|SAS|Berhad|Global|Group|Holdings|companyname|main_entity_id|company_relation|staff|"
        r"org_title|company_name|pagila|dvdrental)\b"
    )
    added = subprocess.run(
        [
            "git",
            "diff",
            "-U0",
            BASE_REF,
            "--",
            "tests/eval/score.py",
            "src/mcp_server/tools/resolve_entities.py",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    ).stdout
    # only the report function's own body is in scope (the lead edits the engine concurrently)
    body = "\n".join(src.splitlines()[fn.lineno - 1 : fn.end_lineno])
    assert not corpus.search(body)
    assert "blocking_report" in added


# ---------------------------------------------------------------- defects (strict xfail)
def test_tokenless_records_match_engines_fallback_block():
    recs = [R("a", ""), R("b", ""), R("c", "")]
    stats: dict = {}
    re_._generate_candidate_pairs(recs, stats=stats)
    assert (
        stats["blocks_used"] == 1 and stats["candidate_pairs"] == 3
    )  # engine: one block of all three
    rep = br(recs, [P("a", "b"), P("a", "c"), P("b", "c")])
    assert rep["block_assignments"] == 3 and rep["cep_K"] == 1.5 and rep["cnp_k"] == 1.0


def test_explicit_blocking_keys_are_honoured():
    recs = [
        {"_origin_id": "s", "_origin_table": "t", "id": str(i), "name": n, "city": c}
        for i, (n, c) in enumerate(
            [("acme", "paris"), ("acme", "rome"), ("zed", "paris"), ("zed", "oslo")]
        )
    ]
    cand = re_._generate_candidate_pairs(recs, blocking_keys=["name"])
    rep = br(
        recs, cand, blocking_keys=["name"]
    )  # engine blocks on name only: acme={0,1}, zed={2,3}
    assert rep["block_assignments"] == 4 and rep["cep_K"] == 2.0 and rep["cnp_k"] == 1.0


def test_truth_naming_no_corpus_record_is_not_reported_as_zero():
    rep = br(EX1, CAND, [("a", "b"), ("c", "d")])  # raw row ids, not engine ids: nothing can match
    assert "pair_completeness" not in rep or rep["pair_completeness"] != 0.0


def test_reduction_ratio_stays_in_unit_interval_or_absent():
    rep = br(EX1, [(I(f"q{i}"), I(f"r{i}")) for i in range(20)])
    assert "reduction_ratio" not in rep or 0 <= rep["reduction_ratio"] <= 1


def test_k_absent_when_no_records():
    assert "cnp_k" not in br([], [])


@pytest.mark.xfail(
    strict=True,
    reason="D6 (debt): nothing in the eval harness calls blocking_report; only the old recall print runs",
)
def test_report_is_wired_into_the_harness():
    src = (ROOT / "tests/eval/score.py").read_text()
    assert len(re.findall(r"\bblocking_report\(", src)) > 1  # definition + at least one call
