"""D5 acceptance (LOCAL_VERIFY_REPORT D5): "Addendum to X" must not auto-match X.

Derived from the report's Evidence / Root cause / Fix text only, before any fix exists.
Defect: ``idf_soft_similarity`` averages the two Monge-Elkan directions, so X -> "Addendum
to X" scores 1.0 and the extra rare tokens only halve the other side.

Targets-the-defect tests (fail on the old code): test_superset_*, test_dblp_acm_fixture_*,
test_pipeline_variants_*.  Regression guards (pass on the old code and must keep passing):
test_guard_*, test_pipeline_true_duplicates_*, test_no_special_case_words.
"""

from __future__ import annotations

import ast
import difflib
import itertools
import json
import math
from pathlib import Path

import pytest

from src.mcp_server.tools.fellegi_sunter import (
    build_token_document_frequencies,
    idf_soft_similarity,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "live_repros" / "p14_dblp_addendum.json"
STRONG = 0.90  # the comparison level cut-off in resolve_entities._comparison_level


def _sim(a: str, b: str) -> float:
    """Character similarity of two tokens, same family as the engine's ``_token_ratio``."""
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


def _f(a: set[str], b: set[str], df: dict[str, int], n: int) -> float:
    out = idf_soft_similarity(a, b, df, n, _sim)
    assert out is not None
    return out


def _w(df: dict[str, int], n: int, t: str) -> float:
    return math.log2(n / df.get(t, 1))


# --------------------------------------------------------------------------- 1. the defect (unit)

# 1,000 documents. Four title words of middling rarity (df 20) shared by both titles, plus
# rare extras (df 1). Extras together outweigh the shared words (about 30 bits vs 23 bits).
N = 1000
SHARED = {"alpha", "bravo", "delta", "gamma"}
RARE = ["quxplo", "zyrmek", "vintha"]
DF = {**dict.fromkeys(SHARED, 20), **dict.fromkeys(RARE, 1)}


def test_superset_with_rare_extras_scores_clearly_below_identical():
    ident = _f(SHARED, SHARED, DF, N)
    sup = _f(SHARED, SHARED | set(RARE), DF, N)
    assert ident == pytest.approx(1.0)
    # The extras carry more evidence than the shared words: the pair must sit well below
    # identical (old code averages 1.0 and 0.43 -> 0.71 here, i.e. only 0.29 below).
    assert sup <= ident - 0.30, sup


def test_superset_one_rare_extra_is_not_near_identical():
    """One rare extra word against a longer shared title: still a different title.
    Old code averages 1.0 and ~0.80 -> 0.90 (a STRONG match)."""
    shared = {f"w{i}" for i in range(6)}
    df = {**dict.fromkeys(shared, 12), "rareword": 1}
    sup = _f(shared, shared | {"rareword"}, df, N)
    assert sup < STRONG - 0.05, sup


def test_superset_score_falls_as_rare_extras_accumulate_and_is_symmetric():
    scores = []
    for k in range(0, len(RARE) + 1):
        b = SHARED | set(RARE[:k])
        s_ab, s_ba = _f(SHARED, b, DF, N), _f(b, SHARED, DF, N)
        assert s_ab == pytest.approx(s_ba, abs=1e-12)
        scores.append(s_ab)
    assert all(x > y for x, y in itertools.pairwise(scores)), scores


def test_dblp_acm_fixture_title_pair_is_not_a_strong_match():
    """The report's own pair at the unit level: CeriFPT94 vs CeriFPT95 titles, df from the
    fixture's 40 records. Old code: 0.926 (STRONG)."""
    recs = json.loads(FIXTURE.read_text())["records"]
    df_all, n_all = build_token_document_frequencies(recs)
    tok = lambda s: set(__import__("re").findall(r"[a-z0-9]+", s.lower()))  # noqa: E731
    by = {r["id"]: r["title"] for r in recs}
    a, b = tok(by["journals/tods/CeriFPT94"]), tok(by["journals/tods/CeriFPT95"])
    s = _f(a, b, df_all["title"], n_all["title"])
    assert s < STRONG, s
    assert s == pytest.approx(_f(b, a, df_all["title"], n_all["title"]))


# ---------------------------------------------------------------- 2. regression guards (unit)

_FIRST = ["john", "mary", "peter", "anna", "luke", "sara", "omar", "ivan", "lena", "hugo"]
_LAST = ["smith", "jones", "brown", "khan", "lopez", "novak", "weber", "rossi", "silva", "cohen"]
_PEOPLE = [{"n": f"{x} {y}"} for x in _FIRST for y in _LAST]
_PDF, _PN = build_token_document_frequencies(_PEOPLE)
PDF, PN = _PDF["n"], _PN["n"]


def test_guard_identical_sets_and_token_order():
    t = {"integrity", "maintenance", "rules"}
    assert _f(t, t, DF, N) == pytest.approx(1.0)
    assert _f(set(["a", "b", "c"]), set(["c", "b", "a"]), {}, 1000) == pytest.approx(1.0)
    assert _f({"john", "smith"}, {"smith", "john"}, PDF, PN) == pytest.approx(1.0)


def test_guard_typo_tolerance_survives():
    """Measured on the old code (this exact corpus): 'jonh smith' vs 'john smith' = 0.854,
    'john smyth' vs 'john smith' = 0.883. A fix may not make typos look like disagreement."""
    margin = 0.03
    assert _f({"jonh", "smith"}, {"john", "smith"}, PDF, PN) >= 0.854 - margin
    assert _f({"john", "smyth"}, {"john", "smith"}, PDF, PN) >= 0.883 - margin
    # and a typo is still clearly closer than a different person
    assert _f({"jonh", "smith"}, {"john", "smith"}, PDF, PN) > _f(
        {"anna", "khan"}, {"john", "smith"}, PDF, PN
    )


def test_guard_common_extra_token_costs_little():
    """A token in ~90% of documents (a legal-form word) adds almost no evidence."""
    docs = [{"n": f"{_FIRST[i % 10]}{_LAST[i % 7]}{i} ltd"} for i in range(100)]
    docs += [{"n": f"co{i} widgets"} for i in range(10)]
    d, n = build_token_document_frequencies(docs)
    df, nn = d["n"], n["n"]
    assert df["ltd"] == 100 and nn == 110
    common = _f({"acme", "widgets"}, {"acme", "widgets", "ltd"}, df, nn)
    assert common >= 0.97, common  # old code: 0.995
    # and it costs strictly less than a rare extra of the same kind
    rare = _f({"acme", "widgets"}, {"acme", "widgets", "zyrmek"}, df, nn)
    assert common > rare


def test_guard_no_information_returns_none():
    assert idf_soft_similarity(set(), {"a"}, {}, 10, _sim) is None
    assert idf_soft_similarity({"a"}, {"a"}, {"a": 10}, 10, _sim) is None  # df == N everywhere


# ------------------------------------------------- 3. pipeline: synthetic publication corpus

_PREFIXES = ("Addendum to", "Corrigendum to", "Errata for", "Erratum:", "Addendum:", "Corrigendum:")
_SYL = ["ba", "ko", "ri", "tu", "sen", "mo", "la", "vex", "dor", "pi", "nal", "qua", "zen", "fi"]
_GLUE = ["of", "for", "the", "and", "in", "on"]
MIN_DUP_FRACTION = 0.95  # true duplicates that must still auto-match (old code: 1.00)


def _corpus(seed: int, n_base: int = 120, n_variant: int = 12):
    """Deterministic. ``n_base`` papers each appear once per source (true duplicates, authors
    reordered, venue spelled differently); ``n_variant`` of them also get an
    Addendum/Corrigendum/Errata record in the DBLP-like source with year + 1."""
    import random

    from src.mcp_server.tools.resolve_entities import _record_id

    rng = random.Random(seed)
    vocab = sorted(
        {"".join(rng.choice(_SYL) for _ in range(rng.randint(2, 4))) for _ in range(900)}
    )
    firsts = sorted({"".join(rng.choice(_SYL) for _ in range(2)).title() for _ in range(60)})
    lasts = sorted({"".join(rng.choice(_SYL) for _ in range(3)).title() for _ in range(80)})

    def rec(rid, origin, title, authors, venue, year):
        return {
            "id": rid, "authors": ", ".join(authors), "title": title, "venue": venue,
            "year": str(year), "_origin_id": origin, "_origin_table": "public.publication",
            "_origin_id_row": rid,
        }  # fmt: skip

    recs, dups, variants = [], [], []
    for i in range(n_base):
        words = rng.sample(vocab, rng.randint(5, 8))
        for g in rng.sample(range(1, len(words)), 2):
            words.insert(g, rng.choice(_GLUE))
        title = " ".join(words).capitalize()
        authors = [f"{rng.choice(firsts)} {rng.choice(lasts)}" for _ in range(rng.randint(2, 4))]
        year = rng.randint(1990, 2002)
        shuffled = authors[:]
        rng.shuffle(shuffled)
        a = rec(
            f"acm{i}", "acm", title, authors, "ACM Transactions on Database Systems (TODS)", year
        )
        d = rec(f"dblp{i}", "dblp", title.title() if i % 2 else title, shuffled, "ACM Trans. Database Syst.", year)  # fmt: skip
        recs += [a, d]
        dups.append((_record_id(a), _record_id(d)))
        if i < n_variant:
            v = rec(f"dblpv{i}", "dblp", f"{_PREFIXES[i % len(_PREFIXES)]} {title}", shuffled, "ACM Trans. Database Syst.", year + 1)  # fmt: skip
            recs.append(v)
            variants += [(_record_id(a), _record_id(v)), (_record_id(d), _record_id(v))]
    return recs, dups, variants


def _scores(recs):
    from src.mcp_server.tools.resolve_entities import _resolve_core

    core = _resolve_core("publication", recs, None, None, None)
    sc = {frozenset((a, b)): s for a, b, s in core["scored_pairs"]}
    sc |= {frozenset((a, b)): s for a, b, _r1, _r2, s in core["borderline"]}
    return core["auto_match"], sc


@pytest.fixture(scope="module", params=[20261013, 7, 424242], ids=lambda s: f"seed{s}")
def pipeline(request):
    recs, dups, variants = _corpus(request.param)
    auto_match, sc = _scores(recs)
    n_var = sum(sc.get(frozenset(p), 0.0) >= auto_match for p in variants)
    n_dup = sum(sc.get(frozenset(p), 0.0) >= auto_match for p in dups)
    print(f"\n[D5 seed={request.param}] variant pairs auto-matched {n_var}/{len(variants)}; "
          f"true duplicates auto-matched {n_dup}/{len(dups)}")  # fmt: skip
    return {
        "sc": sc,
        "am": auto_match,
        "dups": dups,
        "variants": variants,
        "n_var": n_var,
        "n_dup": n_dup,
    }


def test_pipeline_variants_never_auto_match(pipeline):
    """None of the Addendum/Corrigendum/Errata pairs (variant vs either original) may reach
    the auto-match bar. Old code: 24/24, 24/24, 22/24 do (seeds 20261013, 7, 424242)."""
    bad = [
        (p, round(pipeline["sc"][frozenset(p)], 3))
        for p in pipeline["variants"]
        if pipeline["sc"].get(frozenset(p), 0.0) >= pipeline["am"]
    ]
    assert not bad, f"{len(bad)}/{len(pipeline['variants'])} variant pairs auto-match: {bad[:3]}"


def test_pipeline_true_duplicates_still_auto_match(pipeline):
    """Guard: the fix must not buy precision by losing the real cross-source duplicates."""
    need = math.ceil(MIN_DUP_FRACTION * len(pipeline["dups"]))
    assert pipeline["n_dup"] >= need, (pipeline["n_dup"], len(pipeline["dups"]))


# ----------------------------------------------------------------- 4. plan 2.4: no special-casing

SRC_FILES = [
    ROOT / "src/mcp_server/tools/fellegi_sunter.py",
    ROOT / "src/mcp_server/tools/resolve_entities.py",
]
BANNED_ROOTS = ("addend", "errat", "erratum", "corrigend")
STOPWORDS = {"to", "of", "for", "the", "and", "an", "in", "on"}


def _code_strings_and_names(path: Path):
    """String constants (docstrings excluded) and identifiers of a module."""
    tree = ast.parse(path.read_text())
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                doc_ids.add(id(first.value))
    strings, names = [], []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_ids
        ):
            strings.append(node.value)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            names.append(node.name)
    return strings, names


@pytest.mark.parametrize("path", SRC_FILES, ids=lambda p: p.name)
def test_no_special_case_words(path):
    """A fix must be IDF/count based, not a word list (plan 2.4)."""
    strings, names = _code_strings_and_names(path)
    hits = [s for s in strings + names if any(r in s.lower() for r in BANNED_ROOTS)]
    assert not hits, hits
    assert not [s for s in strings if s.lower() in STOPWORDS], "stop-word literal in code"
