"""D5 attack (adversarial, written after the fix 5183478).  Strict xfail = a defect found that the
worker's own tests miss; each reason names it.  Controls (no xfail) pin behaviour that must hold.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d5_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.mcp_server.tools.fellegi_sunter import (
    build_token_document_frequencies,
    idf_soft_similarity,
)
from src.mcp_server.tools.resolve_entities import _record_id, _resolve_core, _token_ratio

from .test_p6_attack import (
    R,
    cls,
    run,
    tk,
)

FIX = Path(__file__).parent / "live_repros" / "p14_dblp_addendum.json"
STRONG = 0.90


def _recs():
    return json.loads(FIX.read_text())["records"]


def _title_sim(a: str, b: str) -> float:
    df, n = build_token_document_frequencies(_recs())
    tok = lambda s: set(re.findall(r"[a-z0-9]+", s.lower()))  # noqa: E731
    out = idf_soft_similarity(tok(a), tok(b), df["title"], n["title"], _token_ratio)
    assert out is not None
    return out


def _pair_score(recs):
    core = _resolve_core("publication", recs, None, None, None)
    sc = {frozenset((a, b)): s for a, b, s in core["scored_pairs"]}
    sc |= {frozenset((a, b)): s for a, b, _r1, _r2, s in core["borderline"]}
    return sc[frozenset((_record_id(recs[1]), _record_id(recs[2])))], core["auto_match"]


X = "Automatic Generation of Production Rules for Integrity Maintenance"


# ---- the containment cliff: one typo in the shared title and the defect is back ----------------
def test_control_exact_containment_is_not_strong():
    assert _title_sim(X, "Addendum to " + X) < STRONG


@pytest.mark.xfail(
    strict=True,
    reason="D5 attack: one typo'd token breaks token-set containment -> mean -> STRONG (0.91)",
)
@pytest.mark.parametrize("word", ["Automatic", "Production", "Maintenance"])
def test_addendum_with_one_typo_in_shared_title_is_not_strong(word):
    typo = word[:-1] + "x"
    assert _title_sim(X.replace(word, typo), "Addendum to " + X) < STRONG


@pytest.mark.xfail(
    strict=True,
    reason="D5 attack: report fixture + one typo in the base title auto-matches (0.967 >= 0.95)",
)
def test_pipeline_report_fixture_with_one_typo_is_not_an_auto_match():
    recs = _recs()
    recs[1] = dict(recs[1], title=recs[1]["title"].replace("Production", "Productoin"))
    score, auto = _pair_score(recs)
    assert score < auto, score


def test_control_report_fixture_margin():
    """The worker's accepted fixture result is real, but thin: 0.933 vs the 0.95 bar."""
    score, auto = _pair_score(_recs())
    assert score < auto


# ---- cases the fix must not make worse ---------------------------------------------------------
def test_control_duplicate_tokens_and_single_token_names():
    df, n = {"john": 5, "smith": 5}, 100
    f = lambda a, b: idf_soft_similarity(a, b, df, n, _token_ratio)  # noqa: E731
    assert f({"john"}, {"john"}) == 1.0
    assert f({"john", "smith"}, {"john", "smith"}) == 1.0
    assert f({"john"}, set()) is None


# ---- P6 regression: dropping a legal form in a tiny corpus (the lead's finding) ------------------
def _p6_corpus(extra_ltd_fillers: int, filler_ltd_every: int = 1):
    recs, groups = [], []
    for g in range(6):
        t = tk(g)
        names = (f"{t} Widgets Ltd", f"{t} Widgets Limited", f"{t} WIDGETS")
        gi = [
            R("a", f"{g}_{j}", name=n, email=f"info@{t.lower()}.example", address=f"{g + 5} Road")
            for j, n in enumerate(names)
        ]
        recs += gi
        groups.append(gi)
    for k in range(extra_ltd_fillers):
        t = tk(1000 + k)
        suffix = " Ltd" if k % filler_ltd_every == 0 else ""
        recs.append(
            R(
                "a",
                f"f{k}",
                name=f"{t} Trading{suffix}",
                email=f"x@{t.lower()}.example",
                address=f"{k} Lane",
            )
        )
    return recs, groups


@pytest.mark.parametrize("fillers,every", [(0, 1), (60, 3), (200, 3), (200, 1)])
def test_legal_form_dropped_names_still_merge_when_email_and_address_agree(fillers, every):
    recs, groups = _p6_corpus(fillers, every)
    res = run("company", recs)
    bad = [
        (g[0]["_origin_id_row"], x["_origin_id_row"])
        for g in groups
        for x in g[1:]
        if cls(res, g[0], x) != "merged"
    ]
    assert not bad, bad
