"""P14 acceptance (derived from the report's Evidence + Fix text only; no implementation seen).

Report: "Term-frequency weighting operates at the wrong grain".  `build_term_frequencies`
is IDF at the WHOLE-VALUE grain, so the token "Group" is never down-weighted.  Fix:
(1) add `build_token_document_frequencies` -> {field: {token: df}} + {field: N} BESIDE the
value-level function; (2) keep the value-level path for atomic fields; (3) text fields
select their agreement level (EXACT/STRONG/WEAK/DISAGREE kept, D-14) from an IDF-weighted
similarity; (5) log2 throughout.  Plan accept: with "Group" high-df, *Acme Group Ltd* vs
*Beta Group Ltd* scores BELOW *Acme Group Ltd* vs *Acme Holdings Ltd*; atomic-field
scores unchanged; NULL input does not crash; no suffix/stop-word list literal.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p14_acceptance.py -q -p no:cacheprovider
Negative cases (the inversion tests, 1*, and the df-function tests, 4*) FAIL on the old code.

ADAPTATION POINTS (everything the report does not fix is loose and lives here):
  DF_BUILDER            name of the new function (report text: build_token_document_frequencies)
  df_tables(ret)        how its return value is read: the report says "{field: {token: df}} plus
                        {field: N}" -> accepted as a 2-tuple (df, n); a dict {"df": .., "n"/"N": ..};
                        or an object with .df/.n attributes.  Edit here if the worker chose another shape.
  call_df_builder()     argument convention: tried (records, normalise) then (records).
  Level/score probes go through `_fit_match_model(records, pairs)` (returns (model, vectors)),
  `_score_pair(a, b, model)` and `_resolve_core(..., extra_candidate_pairs=...)`, i.e. only
  signatures that exist today and that P14 may extend only with optional args.  Where the DF
  table lives (model attr, normalise closure, extra arg) is therefore NOT assumed.
  ATOMIC_SNAPSHOT is a hard-coded snapshot of CURRENT behaviour (see test 2 docstring).
"""

from __future__ import annotations

import ast
import inspect
import logging
import math
import random
import re
import subprocess
from pathlib import Path

import pytest

from src.mcp_server.tools import fellegi_sunter as fs
from src.mcp_server.tools import resolve_entities as re_
from tests.part1 import fixtures_matching as fx

# ── adaptation points ─────────────────────────────────────────────────────────
DF_BUILDER = "build_token_document_frequencies"
RANK = {
    fs.LEVEL_NULL: -1,
    fs.LEVEL_DISAGREE: 0,
    fs.LEVEL_WEAK: 1,
    fs.LEVEL_STRONG: 2,
    fs.LEVEL_EXACT: 3,
}
FS_SRC = Path(fs.__file__)
RE_SRC = Path(re_.__file__)
ROOT = FS_SRC.parents[3]


def df_tables(ret):
    """Normalise the builder's return to (df: {field: {token: int}}, n: {field: int})."""
    if isinstance(ret, (tuple, list)) and len(ret) == 2 and all(isinstance(x, dict) for x in ret):
        return ret[0], ret[1]
    if isinstance(ret, dict):
        lower = {str(k).lower(): v for k, v in ret.items()}
        n = lower.get("n") or lower.get("totals") or lower.get("doc_counts")
        d = lower.get("df") or lower.get("document_frequencies")
        if d is not None and n is not None:
            return d, n
    for a, b in (("df", "n"), ("df", "N"), ("document_frequencies", "totals")):
        if hasattr(ret, a) and hasattr(ret, b):
            return getattr(ret, a), getattr(ret, b)
    raise AssertionError(
        f"cannot read {DF_BUILDER} result {type(ret).__name__}; report: '{{field: {{token: df}}}} "
        "plus {field: N}' - adapt df_tables() at the top of this file"
    )


def call_df_builder(records, normalise):
    fn = getattr(fs, DF_BUILDER, None)
    assert fn is not None, (
        f"fellegi_sunter.{DF_BUILDER} does not exist. OLD BEHAVIOUR: only the value-level "
        "build_term_frequencies, so a shared token like 'Group' is never down-weighted."
    )
    try:
        return fn(records, normalise)
    except TypeError:
        return fn(records)


def keep_space(v):  # the engine's _normalize joins words with '_', which would hide tokens
    return str(v).strip().lower()


# ── fixtures ──────────────────────────────────────────────────────────────────
def _pair(a, b):
    return (fx.rid(a), fx.rid(b), a, b)


def text_corpus(common: str = "Group", other: str = "Holdings", seed: int = 5, with_atomic=False):
    """~270 company records.  `common` is in 70 names (high df), `other` in 10, 'Ltd' in ~all.
    Probes:  A = Acme <common> Ltd  vs  Beta <common> Ltd   (share only the high-df token)
             B = Acme <common> Ltd  vs  Acme <other> Ltd    (share the RARE token 'Acme')
    65 exact-duplicate records give EM real matches; 60 random pairs give non-matches."""
    rng = random.Random(seed)
    toks = fx._tokens(rng, 400)
    recs: list[dict] = []

    def mk(name, **kw):
        r = fx._rec("src_a" if len(recs) % 2 else "src_b", str(len(recs)), name=name, **kw)
        recs.append(r)
        return r

    for t in toks[:70]:
        mk(f"{t} {common} Ltd")
    for t in toks[70:80]:
        mk(f"{t} {other} Ltd")
    for t in toks[80:130]:
        mk(f"{t} Ltd")
    for k in range(0, 130, 2):
        mk(recs[k]["name"])
    a = mk(f"Acme {common} Ltd")
    b = mk(f"Beta {common} Ltd")
    c = mk(f"Acme {other} Ltd")
    if with_atomic:
        for i, r in enumerate(recs):
            r["email"] = f"user{i}@mail.test"
    background = []
    for _ in range(60):
        x, y = rng.sample(range(130), 2)
        background.append((recs[x], recs[y]))
    return {"records": recs, "A": (a, b), "B": (a, c), "background": background}


def fit_pairs(fxt):
    pairs = [_pair(*p) for p in fxt["background"]]
    n_dupe = 65
    pairs += [_pair(fxt["records"][2 * k], fxt["records"][130 + k]) for k in range(n_dupe)]
    iA, iB = len(pairs), len(pairs) + 1
    pairs += [_pair(*fxt["A"]), _pair(*fxt["B"])]
    return pairs, iA, iB


def fit(fxt):
    pairs, iA, iB = fit_pairs(fxt)
    model, vectors = re_._fit_match_model(fxt["records"], pairs)
    assert model is not None and vectors is not None, (
        "match model did not fit (fell back to heuristic)"
    )
    return model, vectors, iA, iB


def core_confs(fxt):
    recs = fxt["records"]
    extra = [(fx.rid(x), fx.rid(y)) for x, y in fxt["background"]]
    extra += [(fx.rid(x), fx.rid(y)) for x, y in (fxt["A"], fxt["B"])]
    core = re_._resolve_core("company", recs, None, None, None, extra_candidate_pairs=extra)
    got = {}
    for x, y, c in core["scored_pairs"]:
        got[frozenset((x, y))] = c
    for x, y, _r1, _r2, c in core["borderline"]:
        got[frozenset((x, y))] = c
    keyA = frozenset((fx.rid(fxt["A"][0]), fx.rid(fxt["A"][1])))
    keyB = frozenset((fx.rid(fxt["B"][0]), fx.rid(fxt["B"][1])))
    assert keyA in got and keyB in got, "probe pairs were not scored by _resolve_core"
    return core, got[keyA], got[keyB]


# ── 1. the inversion the report names ─────────────────────────────────────────
def test_1a_level_grain_idf_weighted_level_B_stronger_than_A():
    """Level chosen for the text field: sharing the rare token (B) must outrank sharing only the
    high-df token (A).  Old code: A='weak', B='disagree' (plain string similarity) -> FAILS."""
    _m, vectors, iA, iB = fit(text_corpus())
    la, lb = vectors[iA]["name"][0], vectors[iB]["name"][0]
    assert RANK[lb] > RANK[la], (
        f"A(Group/Group)={la}  B(Acme/Acme)={lb}: IDF-weighted level should rank B above A"
    )


def test_1b_model_score_B_above_A_by_a_margin():
    model, vectors, iA, iB = fit(text_corpus())
    sa, sb = model.score(vectors[iA]), model.score(vectors[iB])
    assert sb > sa + 1.0, f"score bits A={sa:.2f} B={sb:.2f}; B must beat A by > 1 bit (old: A > B)"


def test_1c_score_pair_A_below_B_end_to_end_without_precomputed_vector():
    """_score_pair rebuilds the vector itself; the DF information must reach it (no hidden state
    that only _fit_match_model's own vectors see).  Also: same answer as with the fitted vector."""
    fxt = text_corpus()
    model, vectors, iA, iB = fit(fxt)
    a = re_._score_pair(*fxt["A"], model)
    b = re_._score_pair(*fxt["B"], model)
    assert a < b, (
        f"_score_pair A={a} B={b}: shared high-df 'Group' must score below shared rare 'Acme'"
    )
    pa = re_._score_pair(*fxt["A"], model, precomputed_vector=vectors[iA])
    pb = re_._score_pair(*fxt["B"], model, precomputed_vector=vectors[iB])
    assert (pa, pb) == (a, b), (
        "precomputed vector and rebuilt vector disagree (DF table not reproducible)"
    )


def test_1d_resolve_core_end_to_end():
    """The real path: blocking + fit + scoring in _resolve_core.  Old code: A=0.0007 > B=0.0001."""
    fxt = text_corpus()
    core, ca, cb = core_confs(fxt)
    assert core["pair_evidence"], "model did not fit inside _resolve_core"
    assert ca < cb, f"_resolve_core confidence A(Group)={ca}  B(Acme)={cb}; expected A < B"


def test_1e_mirror_vocabulary_flips_with_the_data_not_with_a_word():
    """A suffix list would hard-wire 'Group' as noise.  Swap the vocabulary: 'Holdings' is now the
    high-df token, 'Group' the rare one; the same inversion must hold for the swapped words."""
    fxt = text_corpus(common="Holdings", other="Group")
    _m, vectors, iA, iB = fit(fxt)
    assert RANK[vectors[iB]["name"][0]] > RANK[vectors[iA]["name"][0]]
    core, ca, cb = core_confs(fxt)
    assert ca < cb, f"mirror: A(Holdings)={ca} B(Acme)={cb}"


def test_1f_comparison_level_old_call_shape_still_works():
    """Existing callers use _comparison_level(v1, v2, normalise, field_name=...).  New params must be
    optional and the no-corpus call must still return a valid (level, value)."""
    params = list(inspect.signature(re_._comparison_level).parameters.values())
    assert [p.name for p in params[:4]] == ["v1", "v2", "normalise", "field_name"]
    assert all(p.default is not inspect.Parameter.empty for p in params[2:]), (
        "new params must be optional"
    )
    lvl, _v = re_._comparison_level("Acme Group Ltd", "Beta Group Ltd", field_name="name")
    assert lvl in RANK
    assert (
        re_._comparison_level("Acme Group Ltd", "acme group ltd.", field_name="name")[0]
        == fs.LEVEL_EXACT
    )


# ── 2. atomic fields unchanged ────────────────────────────────────────────────
def atomic_corpus():
    rng = random.Random(14)
    recs: list[dict] = []

    def mk(**kw):
        r = fx._rec("src_a" if len(recs) % 2 else "src_b", str(len(recs)), **kw)
        recs.append(r)
        return r

    for k in range(120):
        mk(
            email=f"user{k}@mail.test",
            phone=f"555-{1000 + k}",
            ticker="ZZZ" if k % 3 == 0 else f"T{k}",
        )
    pairs = []
    for k in range(0, 120, 3):
        d = mk(
            email=recs[k]["email"],
            phone=recs[k]["phone"] if k % 2 == 0 else None,
            ticker=recs[k]["ticker"],
        )
        pairs.append(_pair(recs[k], d))
    for _ in range(60):
        x, y = rng.sample(range(120), 2)
        pairs.append(_pair(recs[x], recs[y]))
    return recs, pairs


# Snapshot of the CURRENT (post-P13, pre-P14) working tree.  Probe index pairs into atomic_corpus().
ATOMIC_PROBES = {  # name: (i, j, levels, score_bits)
    "p1": (0, 120, {"email": "exact", "phone": "exact", "ticker": "exact"}, 4.872061),
    "p2": (1, 2, {"email": "strong", "phone": "weak", "ticker": "disagree"}, -24.460882),
    "p3": (3, 121, {"email": "exact", "ticker": "exact"}, -1.144418),
    "p4": (0, 3, {"email": "strong", "phone": "weak", "ticker": "exact"}, -16.729431),
    "p5": (5, 50, {"email": "strong", "phone": "weak", "ticker": "disagree"}, -24.460882),
}
ATOMIC_PRIOR = 0.00312487
ATOMIC_MU = {  # field -> (m, u) per level, rounded 5dp
    "email": ({"exact": 0.94964, "strong": 0.03023, "weak": 0.01343, "disagree": 0.00669},
              {"exact": 0.00344, "strong": 0.43762, "weak": 0.55331, "disagree": 0.00563}),
    "phone": ({"exact": 0.92479, "strong": 0.04499, "weak": 0.02022, "disagree": 0.01000},
              {"exact": 0.00317, "strong": 0.00026, "weak": 0.90360, "disagree": 0.09297}),
    "ticker": ({"exact": 0.94978, "strong": 0.03013, "weak": 0.01339, "disagree": 0.00670},
               {"exact": 0.24318, "strong": 0.00219, "weak": 0.00497, "disagree": 0.74967}),
}  # fmt: skip


def test_2_atomic_only_corpus_matches_current_snapshot():
    """Atomic fields (email/phone/ticker) go through the value-level path: levels, fitted m/u, prior and
    pair scores must equal today's.  Strict on purpose, because a text-grain change that tokenises
    'user1@mail.test' would inflate 'mail'/'test' and silently move all of these.  NOTE for the lead:
    if the worker deliberately adopts the (df-1)/(N-1) own-value correction (HANDOFF open escalation)
    p1/p3/m,u would move ~1 bit; that is a decision, not an accident - then re-snapshot explicitly."""
    recs, pairs = atomic_corpus()
    model, _v = re_._fit_match_model(recs, pairs)
    assert model is not None
    assert model.match_prior == pytest.approx(ATOMIC_PRIOR, abs=2e-8)
    for fld, (m, u) in ATOMIC_MU.items():
        fm = model.fields[fld]
        for lvl in m:
            assert fm.m[lvl] == pytest.approx(m[lvl], abs=2e-5), (fld, "m", lvl)
            assert fm.u[lvl] == pytest.approx(u[lvl], abs=2e-5), (fld, "u", lvl)
    for name, (i, j, levels, bits) in ATOMIC_PROBES.items():
        vec = re_._build_comparison_vector(recs[i], recs[j])
        assert {f: lv for f, (lv, _x) in vec.items()} == levels, name
        assert model.score(vec) == pytest.approx(bits, abs=2e-5), name


def test_2b_value_level_term_frequencies_unchanged_for_atomic_fields():
    """build_term_frequencies keeps its contract (value df / non-null N) and the fitted model carries it."""
    recs, pairs = atomic_corpus()
    tf = fs.build_term_frequencies(recs, re_._normalize)
    emails = [re_._normalize(r["email"]) for r in recs]
    n = len(emails)
    assert tf["email"][re_._normalize("user7@mail.test")] == pytest.approx(
        emails.count(re_._normalize("user7@mail.test")) / n
    )
    tickers = [re_._normalize(r["ticker"]) for r in recs]
    assert tf["ticker"]["zzz"] == pytest.approx(tickers.count("zzz") / len(tickers))
    model, _v = re_._fit_match_model(recs, pairs)
    assert model.fields["ticker"].term_frequency["zzz"] == pytest.approx(tf["ticker"]["zzz"])


def test_2c_atomic_common_exact_still_weighs_less_than_rare_exact_in_a_mixed_corpus():
    """Mixed text+atomic corpus: the exact-email weight is still log2(m/(df/N)) value-level."""
    fxt = text_corpus(with_atomic=True)
    model, _v, _a, _b = fit(fxt)
    fm = model.fields["email"]
    key = re_._normalize("user7@mail.test")
    assert fm.term_frequency[key] > 0
    expect = math.log2(max(fm.m["exact"], 1e-6) / fm.term_frequency[key])
    assert fm.weight("exact", key) == pytest.approx(expect, abs=1e-9)


# ── 3. NULL input does not crash ──────────────────────────────────────────────
def test_3_null_text_values_everywhere(caplog):
    """_fit_match_model and _score_pair SWALLOW exceptions and fall back to the heuristic, so a crash in
    a NULL-unsafe tokenizer / SoftTfIdf would be invisible: assert the model fitted and nothing warned."""
    fxt = text_corpus()
    recs = fxt["records"]
    nulls = [
        fx._rec("src_a", "n1", name=None),
        fx._rec("src_b", "n2", name=""),
        fx._rec("src_a", "n3", name="   "),
        fx._rec("src_b", "n4"),  # field absent
        fx._rec("src_a", "n5", name=12345),  # non-string
        fx._rec("src_b", "n6", name="Acme Group Ltd"),
    ]
    recs = recs + nulls
    fxt["records"] = recs
    pairs, iA, iB = fit_pairs(fxt)
    null_pairs = [_pair(nulls[i], nulls[j]) for i in range(6) for j in range(i + 1, 6)]
    with caplog.at_level(logging.WARNING):
        model, vectors = re_._fit_match_model(recs, pairs + null_pairs)
    assert model is not None, "fit fell back to heuristic (swallowed an exception?)"
    assert not [r for r in caplog.records if "Could not fit" in r.getMessage()], "fit warned"
    for p in null_pairs:
        vec = re_._build_comparison_vector(p[2], p[3])  # raises if unguarded
        c = re_._score_pair(p[2], p[3], model)
        assert 0.0 <= c <= 1.0 and not math.isnan(c)
        p_model = model.probability(vec)  # direct: not swallowed
        assert 0.0 <= p_model <= 1.0
    for a, b in [(None, "x y z"), ("x y z", None), (None, None), ("", "acme"), ("  ", "  ")]:
        lvl, val = re_._comparison_level(a, b, field_name="name")
        assert lvl == fs.LEVEL_NULL and val is None, (a, b, lvl)
    # NULL contributes no evidence either way
    nul_vec = re_._build_comparison_vector(nulls[0], nulls[5])
    assert model.score(nul_vec) == pytest.approx(model.prior_weight)


def test_3b_df_builder_tolerates_null_empty_nonstring():
    recs = [
        {"name": None},
        {"name": ""},
        {"name": "  "},
        {"name": 42},
        {"x": 1},
        {"name": "Acme Group"},
    ]
    df, n = df_tables(call_df_builder(recs, keep_space))
    assert n["name"] == 2  # 42 and 'Acme Group'; NULL/blank/absent excluded from N
    assert df["name"]["acme"] == 1


# ── 4. build_token_document_frequencies beside the value-level function; log2 everywhere ──
DF_RECS = [
    {"_origin_id": "s", "name": "Acme Group Ltd", "email": "a@x.test"},
    {"_origin_id": "s", "name": "Beta Group Ltd", "email": "b@x.test"},
    {"_origin_id": "s", "name": "Gamma Group", "email": None},
    {"_origin_id": "s", "name": "Delta Ltd Ltd", "email": "a@x.test"},
    {"_origin_id": "s", "name": None, "email": "c@x.test"},
]


def test_4a_builder_exists_beside_value_level_function():
    assert callable(fs.build_term_frequencies)
    fn = getattr(fs, DF_BUILDER, None)
    assert fn is not None, f"{DF_BUILDER} missing (OLD BEHAVIOUR)"
    assert fn is not fs.build_term_frequencies
    # value-level function unchanged: whole-value frequency
    tf = fs.build_term_frequencies(DF_RECS, keep_space)
    assert tf["name"]["acme group ltd"] == pytest.approx(1 / 4)


def test_4b_df_counts_documents_not_occurrences_and_n_excludes_null():
    df, n = df_tables(call_df_builder(DF_RECS, keep_space))
    assert n["name"] == 4, "N = non-NULL names (4); the NULL name record is not a document"
    d = {k.lower(): v for k, v in df["name"].items()}
    assert d["group"] == 3 and d["ltd"] == 3, (
        "df counts a record once even if the token repeats ('Delta Ltd Ltd')"
    )
    assert d["acme"] == 1 and d["gamma"] == 1
    assert not any(k.startswith("_") for k in df), "private/_origin fields are not compared fields"


def test_4c_df_builder_is_pure_no_module_state_leak():
    other = [{"name": "Zed Zed Zed"}, {"name": "Zed Corp"}]
    first = df_tables(call_df_builder(DF_RECS, keep_space))
    df_tables(call_df_builder(other, keep_space))
    again = df_tables(call_df_builder(DF_RECS, keep_space))
    assert first == again, (
        "second corpus leaked into the first (module-level mutable state forbidden)"
    )
    assert "zed" not in {k.lower() for k in again[0]["name"]}


def _owned_trees():
    yield "fellegi_sunter.py", ast.parse(FS_SRC.read_text(encoding="utf-8"))
    tree = ast.parse(RE_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and _is_owned_re_function(node.name):
            yield f"resolve_entities.py:{node.name}", node


def _baseline_function_names() -> set[str]:
    """Function names in resolve_entities.py at HEAD (so pre-existing helpers such as _tokens_match
    are not 'owned'); empty if git is unavailable."""
    try:
        src = subprocess.run(
            ["git", "show", "HEAD:src/mcp_server/tools/resolve_entities.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout
        return {n.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
    except Exception:
        return set()


_BASELINE_NAMES = _baseline_function_names()


def _is_owned_re_function(name: str) -> bool:
    """The two named owned functions, plus NEW helpers whose name says token/idf/df."""
    if name in {"_comparison_level", "_fit_match_model"}:
        return True
    return name not in _BASELINE_NAMES and bool(
        re.search(r"token|idf|document_freq|(^|_)df(_|$)", name)
    )


def test_4d_log2_everywhere_in_owned_code():
    """Absolute thresholds (0.95/0.70) mean the log base is not free: no natural log / log10 / log1p /
    math.e / numpy log in owned code; math.log(x, 2) is acceptable.  Baseline hits: none."""
    bad = []
    for where, tree in _owned_trees():
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name in {"log10", "log1p", "ln"} or (name == "log" and len(n.args) == 1):
                    bad.append((where, n.lineno, name))
                if (
                    name == "log"
                    and len(n.args) == 2
                    and not (isinstance(n.args[1], ast.Constant) and n.args[1].value == 2)
                ):
                    bad.append((where, n.lineno, "log base != 2"))
            if (
                isinstance(n, ast.Attribute)
                and n.attr == "e"
                and getattr(n.value, "id", "") == "math"
            ):
                bad.append((where, n.lineno, "math.e"))
    assert not bad, f"non-log2 logarithms in owned code: {bad}"


def test_4e_idf_is_in_bits_a_rarer_agreement_adds_exactly_log2_of_the_df_ratio():
    """Behavioural log2 check on the value-level path the report calls 'already IDF, in bits': two exact
    agreements whose df differ by 4x differ by exactly 2 bits (not 1.386 = ln 4, not 0.602 = log10 4)."""
    fm = fs.FieldModel(name="f", term_frequency={"rare": 0.01, "common": 0.04})
    assert fm.weight("exact", "rare") - fm.weight("exact", "common") == pytest.approx(2.0, abs=1e-9)


# ── 5. EXACT / STRONG / WEAK / DISAGREE kept (D-14) ───────────────────────────
def test_5a_level_structure_kept():
    assert fs._LEVELS == (fs.LEVEL_EXACT, fs.LEVEL_STRONG, fs.LEVEL_WEAK, fs.LEVEL_DISAGREE)
    assert (fs.LEVEL_EXACT, fs.LEVEL_STRONG, fs.LEVEL_WEAK, fs.LEVEL_DISAGREE, fs.LEVEL_NULL) == (
        "exact", "strong", "weak", "disagree", "null",
    )  # fmt: skip


def test_5b_fitted_model_and_emitted_levels_use_only_the_four_levels():
    fxt = text_corpus()
    model, vectors, _a, _b = fit(fxt)
    for f in model.fields.values():
        assert set(f.m) == set(fs._LEVELS) and set(f.u) == set(fs._LEVELS), f.name
    seen = {lv for v in vectors for lv, _x in v.values()}
    assert seen <= set(RANK), seen
    # D-14 changes one thing: the level of a text field.  All four are still reachable on this corpus.
    assert {fs.LEVEL_EXACT, fs.LEVEL_DISAGREE} <= seen


def test_5c_identical_text_is_still_exact():
    fxt = text_corpus()
    _m, _v, _a, _b = fit(fxt)
    recs = fxt["records"]
    dupe = re_._build_comparison_vector(recs[0], recs[130])
    assert dupe["name"][0] == fs.LEVEL_EXACT


# ── 6. section 2.4: no fitted constants / no suffix list ──────────────────────
LITERALS = (
    "France", "US", "SAS", "Berhad", "Global", "Group", "Holdings", "Ltd", "Inc", "SA", "GmbH",
    "Corp", "LLC", "PLC", "Limited", "companyname", "main_entity_id", "company_relation", "staff",
    "org_title",
)  # fmt: skip
# BASELINE (parent working tree, measured when this file was written): 0 string-literal hits in
# fellegi_sunter.py, 0 in resolve_entities.py:_comparison_level, 0 in :_fit_match_model.
# (Whole resolve_entities.py has hits elsewhere - the corporate-suffix handling that P6 owns - and is
# NOT scanned here.)
BASELINE_LITERAL_HITS: set[tuple[str, str]] = set()
BASELINE_NUMERIC_IN_FUNCS = {0, 1, 2, 3, 0.7, 0.9}  # _comparison_level had 0.7/0.9/3; trivial 0/1/2
BASELINE_FS_NUMERIC_CONSTS = {"_EPS", "_PRIOR_WEIGHT", "_PRIOR_MATCH_RATE", "SAMPLE_PAIRS"}


def _docstring_ids(tree) -> set[int]:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(n, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_6a_no_corpus_or_suffix_literal_in_owned_code():
    pat = re.compile(
        r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, LITERALS)) + r")(?![A-Za-z0-9_])", re.I
    )
    hits = set()
    for where, tree in _owned_trees():
        doc = _docstring_ids(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc:
                for m in pat.finditer(n.value):
                    hits.add((where, m.group(1)))
    new = sorted(hits - BASELINE_LITERAL_HITS)
    assert not new, f"corpus/suffix literals as code strings in owned code: {new}"


def test_6b_no_stop_word_or_suffix_collection_in_owned_code():
    """A list/set/tuple/frozenset of >=3 short lowercase words is a stop-word/suffix list however it is
    spelled.  Level names (exact/strong/...) are exempt.  Baseline: none."""
    allowed = {"exact", "strong", "weak", "disagree", "null"}
    bad = []
    for where, tree in _owned_trees():
        for n in ast.walk(tree):
            elts = n.elts if isinstance(n, (ast.List, ast.Tuple, ast.Set)) else None
            if (
                elts
                and len(elts) >= 3
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts)
            ):
                words = {e.value.lower() for e in elts}
                if all(re.fullmatch(r"[a-z.]{1,12}", w) for w in words) and not words <= allowed:
                    bad.append((where, n.lineno, sorted(words)[:6]))
    assert not bad, f"word collections that look like stop-word/suffix lists: {bad}"


def test_6c_no_new_numeric_constants_a_corpus_count_would_not_produce():
    """Any NEW numeric literal in the owned functions (e.g. an 'IDF threshold = 2.7' or a 'df > 40
    means common' cut-off) is a fitted constant unless justified; the existing 0.70/0.90 similarity
    cut-offs and trivial 0/1/2 are baseline.  New module-level numerics only if named *SAMPLE*/*SEED*."""
    new = []
    for where, tree in _owned_trees():
        if where.startswith("fellegi_sunter"):
            for node in tree.body:
                if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)):
                    continue
                if type(node.value.value) not in (int, float):
                    continue
                for t in node.targets:
                    ok = t.id in BASELINE_FS_NUMERIC_CONSTS or re.search(r"SAMPLE|SEED", t.id, re.I)
                    if isinstance(t, ast.Name) and not ok:
                        new.append((where, t.id, node.value.value))
            continue
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Constant)
                and type(n.value) in (int, float)
                and n.value not in BASELINE_NUMERIC_IN_FUNCS
            ):
                new.append((where, n.lineno, n.value))
    assert not new, f"new numeric constants in owned code (derive from data or justify): {new}"


def test_6d_report_comment_mentions_nonfatal():
    """Informational: corpus words in comments/docstrings are allowed (an example is not a constant).
    Prints them so the verdict can record them."""
    pat = re.compile(r"\b(France|Berhad|Global|Group|Holdings|GmbH|SAS)\b")
    lines = [
        (p.name, i + 1)
        for p in (FS_SRC,)
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines())
        if pat.search(ln)
    ]
    print("comment/docstring corpus-word mentions in fellegi_sunter.py:", lines)


def test_6e_arch_lint_still_clean():
    """Layering/mutable-state guard (plan 2.5) on the tools package."""
    r = subprocess.run(
        [".venv/bin/python", "scripts/lint_arch.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
