"""D1 acceptance (LOCAL_VERIFY_REPORT D1): soft text similarity must not be quadratic in tokens.

Derived from the report's Evidence / Root cause / Fix text only, before any fix exists.
Defect: ``fellegi_sunter.idf_soft_similarity`` takes ``max(token_sim(t, u) for u in dst)`` for
every ``t`` -- |A|*|B| ``token_sim`` calls per direction (42,960 for the fixture pair, 120 + 179
tokens) -- for every candidate pair and every sampled u-pair.  Fix shape: keep the soft
Monge-Elkan for short name-like values, use O(|A|+|B|) IDF-weighted exact-token overlap (or an
n-gram candidate restriction) for long text.

Targets-the-defect tests (fail on the old code): test_cost_*, test_comparator_*, test_pipeline_cost_*.
Regression guards (pass on the old code and must keep passing): test_short_*, test_long_*,
test_pipeline_true_duplicates_*, test_no_corpus_literals.

Thresholds (all derived from the report, not from a fix):
* per call ``token_sim`` count <= 20 * (|A| + |B|)  -- the existing repro's own bound; the
  quadratic code needs 2*|A|*|B| (>= 100 * (|A|+|B|) once both sides pass ~100 tokens).
* doubling both sides: calls(2k) <= 2.6 * calls(k) + 20  (linear is 2.0, quadratic is 4.0).
* short text (<= 8 tokens a side): equal to today's value, abs tol 1e-9.
* pipeline: total uncached difflib ratios in one ``_resolve_core`` <= 2 * (R-1) * total_tokens,
  i.e. on average at most 2 per token per other record (sum over ALL record pairs of
  (|A|+|B|) is (R-1) * total_tokens, so this is 2 per token per pair at the worst).
"""

from __future__ import annotations

import ast
import difflib
import itertools
import json
import math
import random
import re
import time
from pathlib import Path

import pytest

from src.mcp_server.tools import resolve_entities as re_mod
from src.mcp_server.tools.fellegi_sunter import (
    build_token_document_frequencies,
    idf_soft_similarity,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "live_repros" / "p14_long_text_pair.json"

PER_TOKEN_CALLS = 20  # the repro's own bound: calls <= 20 * (|A| + |B|)
DOUBLING_FACTOR = 2.6  # linear = 2.0, quadratic = 4.0
_TOK = re.compile(r"[a-z0-9]+")


def _sim(a: str, b: str) -> float:
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


class _Counting:
    """token_sim with a call counter (real difflib similarity, so n-gram style fixes see strings)."""

    def __init__(self, fn=_sim):
        self.fn, self.calls = fn, 0

    def __call__(self, a: str, b: str) -> float:
        self.calls += 1
        return self.fn(a, b)


# ---------------------------------------------------------------- reference: today's algorithm
def _reference(tokens1, tokens2, df, n, token_sim):
    """Frozen copy of today's ``idf_soft_similarity`` (full max over the other side, symmetric
    Monge-Elkan, D5 containment -> weaker direction).  The oracle for short text."""
    if n <= 0 or not tokens1 or not tokens2:
        return None

    def w(t):
        return math.log2(n / min(max(df.get(t, 1), 1), n))

    def directed(src, dst):
        total = math.fsum(w(t) for t in src)
        if total == 0:
            return None
        return math.fsum(w(t) * max(token_sim(t, u) for u in dst) for t in src) / total

    d1, d2 = directed(tokens1, tokens2), directed(tokens2, tokens1)
    if d1 is None or d2 is None:
        return None
    if tokens1 <= tokens2 or tokens2 <= tokens1:
        return min(d1, d2)
    return (d1 + d2) / 2


# ------------------------------------------------------------------------- data builders
_SYL = ["ba", "ko", "ri", "tu", "sen", "mo", "la", "vex", "dor", "pi", "nal", "qua", "zen", "fi"]


def _vocab(rng: random.Random, size: int) -> list[str]:
    return sorted(
        {"".join(rng.choice(_SYL) for _ in range(rng.randint(2, 4))) for _ in range(size * 3)}
    )[:size]


def _fixture_sets() -> tuple[set[str], set[str]]:
    recs = json.loads(FIXTURE.read_text())["records"]
    a, b = (set(_TOK.findall(r["description"].lower())) for r in recs)
    return a, b


def _long_pair(k: int, overlap: float, seed: int = 1):
    """Two token sets of size k sharing ``overlap`` of their tokens, from a big random vocab."""
    rng = random.Random(seed * 1000 + k)
    pool = _vocab(rng, 6 * k + 50)
    rng.shuffle(pool)
    shared = int(k * overlap)
    a = set(pool[:k])
    b = set(pool[:shared]) | set(pool[k : k + (k - shared)])
    return a, b


def _df_for(sets, n_docs=2000):
    """A df table in which every token is fairly rare (n_docs documents)."""
    return {t: 1 + (hash(t) % 7 == 0) for s in sets for t in s}, n_docs


# ================================================================ 1. cost (defect)


def test_cost_fixture_pair_is_linear_not_quadratic():
    """The report's fixture: 120 + 179 tokens -> 42,960 calls (2*|A|*|B|) on the old code."""
    a, b = _fixture_sets()
    assert len(a) > 100 and len(b) > 100  # fixture sanity (report: 120 + 179 tokens)
    for sim_fn in (lambda x, y: 1.0 if x == y else 0.0, _sim):
        sim = _Counting(sim_fn)
        idf_soft_similarity(a, b, {}, 1000, sim)
        assert sim.calls <= PER_TOKEN_CALLS * (len(a) + len(b)), (sim.calls, len(a), len(b))


def test_cost_doubling_both_sides_at_most_doubles_calls():
    counts = {}
    for k in (50, 100, 200, 400, 800):
        a, b = _long_pair(k, 0.6)
        sim = _Counting()
        idf_soft_similarity(a, b, *_df_for([a, b]), sim)
        counts[k] = sim.calls
        assert sim.calls <= PER_TOKEN_CALLS * (len(a) + len(b)), (k, counts)
    sizes = sorted(counts)
    for small, big in itertools.pairwise(sizes):
        assert counts[big] <= DOUBLING_FACTOR * counts[small] + 20, counts


def test_cost_other_side_much_larger_still_linear():
    """Lopsided: 3 tokens vs 600 (a product title vs a description) must not blow up either."""
    a, b = _long_pair(600, 0.5)
    short = set(sorted(a)[:3])
    sim = _Counting()
    idf_soft_similarity(short, b, *_df_for([short, b]), sim)
    assert sim.calls <= PER_TOKEN_CALLS * (len(short) + len(b)), sim.calls


# --------------------------------------------------- 1b. the real comparator path, counted


class _Budget(BaseException):  # not Exception: _resolve_core's `except Exception` must not eat it
    pass


@pytest.fixture
def counters(monkeypatch):
    """Count uncached difflib ratios and `_token_ratio` calls inside resolve_entities."""
    c = {"seq": 0, "tok": 0, "limit": None}
    orig_tok = re_mod._token_ratio

    def seq(a, b):
        c["seq"] += 1
        if c["limit"] is not None and c["seq"] > c["limit"]:
            raise _Budget(f"difflib ratio budget {c['limit']} exceeded")
        return difflib.SequenceMatcher(None, a, b).ratio()

    def tok(a, b):
        c["tok"] += 1
        return orig_tok(a, b)

    monkeypatch.setattr(re_mod, "_seq_ratio", seq)
    monkeypatch.setattr(re_mod, "_token_ratio", tok)
    return c


def _desc(rng, vocab, weights, k):
    return rng.choices(vocab, weights, k=k)


def test_comparator_long_descriptions_are_linear_in_tokens(counters):
    """`_comparison_level` and `_text_similarity` (the two comparator entry points) on
    long-description pairs: the real fixture, and synthetic 150/300/600-token descriptions."""
    recs = json.loads(FIXTURE.read_text())["records"]
    cases = [(recs[0]["description"].lower(), recs[1]["description"].lower())]
    rng = random.Random(7)
    vocab = _vocab(rng, 1500)
    weights = [1 / (i + 1) for i in range(len(vocab))]
    for k in (150, 300, 600):
        a = _desc(rng, vocab, weights, k)
        b = [w if rng.random() > 0.3 else rng.choice(vocab) for w in a]
        cases.append((" ".join(a), " ".join(b)))
    worst = []
    for s1, s2 in cases:
        t1, t2 = set(re_mod._tokenize(s1)), set(re_mod._tokenize(s2))
        df, n = build_token_document_frequencies([{"d": s1}, {"d": s2}])
        token_df = (df["d"], 200)  # a 200-document corpus in which these tokens are rare
        for name in ("_comparison_level", "_text_similarity"):
            counters["seq"] = counters["tok"] = 0
            if name == "_comparison_level":
                re_mod._comparison_level(s1, s2, token_df=token_df)
            else:
                re_mod._text_similarity(s1, s2, token_df[0], 200)
            bound = PER_TOKEN_CALLS * (len(t1) + len(t2))
            worst.append((name, len(t1), len(t2), counters["seq"], counters["tok"], bound))
            assert counters["seq"] <= bound, worst[-1]
            assert counters["tok"] <= bound, worst[-1]


# ================================================================ 2. short text is unchanged (guard)


def _short_corpus(rng):
    firsts = ["john", "mary", "peter", "anna", "luke", "sara", "omar", "ivan", "lena", "hugo",
              "chen", "olga", "pablo", "yuki", "nadia", "karl"]  # fmt: skip
    lasts = ["smith", "jones", "brown", "khan", "lopez", "novak", "weber", "rossi", "silva",
             "cohen", "meyer", "tanaka", "ford", "young"]  # fmt: skip
    legal = ["ltd", "inc", "gmbh", "co", "llc"]
    words = ["acme", "global", "systems", "widgets", "data", "labs", "trade", "partners"]
    docs = []
    for _ in range(300):
        kind = rng.random()
        if kind < 0.5:
            docs.append(f"{rng.choice(firsts)} {rng.choice(lasts)}")
        elif kind < 0.8:
            docs.append(f"{rng.choice(words)} {rng.choice(words)} {rng.choice(legal)}")
        else:
            docs.append(" ".join(rng.sample(words + firsts + lasts + legal, rng.randint(2, 8))))
    return docs


def _typo(rng, t):
    if len(t) < 3:
        return t
    i = rng.randrange(len(t) - 1)
    kind = rng.randrange(3)
    if kind == 0:
        return t[:i] + t[i + 1] + t[i] + t[i + 2 :]  # transposition ("jonh")
    if kind == 1:
        return t[:i] + t[i + 1 :]  # deletion
    return t[:i] + rng.choice("aeiouy") + t[i + 1 :]  # substitution ("smyth")


def _short_pairs(seed: int, n_pairs: int):
    rng = random.Random(seed)
    docs = _short_corpus(rng)
    toks = [list(dict.fromkeys(_TOK.findall(d))) for d in docs]
    df, n = build_token_document_frequencies([{"n": d} for d in docs])
    df, n = df["n"], n["n"]
    pairs = []
    for _ in range(n_pairs):
        base = rng.choice(toks)[:8]
        kind = rng.randrange(8)
        other = list(base)
        if kind == 1:  # typo in one token
            j = rng.randrange(len(other))
            other[j] = _typo(rng, other[j])
        elif kind == 2:  # reorder (a set: must be identical)
            rng.shuffle(other)
        elif kind == 3:  # add a common token
            other.append(rng.choice(["ltd", "inc", "co"]))
        elif kind == 4:  # D5 containment: add rare extra tokens
            other += [
                f"{rng.choice(_SYL)}{rng.choice(_SYL)}{rng.randint(0, 9)}"
                for _ in range(rng.randint(1, 3))
            ]
        elif kind == 5 and len(other) > 2:  # drop a token (the other direction of containment)
            other.pop(rng.randrange(len(other)))
        elif kind == 6:  # unrelated
            other = rng.choice(toks)
        elif kind == 7:  # shares one token with base
            other = [rng.choice(base)] + rng.choice(toks)
        a, b = set(base[:8]), set(other[:8])
        pairs.append((a, b))
    return pairs, df, n


@pytest.mark.parametrize("sim_name", ["token_ratio", "difflib"])
@pytest.mark.parametrize("seed", [11, 20261013, 424242])
def test_short_text_equals_todays_values(seed, sim_name):
    """Name-like values (<= 8 tokens a side): typos, reorders, common/rare extras, containment,
    unrelated, one shared token.  Each must equal the frozen current algorithm to 1e-9."""
    sim = re_mod._token_ratio if sim_name == "token_ratio" else _sim
    pairs, df, n = _short_pairs(seed, 400)
    contained = distinct = 0
    for a, b in pairs:
        assert 1 <= len(a) <= 8 and 1 <= len(b) <= 8
        want = _reference(a, b, df, n, sim)
        for x, y in ((a, b), (b, a)):
            got = idf_soft_similarity(x, y, df, n, sim)
            if want is None:
                assert got is None, (x, y, got)
            else:
                assert got is not None and abs(got - want) <= 1e-9, (
                    sorted(x),
                    sorted(y),
                    got,
                    want,
                )
        contained += (a <= b or b <= a) and a != b
        distinct += want is not None and 0.05 < want < 0.995
    # the generator really exercises the interesting shapes (guards the guard)
    assert contained >= 20 and distinct >= 100, (contained, distinct)


def test_short_text_pinned_values():
    """Values measured on the unfixed tree (same corpus as D5's guards): 10 first x 10 last names."""
    first = ["john", "mary", "peter", "anna", "luke", "sara", "omar", "ivan", "lena", "hugo"]
    last = ["smith", "jones", "brown", "khan", "lopez", "novak", "weber", "rossi", "silva", "cohen"]
    d, n = build_token_document_frequencies([{"n": f"{x} {y}"} for x in first for y in last])
    df, N = d["n"], n["n"]
    f = lambda a, b: idf_soft_similarity(set(a), set(b), df, N, re_mod._token_ratio)  # noqa: E731
    assert f({"jonh", "smith"}, {"john", "smith"}) == pytest.approx(0.8541666666666667, abs=1e-9)
    assert f({"john", "smyth"}, {"john", "smith"}) == pytest.approx(0.8833333333333333, abs=1e-9)
    assert f({"anna", "khan"}, {"john", "smith"}) == pytest.approx(0.3680555555555556, abs=1e-9)
    assert f({"john"}, {"john", "smith"}) == pytest.approx(
        0.6111111111111112, abs=1e-9
    )  # D5 containment
    assert f({"smith", "john", "khan"}, {"john", "smith"}) == pytest.approx(
        0.8333333333333334, abs=1e-9
    )


# ================================================================ 3. long text stays sensible (guard)


def test_long_identical_descriptions_score_one():
    for k in (100, 300):
        a, _ = _long_pair(k, 1.0)
        got = idf_soft_similarity(a, set(a), *_df_for([a]), _sim)
        assert got == pytest.approx(1.0, abs=1e-9), (k, got)
    a, b = _fixture_sets()
    assert idf_soft_similarity(a, set(a), {}, 1000, _sim) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("k", [100, 250])
def test_long_shared_rare_tokens_beat_unrelated_and_rank_by_overlap(k):
    """Score rises with the share of common rare tokens and clearly separates related from
    unrelated text (margin 0.15; the old soft code scores random syllable tokens 0.78 vs 0.98)."""
    scores = []
    for overlap in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0):
        a, b = _long_pair(k, overlap, seed=3)
        scores.append(idf_soft_similarity(a, b, *_df_for([a, b]), _sim))
    assert all(s is not None for s in scores)
    assert all(x < y for x, y in itertools.pairwise(scores)), scores
    assert scores[4] >= scores[0] + 0.15, scores  # 90 % shared vs unrelated


def test_long_near_duplicates_with_small_edits_stay_high():
    """A description with 5 % of its tokens dropped or misspelled is still the same text."""
    rng = random.Random(5)
    vocab = _vocab(rng, 1500)
    a = rng.sample(vocab, 200)
    b = [w for w in a if rng.random() > 0.03]
    b = [w[:-1] + "x" if rng.random() < 0.03 else w for w in b]
    sa, sb = set(a), set(b)
    df, n = _df_for([sa, sb])
    got = idf_soft_similarity(sa, sb, df, n, _sim)
    unrelated = idf_soft_similarity(sa, set(rng.sample(vocab, 200)) - sa, df, n, _sim)
    assert got is not None and unrelated is not None
    assert got >= 0.85, got
    assert got >= unrelated + 0.15, (got, unrelated)


def test_long_is_symmetric():
    for k, overlap in ((120, 0.5), (300, 0.9), (60, 0.0)):
        a, b = _long_pair(k, overlap, seed=9)
        b = set(list(b)[: max(1, int(len(b) * 0.8))])  # unequal sizes
        df, n = _df_for([a, b])
        s_ab, s_ba = idf_soft_similarity(a, b, df, n, _sim), idf_soft_similarity(b, a, df, n, _sim)
        assert s_ab is not None and s_ba is not None
        assert s_ab == pytest.approx(s_ba, abs=1e-9), (k, s_ab, s_ba)


def test_long_none_contract_is_preserved():
    a, b = _long_pair(150, 0.5)
    df, n = _df_for([a, b])
    assert idf_soft_similarity(set(), b, df, n, _sim) is None
    assert idf_soft_similarity(a, set(), df, n, _sim) is None
    assert idf_soft_similarity(a, b, df, 0, _sim) is None
    everywhere = {t: 2000 for t in a | b}  # df == N for every token: no information
    assert idf_soft_similarity(a, b, everywhere, 2000, _sim) is None
    assert idf_soft_similarity(a, set(a), everywhere, 2000, _sim) is None


# ================================================================ 4. pipeline (cost + quality)


def _product_corpus(seed: int, n_pairs: int, n_single: int):
    """Neutral synthetic product corpus: random-syllable vocabulary (zipf weighted so common
    tokens exist), 100-300 token descriptions.  ``n_pairs`` true duplicates across two sources
    with ~3 % of description tokens dropped or misspelt; ``n_single`` unrelated products."""
    rng = random.Random(seed)
    vocab = _vocab(rng, 2500)
    rng.shuffle(vocab)
    weights = [1 / (i + 1) for i in range(len(vocab))]

    def rec(rid, origin, title, desc, mfr, price):
        return {
            "id": rid, "title": title, "description": " ".join(desc), "manufacturer": mfr,
            "price": price, "_origin_id": origin, "_origin_table": "public.product",
            "_origin_id_row": rid,
        }  # fmt: skip

    recs, dups = [], []
    for i in range(n_pairs):
        d = _desc(rng, vocab, weights, rng.randint(100, 300))
        title, mfr = " ".join(rng.sample(vocab, 3)), rng.choice(vocab[:40])
        a = rec(f"a{i}", "src_a", title, d, mfr, f"{rng.randint(5, 500)}.99")
        d2 = [w for w in d if rng.random() > 0.03]
        d2 = [w[:-1] + "x" if rng.random() < 0.03 else w for w in d2]
        b = rec(f"b{i}", "src_b", title, d2, mfr, a["price"])
        recs += [a, b]
        dups.append((re_mod._record_id(a), re_mod._record_id(b)))
    for i in range(n_single):
        d = _desc(rng, vocab, weights, rng.randint(100, 300))
        recs.append(
            rec(f"s{i}", "src_a" if i % 2 else "src_b", " ".join(rng.sample(vocab, 3)), d,
                rng.choice(vocab[:40]), f"{rng.randint(5, 500)}.99")
        )  # fmt: skip
    return recs, dups


@pytest.fixture(scope="module")
def pipeline_run():
    """One `_resolve_core` over 60 long-description products, with a hard cost budget that makes
    the old quadratic code fail in seconds instead of hanging for minutes."""
    recs, dups = _product_corpus(20261013, n_pairs=24, n_single=12)
    total_tokens = sum(len(set(_TOK.findall(r["description"]))) for r in recs)
    budget = int(2 * (len(recs) - 1) * total_tokens)
    mp = pytest.MonkeyPatch()
    state = {"seq": 0}
    orig_seq = re_mod._seq_ratio

    def seq(a, b):
        state["seq"] += 1
        if state["seq"] > budget:
            raise _Budget(f"difflib ratio budget {budget} exceeded")
        return difflib.SequenceMatcher(None, a, b).ratio()

    mp.setattr(re_mod, "_seq_ratio", seq)
    t0 = time.perf_counter()
    try:
        core = re_mod._resolve_core("product", recs, None, None, None)
        err = None
    except _Budget as exc:
        core, err = None, exc
    finally:
        mp.undo()
    del orig_seq
    return {
        "core": core, "err": err, "dups": dups, "recs": recs, "budget": budget,
        "seq": state["seq"], "seconds": time.perf_counter() - t0, "total_tokens": total_tokens,
    }  # fmt: skip


@pytest.mark.timeout(240)
def test_pipeline_cost_resolve_core_on_long_descriptions_is_within_budget(pipeline_run):
    r = pipeline_run
    print(
        f"\n[D1] records={len(r['recs'])} tokens={r['total_tokens']} difflib ratios={r['seq']} "
        f"budget={r['budget']} seconds={r['seconds']:.1f}"
    )
    assert r["err"] is None, f"{r['err']} after {r['seconds']:.1f}s (old code: quadratic)"
    assert r["seq"] <= r["budget"]
    assert r["seconds"] < 120  # generous wall clock; the count above is the real check


@pytest.mark.timeout(240)
def test_pipeline_true_duplicates_still_score_above_unrelated(pipeline_run):
    r = pipeline_run
    if r["core"] is None:
        pytest.fail(f"run did not finish within the cost budget: {r['err']}")
    core = r["core"]
    sc = {frozenset((a, b)): s for a, b, s in core["scored_pairs"]}
    sc |= {frozenset((a, b)): s for a, b, _r1, _r2, s in core["borderline"]}
    dup_keys = {frozenset(p) for p in r["dups"]}
    dup = [sc.get(k, 0.0) for k in dup_keys]
    other = [s for k, s in sc.items() if k not in dup_keys]
    assert min(dup) > max(other, default=0.0), (min(dup), max(other, default=0.0))
    # near-identical descriptions (3 % edits), same title/manufacturer/price: must auto-match
    assert sum(s >= core["auto_match"] for s in dup) >= math.ceil(0.9 * len(dup)), sorted(dup)[:5]


# ================================================================ 5. plan 2.4: no corpus literals

SRC_FILES = [
    ROOT / "src/mcp_server/tools/fellegi_sunter.py",
    ROOT / "src/mcp_server/tools/resolve_entities.py",
]
BANNED = ("amazon", "google", "abt", "buy", "dblp", "scholar", "leipzig", "febrl", "walmart", "acm")


def _strings_and_names(path: Path):
    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    strings, names = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            strings.append(node.value)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            names.append(node.name)
    return strings, names


@pytest.mark.parametrize("path", SRC_FILES, ids=lambda p: p.name)
def test_no_corpus_literals(path):
    """Plan 2.4: the fix must be count/IDF based -- no dataset or product words in code."""
    strings, names = _strings_and_names(path)
    hits = [s for s in strings + names if any(w in s.lower() for w in BANNED)]
    assert not hits, hits
