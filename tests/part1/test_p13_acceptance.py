"""P13 acceptance (derived from the report's Evidence + Fix text only; no implementation seen).

Report: "`u` is estimated on the post-blocking population". Fix: `u` from a random
sample of the UNBLOCKED cross product; `m` from EM on blocked candidates, unchanged;
token-level exact agreement uses df/N directly; the two `u` paths coexist by design
and a comment forbids "unifying" them; a value with no sampled agreement floors.
HANDOFF flag 2: `estimate_model` takes the sampled random-pair comparison vectors as an
OPTIONAL argument (the call site in resolve_entities.py is the lead's, not tested here).

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p13_acceptance.py -q -s
The negative cases (1, 1b, 2b, 3, 4) FAIL on the old code (no sampled-u input).

ADAPTATION POINTS (one place each):
  SAMPLE_ARG / env P13_SAMPLE_ARG : name of the new optional `estimate_model` parameter.
        Default: auto-detect = the one new parameter (beyond the four existing ones)
        whose name matches SAMPLE_ARG_RE.
  SAMPLER_RE / env P13_SAMPLER    : name of an optional seeded sampler in fellegi_sunter.py
        (location is loosely specified; if absent, determinism is tested on estimate_model).
"""

from __future__ import annotations

import ast
import inspect
import math
import os
import random
import re
from pathlib import Path

import pytest

from src.mcp_server.tools import fellegi_sunter as fs
from tests.part1 import p13_fixtures as fx

SRC = Path(fs.__file__)
OLD_PARAMS = {"comparison_vectors", "term_frequencies", "unique_id_fields", "iterations"}
SAMPLE_ARG_RE = re.compile(r"sampl|random|unblocked|negative|cross", re.I)
SAMPLER_RE = re.compile(r"^(draw_|sample_|_sample|_draw|random_pair|sample)", re.I)


# ── helpers ───────────────────────────────────────────────────────────────────
def sample_arg() -> str:
    forced = os.environ.get("P13_SAMPLE_ARG")
    params = set(inspect.signature(fs.estimate_model).parameters)
    if forced:
        assert forced in params, f"P13_SAMPLE_ARG={forced!r} not a parameter of estimate_model"
        return forced
    new = sorted(p for p in params - OLD_PARAMS if SAMPLE_ARG_RE.search(p))
    assert len(new) == 1, (
        "estimate_model has no optional sampled-u argument (found new params matching "
        f"{SAMPLE_ARG_RE.pattern!r}: {new}; all params: {sorted(params)}). "
        "OLD BEHAVIOUR: u is estimated only from blocked candidates."
    )
    return new[0]


# Lead adaptation (round 2): sampled u requires the size of the population it was
# drawn from, so the match prior is rescaled to that population (a model mixing a
# random-pair u with a blocked-pair prior over-scored non-matches; see P13.verdict.md).
# These checks are about m/u, so any population larger than the candidates works.
POPULATION_PAIRS = 1000 * 999 // 2


def _population_kw():
    params = inspect.signature(fs.estimate_model).parameters
    return {"population_pairs": POPULATION_PAIRS} if "population_pairs" in params else {}


def fit(blocked, sampled=None, **kw):
    if sampled is None:
        return fs.estimate_model(blocked, {}, **kw)
    return fs.estimate_model(blocked, {}, **{sample_arg(): sampled}, **_population_kw(), **kw)


def snapshot(model):
    return {
        "prior": round(model.match_prior, 9),
        "fields": {
            n: (
                {k: round(v, 9) for k, v in sorted(f.m.items())},
                {k: round(v, 9) for k, v in sorted(f.u.items())},
            )
            for n, f in sorted(model.fields.items())
        },
    }


# ── 1. sampled u < blocked-EM u; log2(m/u) larger ─────────────────────────────
def test_1_sampled_u_is_lower_and_weight_larger_on_blocked_lookalikes():
    blocked = fx.blocked_vectors()
    base = fit(blocked)
    new = fit(blocked, fx.sampled_vectors())
    for f in ("zip", "name"):
        u0, u1 = base.fields[f].u["exact"], new.fields[f].u["exact"]
        assert u1 < u0, f"{f}: sampled u(exact)={u1:.4f} not < blocked-EM u={u0:.4f}"
    # the field blocking made look-alike on: the report's compressed log2(m/u)
    w0, w1 = base.fields["zip"].weight("exact"), new.fields["zip"].weight("exact")
    assert w1 > w0 + 1.0, f"zip log2(m/u): sampled {w1:.2f} bits vs blocked-EM {w0:.2f} bits"
    # and the discriminating field does not lose weight
    assert new.fields["name"].weight("exact") >= base.fields["name"].weight("exact")


def test_1b_sampled_u_tracks_the_random_pair_rate_not_a_constant():
    """u must be computed from the sample: change the sample's agreement rate and u moves.
    A hard-coded u (or one ignoring the argument) fails."""
    blocked = fx.blocked_vectors()
    lo = fit(blocked, fx.vectors(random.Random(1), 4000, fx.RANDOM_PAIRS))
    hi_spec = {
        **fx.RANDOM_PAIRS,
        "zip": {"exact": 0.40, "strong": 0.02, "weak": 0.04, "disagree": 0.54},
    }
    hi = fit(blocked, fx.vectors(random.Random(1), 4000, hi_spec))
    assert hi.fields["zip"].u["exact"] > lo.fields["zip"].u["exact"] + 0.15
    # ~order of magnitude: ~4% random agreement should give u(exact) well under the blocked ~75%
    assert lo.fields["zip"].u["exact"] < 0.15


def test_1c_m_still_estimated_on_blocked_candidates():
    new = fit(fx.blocked_vectors(), fx.sampled_vectors())
    for f in ("zip", "name"):
        m = new.fields[f].m
        assert math.isclose(sum(m.values()), 1.0, abs_tol=1e-6)
        assert m["exact"] > new.fields[f].u["exact"]
    # ranking sanity: agreeing on the discriminating field still beats disagreeing on it
    agree = {"zip": ("exact", None), "name": ("exact", None)}
    differ = {"zip": ("exact", None), "name": ("disagree", None)}
    assert new.score(agree) > new.score(differ)


# ── 2. determinism ────────────────────────────────────────────────────────────
def test_2_estimate_model_deterministic_given_same_inputs():
    blocked, sampled = fx.blocked_vectors(), fx.sampled_vectors()
    assert snapshot(fit(blocked, sampled)) == snapshot(fit(blocked, sampled))


def test_2b_seeded_sampler_if_present_is_deterministic():
    """Sampler location is loosely specified. If fellegi_sunter.py exposes one with a
    seed parameter, the same seed must give the same sample and a different seed a
    different one; otherwise (sampler lives at the call site) this is a documented skip."""
    forced = os.environ.get("P13_SAMPLER")
    names = [forced] if forced else [n for n in dir(fs) if SAMPLER_RE.search(n)]
    fns = [getattr(fs, n) for n in names if callable(getattr(fs, n, None))]
    fns = [f for f in fns if "seed" in inspect.signature(f).parameters]
    if not fns:
        pytest.skip("no seeded sampler in fellegi_sunter.py (lives at the call site; lead's half)")
    fn = fns[0]
    sig = inspect.signature(fn)
    records = [{"zip": f"z{i % 9}", "name": f"n{i}"} for i in range(200)]
    kwargs = {}
    for p in sig.parameters.values():
        if p.name == "seed" or p.default is not inspect.Parameter.empty:
            continue
        if re.search(r"record|rows|items", p.name):
            kwargs[p.name] = records
        elif re.search(r"n$|count|size|k$|num", p.name):
            kwargs[p.name] = 50
        else:
            pytest.skip(f"cannot auto-call sampler {fn.__name__}{sig}")
    a, b, c = fn(seed=1, **kwargs), fn(seed=1, **kwargs), fn(seed=2, **kwargs)
    assert repr(a) == repr(b)
    assert repr(a) != repr(c)


# ── 3. floor, no ZeroDivision / inf / NaN ─────────────────────────────────────
def _all_finite(model):
    for fm in model.fields.values():
        for lvl in ("exact", "strong", "weak", "disagree", "null"):
            w = fm.weight(lvl, None)
            assert math.isfinite(w), f"{fm.name}.{lvl} weight {w}"
            assert abs(w) < 64, f"{fm.name}.{lvl} weight {w} is not floored"
        for lvl, p in fm.u.items():
            assert math.isfinite(p) and 0.0 < p <= 1.0, f"{fm.name}.u[{lvl}]={p}"
        for lvl, p in fm.m.items():
            assert math.isfinite(p) and 0.0 < p <= 1.0, f"{fm.name}.m[{lvl}]={p}"


def test_3_level_with_zero_sampled_agreement_floors_u():
    blocked = fx.blocked_vectors()
    # 3000 random pairs in which `name` NEVER agrees exactly (rare value, none in sample)
    spec = {**fx.RANDOM_PAIRS, "name": {"weak": 0.04, "disagree": 0.96}}
    sampled = fx.vectors(random.Random(3), 3000, spec)
    assert not any(v["name"][0] == "exact" for v in sampled)
    model = fit(blocked, sampled)
    assert 0.0 < model.fields["name"].u["exact"] < 0.01
    _all_finite(model)


def test_3b_degenerate_samples_do_not_crash():
    blocked = fx.blocked_vectors()
    all_null = [{"zip": ("null", None), "name": ("null", None)} for _ in range(50)]
    all_disagree = [{"zip": ("disagree", None), "name": ("disagree", None)} for _ in range(50)]
    all_exact = [{"zip": ("exact", None), "name": ("exact", None)} for _ in range(50)]
    for sample in ([], all_null, all_disagree, all_exact):
        _all_finite(fit(blocked, sample))


def test_3c_sampled_field_absent_from_blocked_candidates_does_not_crash():
    """A field present only in the random sample (blocked vectors never carried it)."""
    blocked = fx.blocked_vectors()
    extra = [
        {**v, "phone": ("exact" if i % 50 == 0 else "disagree", None)}
        for i, v in enumerate(fx.sampled_vectors(n=500))
    ]
    model = fit(blocked, extra)
    assert "zip" in model.fields
    _all_finite(model)


def test_3d_sampled_u_computable_with_no_blocked_pairs_does_not_crash():
    fit([], fx.sampled_vectors(n=200))


# ── 4. token-level exact agreement uses df/N directly ─────────────────────────
def test_4_build_term_frequencies_is_df_over_n():
    recs = [{"name": "acme"}] * 3 + [{"name": "beta"}] * 6 + [{"name": "rare"}, {"name": None}]
    tf = fs.build_term_frequencies(recs, lambda v: str(v).strip().lower())
    n = 10  # non-null values of the field; NULL does not count toward N
    assert tf["name"]["acme"] == pytest.approx(3 / n)
    assert tf["name"]["rare"] == pytest.approx(1 / n)
    assert sum(tf["name"].values()) == pytest.approx(1.0)


def test_4b_exact_weight_for_a_term_uses_df_over_n_not_the_sampled_u():
    """With sampled u in play, an exact agreement on a value with known df/N must still
    weigh log2(m / (df/N)); sampled u(exact) (~4%) must not replace it."""
    recs = [{"zip": "z1"}] * 30 + [{"zip": f"z{i}"} for i in range(2, 72)]  # df/N = 30/100
    tf = fs.build_term_frequencies(recs, lambda v: str(v).lower())
    assert tf["zip"]["z1"] == pytest.approx(0.30)
    model = fs.estimate_model(
        fx.blocked_vectors(), tf, **{sample_arg(): fx.sampled_vectors()}, **_population_kw()
    )
    fm = model.fields["zip"]
    assert fm.u["exact"] < 0.15  # sampled u really is far from df/N here
    assert fm.weight("exact", "z1") == pytest.approx(math.log2(fm.m["exact"] / 0.30))
    # a unique value (df/N = 1/100) outweighs the common one
    assert fm.weight("exact", "z5") == pytest.approx(math.log2(fm.m["exact"] / 0.01))


# ── 5. regression: no new argument => exactly today's output ──────────────────
SNAP_SMALL = {  # captured from the parent commit with estimate_model(small, {"a": {"x": 0.3}})
    "prior": 0.224948099,
    "fields": {
        "a": (
            {
                "disagree": 0.018039043,
                "exact": 0.909730404,
                "strong": 0.050005767,
                "weak": 0.022224786,
            },
            {
                "disagree": 0.792927767,
                "exact": 0.166449868,
                "strong": 0.009374392,
                "weak": 0.031247973,
            },
        ),
        "b": (
            {
                "disagree": 0.011117281,
                "exact": 0.768933557,
                "strong": 0.197722875,
                "weak": 0.022226287,
            },
            {
                "disagree": 0.542309847,
                "exact": 0.164785916,
                "strong": 0.259009713,
                "weak": 0.033894523,
            },
        ),
        "c": (
            {
                "disagree": 0.01999742,
                "exact": 0.849890366,
                "strong": 0.089988392,
                "weak": 0.040123822,
            },
            {
                "disagree": 0.425027414,
                "exact": 0.010000645,
                "strong": 0.015000968,
                "weak": 0.549970974,
            },
        ),
    },
}
SNAP_BLOCKED = {  # estimate_model(fx.blocked_vectors(), {}) on the parent commit
    "prior": 0.287586318,
    "name": (0.757776353, 0.016183304, 5.549194067367386),
    "zip": (0.915254812, 0.749058929, 0.2890942325837302),
}


def _approx_snapshot(snap, expected):
    assert snap["prior"] == pytest.approx(expected["prior"], abs=2e-9)
    assert set(snap["fields"]) == set(expected["fields"])
    for n, (m, u) in expected["fields"].items():
        assert snap["fields"][n][0] == pytest.approx(m, abs=2e-9)
        assert snap["fields"][n][1] == pytest.approx(u, abs=2e-9)


def test_5_without_new_argument_output_is_todays():
    model = fs.estimate_model(fx.small_snapshot_vectors(), {"a": {"x": 0.3}}, None)
    _approx_snapshot(snapshot(model), SNAP_SMALL)
    # positional signature + defaults unchanged for existing callers
    params = list(inspect.signature(fs.estimate_model).parameters.values())
    assert [p.name for p in params[:4]] == [
        "comparison_vectors",
        "term_frequencies",
        "unique_id_fields",
        "iterations",
    ]
    assert params[3].default == 8
    for p in params[4:]:
        assert p.default is not inspect.Parameter.empty, f"{p.name} must be optional"


def test_5b_blocked_snapshot_and_none_equals_omitted():
    blocked = fx.blocked_vectors()
    model = fs.estimate_model(blocked, {})
    assert model.match_prior == pytest.approx(SNAP_BLOCKED["prior"], abs=2e-9)
    for f in ("name", "zip"):
        m_ex, u_ex, w = SNAP_BLOCKED[f]
        assert model.fields[f].m["exact"] == pytest.approx(m_ex, abs=2e-9)
        assert model.fields[f].u["exact"] == pytest.approx(u_ex, abs=2e-9)
        assert model.fields[f].weight("exact") == pytest.approx(w, abs=1e-6)
    if set(inspect.signature(fs.estimate_model).parameters) - OLD_PARAMS:
        assert snapshot(fit(blocked, None)) == snapshot(
            fs.estimate_model(blocked, {}, **{sample_arg(): None})
        )


# ── 6. a comment forbids unifying the two u paths ─────────────────────────────
def _comment_blocks(text: str) -> list[str]:
    blocks, cur = [], []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            cur.append(s.lstrip("# ").strip())
        elif cur:
            blocks.append(" ".join(cur))
            cur = []
    if cur:
        blocks.append(" ".join(cur))
    return blocks


def test_6_comment_forbids_unifying_the_two_u_paths():
    hits = [
        b
        for b in _comment_blocks(SRC.read_text(encoding="utf-8"))
        if re.search(r"unif", b, re.I)
        and re.search(r"\b(do not|don't|never|must not|not)\b", b, re.I)
        and re.search(r"sampl", b, re.I)
        and re.search(r"df\s*/\s*N|token", b, re.I)
    ]
    assert hits, "no # comment says not to unify the sampled-u and df/N (token) u paths"


# ── 7. section 2.4: no fitted constants ───────────────────────────────────────
LITERALS = (
    "France", "US", "SAS", "Berhad", "Global", "Group", "Holdings",
    "companyname", "main_entity_id", "company_relation", "staff", "org_title",
)  # fmt: skip
BASELINE_HITS: set[tuple[str, str]] = set()  # fellegi_sunter.py has NO hits today
BASELINE_NUMERIC = {"_EPS", "_PRIOR_WEIGHT", "_PRIOR_MATCH_RATE"}
# A named sample-size / seed constant is sanctioned ("a few thousand pairs, seeded"),
# but must be named as such; the attacker still asks: would a count produce it?
SANCTIONED_NUMERIC = re.compile(r"SAMPLE|SEED", re.I)


def _hits() -> set[tuple[str, str]]:
    out = set()
    for line in SRC.read_text(encoding="utf-8").splitlines():
        for w in LITERALS:
            if re.search(rf"(?<![A-Za-z0-9_]){w}(?![A-Za-z0-9_])", line):
                out.add((w, line.strip()))
    return out


def test_7_no_new_corpus_literals():
    new = sorted(_hits() - BASELINE_HITS)
    assert not new, f"new corpus literals in fellegi_sunter.py: {new}"


def test_7b_new_numeric_constants_only_named_sample_knobs():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:
        v = node.value if isinstance(node, ast.Assign) else None
        if isinstance(v, ast.Constant) and type(v.value) in (int, float):
            found |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    new = sorted(n for n in found - BASELINE_NUMERIC if not SANCTIONED_NUMERIC.search(n))
    assert not new, f"new module-level numeric constants (derive from data or justify): {new}"


def test_7c_no_value_specific_branching_on_u():
    """u-path code must not special-case string literals (a value list would be a fitted
    constant). Compare-to-string-literal inside estimate_model is limited to the level names."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "estimate_model"
    )
    allowed = {"exact", "strong", "weak", "disagree", "null"}
    lits = {
        c.value
        for c in ast.walk(fn)
        if isinstance(c, ast.Constant) and isinstance(c.value, str) and len(c.value) < 40
    }
    # docstrings/log messages are longer or contain spaces/format chars; keep identifier-like only
    ident = {s for s in lits if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s)} - allowed
    ident -= {"name", "level", "value"}
    assert not ident, f"identifier-like string literals inside estimate_model: {sorted(ident)}"
