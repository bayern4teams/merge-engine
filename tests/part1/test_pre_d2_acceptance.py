"""PRE-D2 acceptance (LOCAL_VERIFY_REPORT "Pre-existing" PRE-D2): product variants auto-merge.

Evidence: Abt-Buy "Omnimount Wall Speaker Mount - 20WLBK" vs "... - 20WLWH" (abt:10101, abt:10102)
score 0.957 (0.9588 measured here) on the full corpus, so the pair is auto-merged; the largest
cluster is 16. Hypothesis: model codes that differ in a suffix are not treated as conflicting
identifiers.  Written before any fix exists; ``src/`` is not read for the design.

Targets-the-defect tests (fail on the old code): ``test_variants_never_auto_match``,
``test_real_*`` (optional).  Guards (pass on the old code and must keep passing):
``test_true_duplicates_*``, ``test_one_sided_code_*``, ``test_persons_*``, ``test_publications_*``,
``test_no_corpus_words_in_source``, ``test_bars_are_unchanged``.

All synthetic corpora are seeded random syllables; no corpus literal appears in the data.
"""

from __future__ import annotations

import ast
import csv
import math
import os
import random
from pathlib import Path

import pytest

from src.mcp_server.tools.resolve_entities import _record_id, _resolve_core

ROOT = Path(__file__).resolve().parents[2]
SEEDS = [20261013, 7, 424242]
HEAD = "22685c3ff87a894035eef040ace19ef9e55067fa"  # the tree the frozen numbers were captured on

# --------------------------------------------------------------------------- synthetic products
_C, _V = "bdfgklmnprstvz", "aeiou"
_UP = "ABCDEFGHJKLMNPRSTUVWXYZ"


def _word(rng: random.Random, lo: int = 2, hi: int = 3) -> str:
    return "".join(rng.choice(_C) + rng.choice(_V) for _ in range(rng.randint(lo, hi)))


def _pool(rng: random.Random, n: int, lo: int = 2, hi: int = 3) -> list[str]:
    out: list[str] = []
    while len(out) < n:
        w = _word(rng, lo, hi)
        if w not in out:
            out.append(w)
    return out


class Shop:
    """One seeded product world: variant families, single products, filler, and buy-side copies."""

    def __init__(self, seed: int):
        rng = self.rng = random.Random(seed)
        self.recs: list[dict] = []
        self.by_id: dict[str, dict] = {}
        # truth, by category: lists of (abt_rid, buy_rid) or (rid, rid) for within-origin variants
        self.dups: dict[str, list[tuple[str, str]]] = {
            "exact": [], "split": [], "oneside": [], "family": []
        }  # fmt: skip
        self.variants: dict[str, list[tuple[str, str]]] = {"within": [], "cross": []}
        self.digit_variants: list[tuple[str, str]] = []  # base code differs, informational
        self._n = 0
        # skewed vocabulary: a few common words, a long tail (mid-IDF tokens, like real prose)
        self.vocab = _pool(rng, 260)
        self.weights = [1 / (i + 3) for i in range(len(self.vocab))]
        self.brands = _pool(rng, 40, 2, 3)
        self.suffixes = ["".join(rng.choice(_UP) for _ in range(2)) for _ in range(6)]
        self.suffixes = sorted(set(self.suffixes))
        self.colour = {s: _word(rng, 2, 2) for s in self.suffixes}  # finish word of a colour code
        self._codes: set[str] = set()

        for _ in range(32):  # families: 2-3 variants differing only in the colour-code suffix
            self._family(rng.randint(2, 3))
        for i in range(120):  # single products, a buy copy for 70% (rotating format)
            self._single(["exact", "split", "oneside"][i % 3] if i % 10 < 7 else None)
        for _ in range(90):  # unrelated filler on each side
            self._filler("abt")
            self._filler("buy")

    # -- builders
    def _base_code(self) -> str:
        rng = self.rng
        while True:
            pat = rng.choice(["dd LL", "LL ddd", "LL dd L", "L ddd"])
            base = "".join(
                rng.choice(_UP) if ch == "L" else str(rng.randint(1, 9)) if ch == "d" else ""
                for ch in pat
            )
            if base not in self._codes:
                self._codes.add(base)
                return base

    def _add(self, origin: str, name: str, desc: str, price: str | None, manu: str | None) -> str:
        self._n += 1
        rid = f"{origin[0]}{self._n}"
        rec = {
            "id": rid, "name": name, "description": desc, "price": price,
            "_origin_id": origin, "_origin_table": "public.product", "_origin_id_row": rid,
        }  # fmt: skip
        if origin == "buy":
            rec["manufacturer"] = manu
        self.recs.append(rec)
        self.by_id[rid] = rec
        return _record_id(rec)

    def _product(self):
        rng = self.rng
        brand = rng.choice(self.brands)
        title = " ".join(
            rng.sample(self.vocab[:120], 2) + rng.choices(self.vocab, self.weights, k=1)
        )
        feats = [
            " ".join(rng.choices(self.vocab, self.weights, k=rng.randint(3, 5))) for _ in range(4)
        ]
        price = rng.randint(20, 900) + rng.choice([0.0, 0.95, 0.99])
        return brand.title(), title.title(), feats, price

    @staticmethod
    def _fmt_code(code: str, how: str, suffix: str) -> str | None:
        if how == "oneside":
            return None
        if how == "split":
            base = code[: -len(suffix)]
            return f"{base}-{suffix}" if len(code) % 2 else f"{base} {suffix}"
        return code

    def _abt(self, brand, title, feats, price, code, suffix) -> str:
        finish = self.colour[suffix].title()
        name = f"{brand} {title} - {code}"
        desc = f"{name}/ {'/ '.join(feats)}/ {finish} Finish"
        return self._add("abt", name, desc, f"${price:.2f}", None)

    def _buy(self, brand, title, feats, price, code, suffix, how) -> str:
        rng = self.rng
        shown = self._fmt_code(code, how, suffix)
        name = f"{brand} {title}" + (f" - {shown}" if shown else "")
        desc = f"{brand} {title} " + " ".join(feats[: rng.randint(1, 2)])
        p = f"${price:.2f}" if rng.random() < 0.7 else None
        return self._add("buy", name, desc, p, brand.upper())

    def _family(self, k: int):
        brand, title, feats, price = self._product()
        base = self._base_code()
        sfx = self.rng.sample(self.suffixes, k)
        ids: list[tuple[str, str]] = []  # (abt rid, buy rid or None)
        for s in sfx:
            a = self._abt(brand, title, feats, price + self.rng.choice([0, 0.05, 1.0]), base + s, s)
            b = None
            if self.rng.random() < 0.6:
                b = self._buy(brand, title, feats, price, base + s, s, "exact")
                self.dups["family"].append((a, b))
            ids.append((a, b))
        for (a1, b1), (a2, b2) in __import__("itertools").combinations(ids, 2):
            self.variants["within"].append((a1, a2))
            for x, y in ((a1, b2), (a2, b1)):
                if y is not None:
                    self.variants["cross"].append((x, y))
        # a digit-changing sibling (base code differs): informational only
        if self.rng.random() < 0.3:
            base2 = self._base_code()
            a3 = self._abt(brand, title, feats, price, base2 + sfx[0], sfx[0])
            self.digit_variants.append((ids[0][0], a3))

    def _single(self, how: str | None):
        brand, title, feats, price = self._product()
        base = self._base_code()
        s = self.rng.choice(self.suffixes)
        a = self._abt(brand, title, feats, price, base + s, s)
        if how:
            b = self._buy(brand, title, feats, price, base + s, s, how)
            self.dups[how].append((a, b))

    def _filler(self, origin: str):
        brand, title, feats, price = self._product()
        base = self._base_code()
        s = self.rng.choice(self.suffixes)
        if origin == "abt":
            self._abt(brand, title, feats, price, base + s, s)
        else:
            self._buy(brand, title, feats, price, base + s, s, "exact")


def _scores(entity: str, recs: list[dict]):
    core = _resolve_core(entity, recs, None, None, None)
    sc = {frozenset((a, b)): s for a, b, s in core["scored_pairs"]}
    sc |= {frozenset((a, b)): s for a, b, _r1, _r2, s in core["borderline"]}
    return core, sc


@pytest.fixture(scope="module", params=SEEDS, ids=lambda s: f"seed{s}")
def shop(request):
    w = Shop(request.param)
    core, sc = _scores("product", w.recs)
    am, ax = core["auto_match"], core["auto_exclude"]
    get = lambda p: sc.get(frozenset(p), 0.0)  # noqa: E731
    out = {
        "seed": request.param, "n": len(w.recs), "am": am, "ax": ax, "world": w,
        "core": core, "get": get,
    }  # fmt: skip
    out["var_auto"] = {k: sum(get(p) >= am for p in v) for k, v in w.variants.items()}
    out["dup_auto"] = {k: sum(get(p) >= am for p in v) for k, v in w.dups.items()}
    out["dup_reach"] = {k: sum(get(p) >= ax for p in v) for k, v in w.dups.items()}
    out["dup_mean"] = {k: sum(get(p) for p in v) / len(v) for k, v in w.dups.items()}
    print(
        f"\n[PRE-D2 seed={request.param}] n={len(w.recs)} fitted={core['model_fitted']} "
        f"variant auto {out['var_auto']} of { {k: len(v) for k, v in w.variants.items()} }; "
        f"dups auto {out['dup_auto']} reach {out['dup_reach']} of "
        f"{ {k: len(v) for k, v in w.dups.items()} } mean { ({k: round(x, 3) for k, x in out['dup_mean'].items()}) }; "
        f"digit-variants auto {sum(get(p) >= am for p in w.digit_variants)}/{len(w.digit_variants)}"
    )
    return out


# ----------------------------------------------------------------------------- 1. the defect


def test_world_shape(shop):
    """The synthetic world is a real test: a fitted model, 300-600 records, enough pairs."""
    w = shop["world"]
    assert shop["core"]["model_fitted"]
    assert 300 <= shop["n"] <= 600
    assert len(w.variants["within"]) >= 40 and len(w.variants["cross"]) >= 40
    assert all(len(v) >= 25 for v in w.dups.values())
    assert {r["_origin_id"] for r in w.recs} == {"abt", "buy"}


def test_bars_are_unchanged(shop):
    """A fix may not buy precision by raising the auto bar or the exclude bar (sanctioned P15)."""
    assert shop["am"] == 0.95 and shop["ax"] == 0.70


@pytest.mark.parametrize("kind", ["within", "cross"])
def test_variants_never_auto_match(shop, kind):
    """Siblings that differ ONLY in the colour-code suffix (near-identical descriptions) never
    reach the auto bar, within one origin (the report's abt:10101 / abt:10102 shape) or across
    origins. Old code: ~40 % of the within-origin and ~35 % of the cross-origin pairs do."""
    w, get, am = shop["world"], shop["get"], shop["am"]
    bad = [(p, round(get(p), 3)) for p in w.variants[kind] if get(p) >= am]
    assert not bad, f"{len(bad)}/{len(w.variants[kind])} {kind} variant pairs auto-match: {bad[:2]}"


@pytest.mark.parametrize("kind", ["within", "cross"])
def test_variants_are_not_parked_just_under_the_bar(shop, kind):
    """Anti-clamp: a conflicting identifier is evidence, not a cap. No variant pair may sit within
    0.05 of the auto bar (test tolerance, the same margin as D5), so a fix that subtracts a
    constant or clamps to auto_match - eps fails. Old code: variants at 0.95-1.00."""
    w, get, am = shop["world"], shop["get"], shop["am"]
    worst = max(get(p) for p in w.variants[kind])
    assert worst < am - 0.05, worst


# -------------------------------------------------------------- 2. true duplicates stay (guards)

# Measured on HEAD (see HEAD above), per seed: pairs scored >= auto bar / >= exclude bar, and
# the mean score, per category. Category sizes: exact 28, split 28, oneside 28, family 45-55.
TODAY = {
    20261013: {
        "exact": (18, 18, 0.639), "split": (17, 19, 0.709), "oneside": (20, 22, 0.819),
        "family": (16, 26, 0.569),
    },
    7: {
        "exact": (24, 24, 0.834), "split": (19, 22, 0.778), "oneside": (20, 22, 0.813),
        "family": (16, 26, 0.555),
    },
    424242: {
        "exact": (24, 24, 0.834), "split": (23, 25, 0.900), "oneside": (20, 21, 0.782),
        "family": (14, 29, 0.520),
    },
}  # fmt: skip


def _tol(size: int) -> int:
    """Stated tolerance: 7 % of the category, rounded up (2 of 28, 4 of 55)."""
    return math.ceil(0.07 * size)


@pytest.mark.parametrize("cat", ["exact", "split", "family"])
def test_true_duplicates_keep_reaching_the_band(shop, cat):
    """Same product, same code (written the same way, split by a hyphen/space, or a family
    member's own copy on the other origin): the auto count and the band-or-auto count must not
    fall below today's by more than 7 % of the category."""
    today_auto, today_reach, _ = TODAY[shop["seed"]][cat]
    size = len(shop["world"].dups[cat])
    t = _tol(size)
    assert shop["dup_auto"][cat] >= today_auto - t, (shop["dup_auto"][cat], today_auto, size)
    assert shop["dup_reach"][cat] >= today_reach - t, (shop["dup_reach"][cat], today_reach, size)


def test_one_sided_code_is_not_penalised(shop):
    """A code on one record only is absence of evidence, not a conflict: those duplicates keep
    their auto and band counts (tol 7 %) and their mean score (tol 0.03)."""
    today_auto, today_reach, today_mean = TODAY[shop["seed"]]["oneside"]
    size = len(shop["world"].dups["oneside"])
    assert shop["dup_auto"]["oneside"] >= today_auto - _tol(size)
    assert shop["dup_reach"]["oneside"] >= today_reach - _tol(size)
    assert shop["dup_mean"]["oneside"] >= today_mean - 0.03, shop["dup_mean"]["oneside"]


def test_informational_digit_variants(shop):
    """Not asserted (beyond the report's evidence): siblings whose BASE code differs too."""
    w, get, am = shop["world"], shop["get"], shop["am"]
    print(f"[PRE-D2 info] digit-variant auto {sum(get(p) >= am for p in w.digit_variants)}"
          f"/{len(w.digit_variants)}")  # fmt: skip


def test_no_crash_on_empty_and_null_fields():
    """None / '' / whitespace names, codes of punctuation only: no exception, bars intact."""
    recs = []
    for i, name in enumerate([None, "", "  ", "-", "N/A", "x", "ab1", "AB-1", "ab 1"] * 3):
        rid = f"n{i}"
        recs.append({
            "id": rid, "name": name, "description": name, "price": None,
            "_origin_id": "abt" if i % 2 else "buy", "_origin_table": "public.product",
            "_origin_id_row": rid,
        })  # fmt: skip
    core, sc = _scores("product", recs)
    assert all(0.0 <= s <= 1.0 for s in sc.values())


# --------------------------------------------------------- 3. guards: persons and publications

_SYL = ["ba", "ko", "ri", "tu", "sen", "mo", "la", "vex", "dor", "pi", "nal", "qua", "zen", "fi"]


def _persons(seed: int, handles: bool = False):
    """200 people, 80 of them twice (typo in one copy, email re-cased), name + email + city,
    no model codes. With ``handles`` the email local part carries a number (jsmith87)."""
    rng = random.Random(seed)
    firsts = sorted({"".join(rng.choice(_SYL) for _ in range(2)).title() for _ in range(70)})
    lasts = sorted({"".join(rng.choice(_SYL) for _ in range(3)).title() for _ in range(90)})
    cities = sorted({"".join(rng.choice(_SYL) for _ in range(3)).title() for _ in range(25)})
    recs = []

    def rec(rid, name, email, city):
        return {
            "id": rid, "name": name, "email": email, "city": city, "_origin_id": "crm",
            "_origin_table": "public.person", "_origin_id_row": rid,
        }  # fmt: skip

    for i in range(200):
        f, la, c = rng.choice(firsts), rng.choice(lasts), rng.choice(cities)
        local = f"{f}.{la}".lower() + (str(rng.randint(10, 99)) if handles else "")
        email = f"{local}@{rng.choice(['mailer', 'postbox', 'netbox'])}.org"
        recs.append(rec(f"p{i}", f"{f} {la}", email, c))
        if i < 80:
            typo = f if i % 3 else f[:-1] + rng.choice("aeiou")
            recs.append(rec(f"q{i}", f"{typo} {la}", email.upper() if i % 2 else email, c))
    return recs


def _publications(seed: int):
    rng = random.Random(seed)
    vocab = sorted(
        {"".join(rng.choice(_SYL) for _ in range(rng.randint(2, 4))) for _ in range(500)}
    )
    recs = []
    for i in range(100):
        title = " ".join(rng.sample(vocab, rng.randint(5, 8))).capitalize()
        authors = [f"{rng.choice(vocab).title()} {rng.choice(vocab).title()}" for _ in range(3)]
        year = str(rng.randint(1990, 2002))
        for src, venue in (("acm", "ACM Trans. Database Syst."), ("dblp", "TODS")):
            shuffled = authors[::-1] if src == "dblp" else authors
            rid = f"{src}{i}"
            recs.append({
                "id": rid, "title": title if src == "acm" else title.title(),
                "authors": ", ".join(shuffled), "venue": venue, "year": year,
                "_origin_id": src, "_origin_table": "public.publication", "_origin_id_row": rid,
            })  # fmt: skip
    return recs


def _fingerprint(entity: str, recs: list[dict], twins: list[tuple[str, str]]):
    """(pairs scored, sum of scores, 12 evenly spaced order statistics, scores of 12 named
    true-duplicate pairs). Order statistics and sums do not depend on hash order."""
    core, sc = _scores(entity, recs)
    ordered = sorted(sc.values())
    quant = [round(ordered[(len(ordered) - 1) * i // 11], 9) for i in range(12)]
    by = {r["id"]: _record_id(r) for r in recs}
    named = [round(sc.get(frozenset((by[a], by[b])), -1.0), 9) for a, b in twins[:12]]
    return len(sc), round(sum(sc.values()), 6), quant, named


# Captured on HEAD 22685c3 (PYTHONHASHSEED-stable; checked with 0 and 1234). Tolerance 1e-6.
FROZEN = {
    "persons": (
        949, 86.956297,
        [1.22e-07] * 7 + [0.000496221] * 3 + [0.423297897, 0.9996],
        [0.9899, 0.9984, 0.9984, 0.9899, 0.9984, 0.9984, 0.9899, 0.9984, 0.9984, 0.9899, 0.9984, 0.9975],
    ),
    "persons_handles": (
        708, 86.795474,
        [3.6e-08] * 6 + [2.843e-06, 0.000184187, 0.000184187, 0.000219165, 0.998, 0.9998],
        [0.9998, 0.9986, 0.9986, 0.9986, 0.9986, 0.9986, 0.9998, 0.9986, 0.9986, 0.9998, 0.9986, 0.9986],
    ),
    "publications": (
        968, 99.9718,
        [0.0] * 10 + [0.9996, 0.9999],
        [0.9998, 0.9997, 0.9997, 0.9997, 0.9996, 0.9998, 0.9997, 0.9997, 0.9997, 0.9997, 0.9996, 0.9998],
    ),
}  # fmt: skip


@pytest.mark.parametrize(
    "name,build",
    [
        ("persons", lambda: (_persons(20261013), [(f"p{i}", f"q{i}") for i in range(12)])),
        (
            "persons_handles",
            lambda: (_persons(7, handles=True), [(f"p{i}", f"q{i}") for i in range(12)]),
        ),
        (
            "publications",
            lambda: (_publications(424242), [(f"acm{i}", f"dblp{i}") for i in range(12)]),
        ),
    ],
)
def test_guard_scores_unchanged_where_no_model_codes(name, build):
    """Persons (name+email+city), persons with numeric email handles, and publications carry no
    model codes: every score is frozen to the value on HEAD (tolerance 1e-6)."""
    entity = {"persons": "person", "persons_handles": "person", "publications": "publication"}[name]
    recs, twins = build()
    n, total, quant, named = _fingerprint(entity, recs, twins)
    f_n, f_total, f_quant, f_named = FROZEN[name]
    assert n == f_n
    assert total == pytest.approx(f_total, abs=1e-6 * max(1, n))
    assert quant == pytest.approx(f_quant, abs=1e-6)
    assert named == pytest.approx(f_named, abs=1e-6)


# ------------------------------------------------------------ 4. plan 2.4: no corpus literals

SRC_FILES = [
    ROOT / "src/mcp_server/tools/fellegi_sunter.py",
    ROOT / "src/mcp_server/tools/resolve_entities.py",
]
BANNED = ("omnimount", "sanus", "sony", "abt", "buy", "amazon", "google", "wlbk", "wlwh")


def _strings_and_names(path: Path):
    """String constants (docstrings and comments excluded) and every identifier of a module."""
    tree = ast.parse(path.read_text())
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                doc_ids.add(id(first.value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in doc_ids:
                out.append(("str", node.value))
        elif isinstance(node, ast.Name):
            out.append(("name", node.id))
        elif isinstance(node, ast.Attribute):
            out.append(("attr", node.attr))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.append(("def", node.name))
        elif isinstance(node, ast.arg):
            out.append(("arg", node.arg))
        elif isinstance(node, ast.keyword) and node.arg:
            out.append(("kw", node.arg))
    return out


@pytest.mark.parametrize("path", SRC_FILES, ids=lambda p: p.name)
def test_no_corpus_words_in_source(path):
    """Plan 2.4 grep as an AST check. Matched as a whole alphanumeric word inside a string or
    inside an identifier part ('abt' must not trip on 'abstract'). Numeric constants cannot be
    grepped: attack mode must ask 'would a corpus count produce this?' of every new number."""
    import re

    hits = []
    for kind, text in _strings_and_names(path):
        parts = re.split(r"[^a-z0-9]+|_", text.lower())
        wordlike = set(parts)
        hits += [(kind, text) for b in BANNED if b in wordlike]
        hits += [(kind, text) for b in ("omnimount", "wlbk", "wlwh") if b in text.lower()]
    assert not hits, hits


# --------------------------------------------------------- 5. optional: the real Abt-Buy corpus

# Measured on HEAD with the public dataset (Abt.csv, Buy.csv, abt_buy_perfectMapping.csv, latin-1;
# ~18 s). truth = the 1,097 perfect-mapping pairs; every other auto-matched pair is a false merge.
REAL_TODAY = {
    "variant": 0.9588, "auto": 940, "true_auto": 4, "false_auto": 936, "true_reach": 36,
}  # fmt: skip


def _load_real(d: Path):
    def load(name: str, origin: str):
        with open(d / name, encoding="latin-1", newline="") as f:
            rows = list(csv.DictReader(f))
        out = []
        for r in rows:
            rec = {k: (v if v != "" else None) for k, v in r.items()}
            rec.update(_origin_id=origin, _origin_table="public.product", _origin_id_row=r["id"])
            out.append(rec)
        return out

    recs = load("Abt.csv", "abt") + load("Buy.csv", "buy")
    with open(d / "abt_buy_perfectMapping.csv", encoding="latin-1", newline="") as f:
        truth = {
            frozenset((f"abt:public.product:{r['idAbt']}", f"buy:public.product:{r['idBuy']}"))
            for r in csv.DictReader(f)
        }
    return recs, truth


@pytest.fixture(scope="module")
def real():
    d = os.environ.get("ABT_BUY_DIR")
    if not d or not (Path(d) / "Abt.csv").exists():
        pytest.skip("set ABT_BUY_DIR to the unpacked Leipzig Abt-Buy dataset (about 20 s)")
    recs, truth = _load_real(Path(d))
    core, sc = _scores("product", recs)
    am, ax = core["auto_match"], core["auto_exclude"]
    auto = {p for p, s in sc.items() if s >= am}
    reach = {p for p, s in sc.items() if s >= ax}
    out = {
        "variant": sc.get(
            frozenset(("abt:public.product:10101", "abt:public.product:10102")), 0.0
        ),
        "auto": len(auto), "true_auto": len(auto & truth), "false_auto": len(auto - truth),
        "true_reach": len(reach & truth), "am": am,
    }  # fmt: skip
    print(f"\n[PRE-D2 real] today {REAL_TODAY} now {out}")
    return out


def test_real_variant_pair_is_below_the_auto_bar(real):
    """abt:10101 'Omnimount ... 20WLBK' vs abt:10102 '... 20WLWH'. Old code: 0.9588."""
    assert real["variant"] < real["am"], real["variant"]


def test_real_true_matches_do_not_drop(real):
    """Auto-matched TRUE pairs (perfectMapping) never fall (today 4); true pairs at or over the
    exclude bar fall by at most 2 (today 36, 5 %)."""
    assert real["true_auto"] >= REAL_TODAY["true_auto"]
    assert real["true_reach"] >= REAL_TODAY["true_reach"] - 2


def test_real_false_auto_merges_drop(real):
    """Auto pairs absent from the truth (today 936, 769 of them with differing letter+digit
    codes in the two names): at most half remain. A fix that clears only the one report pair
    fails."""
    assert real["false_auto"] <= REAL_TODAY["false_auto"] // 2, real["false_auto"]
