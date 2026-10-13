"""D1 attack (adversarial, written against the fix in 2f90c83 after reading it).

Targets: ``idf_soft_similarity`` matched-token shortcut + linear budget.
Findings are `xfail(strict=True)` so they flip to XPASS (and fail loudly) when fixed.

* A  below-budget results equal today's full-max algorithm EXACTLY, at every size 1..40 (the
     acceptance only pins <= 8 tokens); above budget the score is the IDF exact overlap and
     never exceeds the full-max score.
* B  call count never exceeds the budget it advertises, and symmetric across the cliff.
* C  THE CLIFF: the soft/exact switch depends on the number of unmatched tokens (> 8 per side
     for equal sizes), so adding one more typo to a 20-30 token title drops STRONG to DISAGREE.
* D  plan 2.4: the literal 8 is a cost bound, but `test_p14_acceptance::test_6c` rejects it as a
     new numeric constant -- pinned here as a regression versus dd268cc.
* E  `_resolve_core` on a hard long-description product corpus (real English vocabulary, 45 %
     of description tokens reworded between sources): finishes, duplicates above unrelated.
"""

from __future__ import annotations

import collections
import difflib
import glob
import itertools
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
_TOK = re.compile(r"[a-z0-9]+")


def _sim(a: str, b: str) -> float:
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


class _Counting:
    def __init__(self):
        self.calls = 0

    def __call__(self, a: str, b: str) -> float:
        self.calls += 1
        return _sim(a, b)


def _reference(t1, t2, df, n):
    """Frozen copy of the pre-D1 algorithm (full max over the other side, D5 containment)."""
    if n <= 0 or not t1 or not t2:
        return None

    def w(t):
        return math.log2(n / min(max(df.get(t, 1), 1), n))

    def directed(src, dst):
        total = math.fsum(w(t) for t in src)
        if total == 0:
            return None
        return math.fsum(w(t) * max(_sim(t, u) for u in dst) for t in src) / total

    d1, d2 = directed(t1, t2), directed(t2, t1)
    if d1 is None or d2 is None:
        return None
    return min(d1, d2) if (t1 <= t2 or t2 <= t1) else (d1 + d2) / 2


def _exact_overlap(t1, t2, df, n):
    """What the over-budget path must equal: IDF-weighted exact-token overlap, both directions."""
    if n <= 0 or not t1 or not t2:
        return None

    def w(t):
        return math.log2(n / min(max(df.get(t, 1), 1), n))

    def directed(src, dst):
        total = math.fsum(w(t) for t in src)
        return None if total == 0 else math.fsum(w(t) for t in src if t in dst) / total

    d1, d2 = directed(t1, t2), directed(t2, t1)
    if d1 is None or d2 is None:
        return None
    return min(d1, d2) if (t1 <= t2 or t2 <= t1) else (d1 + d2) / 2


# ------------------------------------------------------------ English-like vocabulary (docs/)
def _english():
    c: collections.Counter = collections.Counter()
    for p in sorted(glob.glob(str(ROOT / "docs/**/*.md"), recursive=True))[:200]:
        c.update(re.findall(r"[a-z]{3,12}", Path(p).read_text(errors="ignore").lower()))
    vocab = [w for w, _ in c.most_common(4000)]
    return vocab, [1 / (i + 5) ** 0.9 for i in range(len(vocab))]


VOCAB, WTS = _english()


def _typo(rng: random.Random, t: str) -> str:
    i = rng.randrange(len(t) - 1)
    return t[:i] + t[i + 1] + t[i] + t[i + 2 :] if t[i] != t[i + 1] else t[:i] + t[i + 1 :]


def _title(rng, n):
    return list(dict.fromkeys(rng.choices(VOCAB, WTS, k=n * 2)))[:n]


def _corpus_df(rng, n_docs=2000):
    docs = [_title(rng, rng.randint(9, 30)) for _ in range(n_docs)]
    df, n = build_token_document_frequencies([{"t": " ".join(d)} for d in docs])
    return docs, df["t"], n["t"]


# ===================================================================== A. equality / dominance
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_below_budget_is_todays_value_above_budget_is_exact_overlap(seed):
    rng = random.Random(seed)
    docs, df, n = _corpus_df(rng, 800)
    soft = exact = 0
    for _ in range(700):
        a = docs[rng.randrange(len(docs))][: rng.randint(1, 40)]
        b = [t for t in a if rng.random() < rng.choice([0.9, 0.6, 0.3])]
        b = [_typo(rng, t) if len(t) > 2 and rng.random() < 0.3 else t for t in b]
        b += rng.choices(VOCAB, WTS, k=rng.randint(0, 12))
        t1, t2 = set(a), set(b)
        if not t1 or not t2:
            continue
        cnt = _Counting()
        new = idf_soft_similarity(t1, t2, df, n, token_sim=cnt)
        old = _reference(t1, t2, df, n)
        if new is None or old is None:
            assert new is None and old is None
            continue
        if cnt.calls:  # soft path taken: must be bit-for-bit today's answer
            soft += 1
            assert abs(new - old) < 1e-9, (len(t1), len(t2), new, old)
        else:  # no token_sim call at all: either identical-token coverage or exact overlap
            exact += 1
            assert abs(new - _exact_overlap(t1, t2, df, n)) < 1e-9
            assert new <= old + 1e-9
    assert soft >= 100 and exact >= 100, (soft, exact)  # the fuzz really crosses the switch


def test_a_identical_and_subset_pairs_make_no_token_sim_call_when_nothing_unmatched():
    t = set(_title(random.Random(5), 30))
    cnt = _Counting()
    assert idf_soft_similarity(t, set(t), {x: 1 for x in t}, 100, token_sim=cnt) == 1.0
    assert cnt.calls == 0


# ============================================================ B. cost bound and symmetry
def test_b_calls_never_exceed_advertised_bound_and_scores_are_symmetric():
    rng = random.Random(11)
    docs, df, n = _corpus_df(rng, 600)
    worst = 0.0
    for _ in range(600):
        a = set(docs[rng.randrange(len(docs))][: rng.randint(1, 40)])
        k = rng.randint(1, 300)
        b = set(rng.choices(VOCAB, WTS, k=k)) | set(
            rng.sample(sorted(a), min(len(a), rng.randint(0, 8)))
        )
        c1, c2 = _Counting(), _Counting()
        s1 = idf_soft_similarity(a, b, df, n, token_sim=c1)
        s2 = idf_soft_similarity(b, a, df, n, token_sim=c2)
        assert c1.calls == c2.calls  # the budget expression is symmetric
        if s1 is None or s2 is None:
            assert s1 is None and s2 is None
        else:
            assert abs(s1 - s2) < 1e-9
        worst = max(worst, c1.calls / (len(a) + len(b)))
    assert worst <= 8.0 + 1e-9, worst  # advertised: <= 8 calls per token of the pair


# ================================================================================ C. the cliff
def _typo_pair(n_tokens: int, n_typos: int, seed: int):
    rng = random.Random(seed)
    docs, df, n = _corpus_df(rng, 600)
    t = list(dict.fromkeys(list(itertools.chain.from_iterable(docs[:6]))))[:n_tokens]
    assert len(t) == n_tokens
    d = list(t)
    for i in rng.sample(range(n_tokens), n_typos):
        d[i] = _typo(rng, d[i])
    assert set(d) & set(t[i] for i in range(n_tokens)) != set(d)
    return set(t), set(d), df, n


def _level(s):
    return "STRONG" if s >= 0.90 else "WEAK" if s >= 0.70 else "DISAGREE"


@pytest.mark.parametrize("n_tokens", [10, 16, 20, 30])
def test_c_up_to_eight_typos_keep_the_level_of_todays_code(n_tokens):
    """Guard (passes): below the cliff the answer is today's, STRONG for a few typos."""
    for typos in range(1, min(8, n_tokens // 2) + 1):
        t1, t2, df, n = _typo_pair(n_tokens, typos, seed=n_tokens * 100 + typos)
        new = idf_soft_similarity(t1, t2, df, n, token_sim=_sim)
        assert _level(new) == _level(_reference(t1, t2, df, n)), (n_tokens, typos, new)


@pytest.mark.xfail(
    strict=True,
    reason="D1-ATTACK cliff: 20-token title, 8 typos 0.925 (STRONG), 9 typos 0.600 (DISAGREE); "
    "the switch is a count of unmatched tokens, not a property of the text",
)
@pytest.mark.parametrize("n_tokens", [20, 30])
def test_c_one_more_typo_must_not_drop_strong_to_disagree(n_tokens):
    levels = []
    for typos in (7, 8, 9, 10):
        t1, t2, df, n = _typo_pair(n_tokens, typos, seed=n_tokens * 100 + typos)
        levels.append(_level(idf_soft_similarity(t1, t2, df, n, token_sim=_sim)))
    # monotone, and no STRONG -> DISAGREE step between neighbouring typo counts
    assert not any(a == "STRONG" and b == "DISAGREE" for a, b in itertools.pairwise(levels)), levels


@pytest.mark.xfail(strict=True, reason="D1-ATTACK cliff reaches the real comparator unchanged")
def test_c_real_comparison_level_cliff_on_a_24_token_title():
    rng = random.Random(3)
    docs, df, n = _corpus_df(rng, 600)
    t = list(dict.fromkeys(list(itertools.chain.from_iterable(docs[:8]))))[:24]
    out = []
    for typos in (8, 9):
        d = list(t)
        for i in random.Random(typos).sample(range(24), typos):
            d[i] = _typo(rng, d[i])
        out.append(re_mod._comparison_level(" ".join(t), " ".join(d), token_df=(df, n))[0])
    assert out[0] == out[1], out


@pytest.mark.xfail(
    strict=True,
    reason="D1-ATTACK: medium edits (10 % typo + 10 % drop + 10 % reworded) on 9-30 token titles "
    "flip the level versus the unbounded algorithm for ~8 % of true duplicates",
)
def test_c_level_flip_rate_vs_unbounded_on_medium_edited_titles_is_small():
    rng = random.Random(1)
    docs, df, n = _corpus_df(rng, 1500)
    flips = total = 0
    for t in docs[:600]:
        d = []
        for w in t:
            r = rng.random()
            if r < 0.10:
                continue
            d.append(
                rng.choices(VOCAB, WTS)[0]
                if r < 0.20
                else _typo(rng, w)
                if r < 0.30 and len(w) > 2
                else w
            )
        t1, t2 = set(t), set(d or t[:1])
        new = idf_soft_similarity(t1, t2, df, n, token_sim=_sim)
        old = _reference(t1, t2, df, n)
        total += 1
        flips += _level(new) != _level(old)
    assert flips / total < 0.02, (flips, total)


def test_c_light_edits_have_zero_flips_guard():
    """Guard (passes): 5 % typo + 5 % drop never crosses the cliff on 9-30 token titles."""
    rng = random.Random(2)
    docs, df, n = _corpus_df(rng, 1500)
    for t in docs[:600]:
        d = [
            _typo(rng, w) if rng.random() < 0.05 and len(w) > 2 else w
            for w in t
            if rng.random() > 0.05
        ]
        t1, t2 = set(t), set(d or t[:1])
        assert _level(idf_soft_similarity(t1, t2, df, n, token_sim=_sim)) == _level(
            _reference(t1, t2, df, n)
        )


# ============================================================================== D. plan 2.4
def test_d_p14_6c_numeric_constant_gate_is_not_regressed_versus_parent():
    """`test_p14_acceptance::test_6c` passed on dd268cc and fails on 2f90c83 because of the new
    module-level `_SOFT_CALLS_PER_TOKEN = 8` (named neither *SAMPLE* nor *SEED*, not in
    BASELINE_FS_NUMERIC_CONSTS).  Either the constant is justified in that baseline or removed."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(ROOT / "tests/part1/test_p14_acceptance.py"), "-k", "6c"],
        capture_output=True, text=True, cwd=ROOT,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout[-600:]


# =================================================================== E. pipeline, hard corpus
def _hard_products(seed: int, n_pairs: int, n_single: int):
    rng = random.Random(seed)

    def desc(k):
        return rng.choices(VOCAB, WTS, k=k)

    def rec(rid, origin, title, d, mfr, price):
        return {
            "id": rid, "title": title, "description": " ".join(d), "manufacturer": mfr,
            "price": price, "_origin_id": origin, "_origin_table": "public.product",
            "_origin_id_row": rid,
        }  # fmt: skip

    recs, dups = [], []
    for i in range(n_pairs):
        d = desc(rng.randint(120, 320))
        title = " ".join(rng.sample(VOCAB[100:2500], 3))
        mfr, price = rng.choice(VOCAB[:60]), f"{rng.randint(5, 500)}.99"
        # source b: shorter, 45 % of tokens reworded, title keeps one typo
        d2 = [rng.choice(VOCAB) if rng.random() < 0.45 else w for w in d][: int(len(d) * 0.8)]
        a = rec(f"a{i}", "src_a", title, d, mfr, price)
        b = rec(f"b{i}", "src_b", re.sub(r"(\w)(\w)\b", r"\2\1", title, count=1), d2, mfr, price)
        recs += [a, b]
        dups.append((re_mod._record_id(a), re_mod._record_id(b)))
    for i in range(n_single):
        recs.append(
            rec(f"s{i}", "src_a" if i % 2 else "src_b", " ".join(rng.sample(VOCAB[100:2500], 3)),
                desc(rng.randint(120, 320)), rng.choice(VOCAB[:60]), f"{rng.randint(5, 500)}.99")
        )  # fmt: skip
    return recs, dups


@pytest.mark.timeout(300)
def test_e_resolve_core_hard_long_description_corpus_finishes_and_separates():
    recs, dups = _hard_products(31337, n_pairs=30, n_single=20)
    t0 = time.perf_counter()
    core = re_mod._resolve_core("product", recs, None, None, None)
    secs = time.perf_counter() - t0
    sc = {frozenset((a, b)): s for a, b, s in core["scored_pairs"]}
    sc |= {frozenset((a, b)): s for a, b, _r1, _r2, s in core["borderline"]}
    dk = {frozenset(p) for p in dups}
    dup = sorted(sc.get(k, 0.0) for k in dk)
    other = [s for k, s in sc.items() if k not in dk]
    print(f"\n[D1-attack] records={len(recs)} seconds={secs:.1f} dup_min={dup[0]:.3f} "
          f"dup_median={dup[len(dup) // 2]:.3f} other_max={max(other, default=0):.3f}")  # fmt: skip
    assert secs < 120
    assert dup[0] > max(other, default=0.0)  # every true duplicate outranks every non-duplicate


def test_e_empty_and_single_token_sides_keep_none_contract():
    assert idf_soft_similarity(set(), {"a"}, {"a": 1}, 10, token_sim=_sim) is None
    assert idf_soft_similarity({"a"}, {"b"}, {"a": 10, "b": 10}, 10, token_sim=_sim) is None
    assert idf_soft_similarity({"a"}, {"a"}, {"a": 1}, 0, token_sim=_sim) is None
    # n=1 corner: every weight is 0 -> None, never ZeroDivisionError
    assert idf_soft_similarity({"a", "b"}, {"a", "c"}, {}, 1, token_sim=_sim) is None
