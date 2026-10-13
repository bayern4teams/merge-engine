"""PRE-D1 acceptance (LOCAL_VERIFY_REPORT "Pre-existing"): a name-only entity never merges.

Derived from the report's Evidence text only, before any fix exists. Live: Affiliations has one
data column (``name``); 0 of 16,795 true pairs merged on main and branch, max score over 30,593
scored pairs 0.109, two identical names score 0.058. Hypothesis in the report: the heuristic or
model needs more than one agreeing field to leave the exclude zone.

Everything runs through ``_resolve_core("organisation", ...)`` on SYNTHETIC seeded corpora: one
text field, names built from random syllables, no corpus literals.

Targets-the-defect (fail on the old code): test_a_*, test_b_*, test_small_corpus_*.
Guards (pass on the old code and must keep passing): test_c_*, test_d_*, test_e_*, test_f_*,
test_bars_*, test_empty_names_*, test_ordering_*.

The band bars 0.70 (review) / 0.95 (auto) are sanctioned (P15); a fix may not move them.
"""

from __future__ import annotations

import ast
import math
import random
from pathlib import Path

import pytest

from src.mcp_server.tools.resolve_entities import _record_id, _resolve_core

ROOT = Path(__file__).resolve().parents[2]
REVIEW, AUTO = 0.70, 0.95
SEEDS = [20261013, 7, 424242]
NEAR_FRACTION = 0.90  # share of near-duplicate pairs that must reach the review band

_SYL = ["ba", "ko", "ri", "tu", "sen", "mo", "la", "vex", "dor", "pi", "nal", "qua", "zen", "fi",
        "gra", "lum", "tek", "sor", "vin", "hal"]  # fmt: skip
_COMMON = ["group", "centre", "labs"]  # given a high document frequency by construction


def _rec(i: int, name: str | None) -> dict:
    return {
        "id": f"r{i}", "name": name, "_origin_id": "o",
        "_origin_table": "public.organisation", "_origin_id_row": f"r{i}",
    }  # fmt: skip


def _typo(word: str, rng: random.Random) -> str:
    """One adjacent transposition inside the word (never the first or last char)."""
    for _ in range(20):
        i = rng.randrange(1, len(word) - 2)
        if word[i] != word[i + 1]:
            return word[:i] + word[i + 1] + word[i] + word[i + 2 :]
    return word[:-1]


class Corpus:
    """~230 name-only records. ``groups[kind]`` is a list of (id, id) pairs (or id-lists)."""

    def __init__(self, seed: int):
        rng = random.Random(seed)
        vocab = sorted(
            {"".join(rng.choice(_SYL) for _ in range(rng.randint(3, 5))) for _ in range(3000)}
        )
        rng.shuffle(vocab)
        rare = iter(vocab)  # every rare token is used exactly once across the corpus
        self.recs: list[dict] = []
        self.groups: dict[str, list] = {
            k: [] for k in ("ident", "typo", "order", "addcommon", "common_only", "share1of2",
                            "share1of3", "control")
        }  # fmt: skip

        def add(name: str) -> str:
            self.recs.append(_rec(len(self.recs), name))
            return _record_id(self.recs[-1])

        # Identical strings (some carry a common token in the middle, as legal forms do).
        for k in range(40):
            a, b = next(rare), next(rare)
            name = f"{a} {rng.choice(_COMMON)} {b}" if k % 2 else f"{a} {b}"
            self.groups["ident"].append((add(name), add(name)))
        # One typo in one rare token.
        for _ in range(20):
            a, b = next(rare), next(rare)
            self.groups["typo"].append((add(f"{a} {b}"), add(f"{_typo(a, rng)} {b}")))
        # Word order swapped.
        for _ in range(10):
            a, b = next(rare), next(rare)
            self.groups["order"].append((add(f"{a} {b}"), add(f"{b} {a}")))
        # One extra common token on one side.
        for _ in range(10):
            a, b = next(rare), next(rare)
            self.groups["addcommon"].append(
                (add(f"{a} {b}"), add(f"{a} {b} {rng.choice(_COMMON)}"))
            )
        # Distinct names whose only shared tokens are common ones (df >> 1 by construction).
        ids = [add(f"{next(rare)} {_COMMON[0]} {_COMMON[1]}") for _ in range(25)]
        self.groups["common_only"] = [(x, y) for i, x in enumerate(ids) for y in ids[i + 1 :]]
        # Distinct names sharing ONE rare token but differing in another rare token.
        for _ in range(15):
            a, b, c = next(rare), next(rare), next(rare)
            self.groups["share1of2"].append((add(f"{a} {b}"), add(f"{a} {c}")))
        for _ in range(10):
            a, b, c, d, e = (next(rare) for _ in range(5))
            self.groups["share1of3"].append((add(f"{a} {b} {c}"), add(f"{a} {d} {e}")))
        # Wholly distinct control pairs.
        for _ in range(15):
            self.groups["control"].append(
                (add(f"{next(rare)} {next(rare)}"), add(f"{next(rare)} {next(rare)}"))
            )


def _score(recs: list[dict]):
    core = _resolve_core("organisation", recs, None, None, None)
    sc = {frozenset((a, b)): s for a, b, s in core["scored_pairs"]}
    sc |= {frozenset((a, b)): s for a, b, _r1, _r2, s in core["borderline"]}
    return core, sc


@pytest.fixture(scope="module", params=SEEDS, ids=lambda s: f"seed{s}")
def run(request):
    c = Corpus(request.param)
    core, sc = _score(c.recs)
    get = lambda kind: [sc.get(frozenset(p), 0.0) for p in c.groups[kind]]  # noqa: E731
    return {"c": c, "core": core, "sc": sc, "get": get}


def _frac_at_least(vals, bar):
    return sum(v >= bar for v in vals) / len(vals)


# ------------------------------------------------------------------------ (a) the defect


def test_a_identical_rare_names_reach_the_review_band(run):
    """Two records with the same rare name are the clearest possible duplicate. Old code: every
    one of them scores 0.19 (max over the whole corpus 0.19), nothing leaves the exclude zone."""
    s = run["get"]("ident")
    low = sorted(round(v, 3) for v in s)[:3]
    assert min(s) >= REVIEW, (
        f"{sum(v < REVIEW for v in s)}/{len(s)} identical pairs below {REVIEW}; lowest {low}"
    )


def test_a_scores_are_valid_probabilities(run):
    """Identical rare names may reach auto (>=0.95) or stay in
    review. Only the lower bar is demanded (test_a_...review_band). This test pins that the
    scores are valid probabilities so a fix cannot 'pass' with out-of-range values."""
    s = run["get"]("ident")
    assert all(0.0 <= v <= 1.0 and math.isfinite(v) for v in s), s[:5]


# ------------------------------------------------------------------------ (b) near-duplicates


def test_b_one_typo_pairs_reach_the_review_band(run):
    s = run["get"]("typo")
    assert _frac_at_least(s, REVIEW) >= NEAR_FRACTION, (
        f"{_frac_at_least(s, REVIEW):.0%} of typo pairs reach {REVIEW}; scores {sorted(round(v, 3) for v in s)[:5]}"
    )


def test_b_word_order_pairs_reach_the_review_band(run):
    s = run["get"]("order")
    assert _frac_at_least(s, REVIEW) >= NEAR_FRACTION, sorted(round(v, 3) for v in s)[:5]


def test_b_extra_common_token_pairs_reach_the_review_band(run):
    s = run["get"]("addcommon")
    assert _frac_at_least(s, REVIEW) >= NEAR_FRACTION, sorted(round(v, 3) for v in s)[:5]


def test_b_near_duplicates_never_outscore_identical(run):
    """A typo is weaker evidence than an identical string: mean(typo) <= mean(identical)."""
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    assert mean(run["get"]("typo")) <= mean(run["get"]("ident")) + 1e-9


def _small(n: int, suffix: str = ""):
    rng = random.Random(99)
    names = sorted(
        {
            "".join(rng.choice(_SYL) for _ in range(4))
            + " "
            + "".join(rng.choice(_SYL) for _ in range(4))
            for _ in range(60)
        }
    )
    names = [x + suffix for x in names[:n]]
    recs = [_rec(i, x) for i, x in enumerate(names)] + [_rec(n, names[0]), _rec(n + 1, names[1])]
    core, sc = _score(recs)
    return core, [
        sc.get(frozenset((_record_id(recs[i]), _record_id(recs[n + i]))), 0.0) for i in (0, 1)
    ]


def test_small_corpus_with_a_fitted_model_identical_pair_reaches_the_band():
    """The defect is not about corpus size: 12 names that all share a common token (87 candidate
    pairs, so a model IS fitted) plus two identical duplicates. Old code: 0.06 and below."""
    core, s = _small(12, " group")
    assert core["model_fitted"]
    assert min(s) >= REVIEW, s


def test_small_corpus_without_a_model_keeps_scoring_identical_names_at_the_top():
    """Guard: with 2 candidate pairs no model fits and the heuristic already gives 1.0 for an
    identical name. A fix must not break the heuristic path (old code: 1.0)."""
    core, s = _small(12)
    assert not core["model_fitted"]
    assert min(s) >= AUTO, s


# ------------------------------------------------------------------------ (c) no over-merge


def test_c_distinct_names_sharing_only_common_tokens_stay_below_the_band(run):
    s = run["get"]("common_only")
    assert max(s) < REVIEW, (max(s), sum(v >= REVIEW for v in s), len(s))


def test_c_wholly_distinct_names_stay_below_the_band(run):
    assert max(run["get"]("control")) < REVIEW


# ------------------------------------------------------------------------ (d) shared rare token


@pytest.mark.parametrize("kind", ["share1of2", "share1of3"])
def test_d_shared_rare_token_with_a_different_rare_token_is_not_auto_merged(run, kind):
    s = run["get"](kind)
    assert max(s) < AUTO, (kind, max(s), sum(v >= AUTO for v in s), len(s))


def test_ordering_identical_outscores_every_distinct_pair(run):
    """A fix that lifts everything into the band proves nothing: identical strings must stay
    strictly above every distinct-name pair, and above the 1-of-3 shared-token pairs by a gap."""
    ident = min(run["get"]("ident"))
    distinct = (
        run["get"]("common_only")
        + run["get"]("control")
        + run["get"]("share1of2")
        + run["get"]("share1of3")
    )
    assert ident > max(distinct), (ident, max(distinct))


def test_empty_and_null_names_never_merge_each_other():
    """NULL / blank names carry no evidence. They must not crash and must stay out of the band."""
    c = Corpus(SEEDS[0])
    recs = c.recs[:60] + [_rec(1000 + i, v) for i, v in enumerate([None, None, "", "", " ", "  "])]
    _core, sc = _score(recs)
    blanks = [_record_id(r) for r in recs[60:]]
    s = [sc.get(frozenset((x, y)), 0.0) for i, x in enumerate(blanks) for y in blanks[i + 1 :]]
    assert max(s) < REVIEW, sorted(s)[-3:]


def test_bars_are_unchanged(run):
    """The fix may not buy recall by lowering the sanctioned bars (P15)."""
    assert run["core"]["auto_match"] == pytest.approx(AUTO)
    assert run["core"]["auto_exclude"] == pytest.approx(REVIEW)


# ------------------------------------------------------------------------ (e) multi-field guard

# Captured by running the code at HEAD 6c69bc09ca1f3d87abe5d41196563b518063c0fb (no src change
# since d5ceee8) BEFORE any PRE-D1 fix. 200 person records (name + email + city), seed 20261013.
# A name-only fix must leave multi-field entities exactly as they are.
GUARD_SEED = 20261013
GUARD_PAIRS = {  # (kind, i, j) -> score
    ("dup", 0, 120): 0.9959, ("dup", 5, 125): 0.9959, ("dup", 29, 149): 0.9959,
    ("typo", 30, 150): 0.9992, ("typo", 40, 160): 0.9992, ("typo", 49, 169): 0.9992,
    ("email", 50, 170): 0.0, ("email", 60, 180): 0.0544, ("email", 64, 184): 0.0,
    ("name", 65, 185): 0.0013284596230428998, ("name", 70, 190): 0.0013284596230428998,
    ("name", 79, 199): 0.0013284596230428998,
}  # fmt: skip
GUARD_SUM, GUARD_COUNT = 52.82162448851662, 408


def _persons(seed: int = GUARD_SEED, n: int = 120):
    rng = random.Random(seed)

    def w(lo, hi):
        return "".join(rng.choice(_SYL[:16]) for _ in range(rng.randint(lo, hi)))

    firsts = sorted({w(2, 3).title() for _ in range(80)})
    lasts = sorted({w(3, 4).title() for _ in range(120)})
    cities = sorted({w(2, 3).title() for _ in range(15)})
    recs: list[dict] = []

    def add(f, last, e, c):
        i = len(recs)
        recs.append({"id": f"p{i}", "name": f"{f} {last}", "email": e, "city": c, "_origin_id": "o",
                     "_origin_table": "public.person", "_origin_id_row": f"p{i}"})  # fmt: skip

    base = []
    for _ in range(n):
        f, last, c = rng.choice(firsts), rng.choice(lasts), rng.choice(cities)
        e = f"{f.lower()}.{last.lower()}{rng.randint(1, 99)}@{rng.choice(['mail', 'post'])}.example"
        base.append((f, last, e, c))
        add(f, last, e, c)
    for k in range(0, 30):
        add(*base[k])
    for k in range(30, 50):
        f, last, e, c = base[k]
        add(f[:-1] + f[-1] * 2, last, e, c)
    for k in range(50, 65):
        add(rng.choice(firsts), rng.choice(lasts), base[k][2], rng.choice(cities))
    for k in range(65, 80):
        f, last, e, c = base[k]
        add(f, last, f"x{k}@other.example", rng.choice(cities))
    return recs


def test_e_multi_field_entity_scores_are_unchanged():
    recs = _persons()
    core = _resolve_core("person", recs, None, None, None)
    sc = {frozenset((a, b)): s for a, b, s in core["scored_pairs"]}
    sc |= {frozenset((a, b)): s for a, b, _r1, _r2, s in core["borderline"]}
    ids = [_record_id(r) for r in recs]
    for (kind, i, j), want in GUARD_PAIRS.items():
        got = sc.get(frozenset((ids[i], ids[j])))
        assert got is not None and abs(got - want) <= 1e-6, (kind, i, j, got, want)
    assert len(sc) == GUARD_COUNT
    assert abs(sum(sc.values()) - GUARD_SUM) <= 1e-6, (sum(sc.values()), GUARD_SUM)


# ------------------------------------------------------------------------ (f) plan 2.4: no corpus words

SRC_FILES = [
    ROOT / "src/mcp_server/tools/resolve_entities.py",
    ROOT / "src/mcp_server/tools/fellegi_sunter.py",
]
BANNED = ("inria", "rocquencourt", "affiliation", "university", "univ")


def _code_strings_and_names(path: Path):
    tree = ast.parse(path.read_text())
    strings, names = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)  # docstrings included: stricter, and clean today
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
    return strings, names


@pytest.mark.parametrize("path", SRC_FILES, ids=lambda p: p.name)
def test_f_no_corpus_words_in_source(path):
    """A fix must come from counts (IDF, field cardinality), not a word list (plan 2.4).
    Numeric constants cannot be grepped: attack mode must ask 'would a corpus count produce
    this number?' of every new literal in the diff."""
    strings, names = _code_strings_and_names(path)
    hits = [s for s in strings + names if any(b in s.lower() for b in BANNED)]
    assert not hits, hits
