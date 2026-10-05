"""P5 acceptance (derived from the report's Evidence + Fix text only; no P5 code seen).

Report P5: schema-agnostic, zero-label blocking. Token blocking (every token a key,
attribute names never read) -> purge -> filter -> meta-blocking WNP / CEP (K = sum|b|/2)
/ CNP (k = sum|b|/(|E1|+|E2|)); every constant computed from the block collection.
Reference expectations (NEVER tuned to): PC 0.96-0.99, RR 0.76-0.90.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p5_acceptance.py -q -s -p no:cacheprovider
(-s prints the [P5] PC / RR / memory lines.)

ADAPTATION POINTS (retarget here, not in the tests):
  FUNC                    entry point under test (records -> pairs, stats=...)
  PC_FLOOR / RR_FLOOR     "no regression" floors, derived from the report text:
                          PC 0.90 = the R1 general baseline ">90% recall"; RR 0.76 = the LOWEST
                          reduction ratio the report reports. Floors, not targets; numbers are printed.
  PROVENANCE_PREFIX       keys starting with it are engine provenance (_origin_id ...), not data
                          columns; the only attribute-name inspection tolerated (startswith).
  VOCAB_ALLOWED           module-level string collections the new blocking path may reference (none).
  LEGACY_NAME_HELPERS     old name-hint helpers the new path must not reach.
  SMALL_NUMBERS           structural literals allowed inside the blocking path (0, 1, 2, 0.5).
"""

from __future__ import annotations

import ast
import os
import random
import re
import tracemalloc
from pathlib import Path

import pytest

os.environ.setdefault("MERGE_ENGINE_DISABLE_RESOLVE_CACHE", "1")

from src.mcp_server.tools import resolve_entities as re_
from tests.part1 import fixtures_matching as fx
from tests.part1.scorer import pairs_of

SRC = Path(re_.__file__)

# -- adaptation points --------------------------------------------------------
FUNC = "_generate_candidate_pairs"
PC_FLOOR = 0.90
RR_FLOOR = 0.76
PROVENANCE_PREFIX = "_"
VOCAB_ALLOWED: set[str] = set()
LEGACY_NAME_HELPERS = {
    "_blockable_fields",
    "_pick_blocking_field",
    "_best_name_field",
    "_ref_fields",
}
SMALL_NUMBERS = {0, 1, 2, 0.5}


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def run(records: list[dict]) -> tuple[list, dict, set[tuple[str, str]]]:
    stats: dict = {}
    pairs = getattr(re_, FUNC)(records, stats=stats)
    return pairs, stats, {_pair(p[0], p[1]) for p in pairs}


def _data_cols(rec: dict) -> list[str]:
    return [k for k in rec if not k.startswith(PROVENANCE_PREFIX)]


def _token_pairs(records: list[dict]) -> set[tuple[str, str]]:
    """Independent oracle: pairs sharing >=1 alphanumeric token in ANY column."""
    by_tok: dict[str, list[str]] = {}
    for r in records:
        toks = {t for k in _data_cols(r) for t in re.findall(r"[a-z0-9]+", str(r[k]).lower())}
        for t in toks:
            by_tok.setdefault(t, []).append(fx.rid(r))
    out = set()
    for ids in by_tok.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                out.add(_pair(a, b))
    return out


def pc_rr(got: set, planted: set, n: int) -> tuple[float, float]:
    total = n * (n - 1) // 2
    return len(planted & got) / len(planted), 1 - len(got) / total


# -- fixtures ------------------------------------------------------------------
COLS = ("full_name", "employer", "city", "phone")


def planted_mixed(n_entities: int = 450, n_dups: int = 50, seed: int = 5) -> dict:
    """Four columns, common first names / surnames / cities (a realistic high-frequency
    token mix), a unique phone number, two-token employers. Each planted duplicate sits in the
    other origin with exactly ONE of the four columns perturbed (typo / different value), so
    it still shares tokens elsewhere -- the premise token blocking rests on."""
    rng = random.Random(seed)
    pool = fx._tokens(rng, 700)
    firsts, lasts, cities, emps = pool[:40], pool[40:190], pool[190:215], pool[215:]
    rows = []
    for i in range(n_entities):
        e = {
            "full_name": f"{rng.choice(firsts)} {rng.choice(lasts)}",
            "employer": f"{rng.choice(emps)} {rng.choice(emps)}",
            "city": rng.choice(cities),
            "phone": f"5550{i:05d}",
        }
        g = f"g{i}" if i < n_dups else None
        rows.append(("src_a", e, g))
        if g:
            d, col = dict(e), COLS[i % 4]
            if col == "full_name":
                f, ln = d[col].split()
                d[col] = f"{f} {ln[:1]}x{ln[2:]}"
            elif col == "employer":
                a, b = d[col].split()
                d[col] = f"{a[:1]}x{a[2:]} {b}"
            elif col == "city":
                d[col] = rng.choice(cities)
            else:
                d[col] = d[col][:-1] + "x"
            rows.append(("src_b", d, g))
    rng.shuffle(rows)
    records, truth = [], {}
    for j, (src, e, g) in enumerate(rows):
        rec = fx._rec(src, str(j), **e)
        records.append(rec)
        if g:
            truth.setdefault(g, []).append(fx.rid(rec))
    return {"records": records, "truth": sorted(truth.values())}


def transform(
    records: list[dict], mapping: dict[str, str], order: list[str], *, per_record: int | None = None
) -> list[dict]:
    """Rename data columns via ``mapping`` and emit them in ``order`` (the same for every
    record, or shuffled per record with seed ``per_record``). Provenance keys are untouched."""
    rng = random.Random(per_record)
    out = []
    for r in records:
        cols = list(order)
        if per_record is not None:
            rng.shuffle(cols)
        new = {k: v for k, v in r.items() if k.startswith(PROVENANCE_PREFIX)}
        for c in cols:
            new[mapping[c]] = r[c]
        out.append(new)
    return out


@pytest.fixture(scope="module")
def mixed() -> dict:
    f = planted_mixed()
    pairs, stats, got = run(f["records"])
    f.update(pairs=pairs, stats=stats, got=got, planted=pairs_of(f["truth"]))
    return f


# 1 -- PROPERTY: renaming / reordering columns leaves the candidate set unchanged ---------
RENAMES = {
    "opaque": {"full_name": "c0", "employer": "c1", "city": "c2", "phone": "c3"},
    # every column wears ANOTHER column's hint-looking name (a name-reading blocker is misled)
    "swapped_hints": {
        "full_name": "city",
        "employer": "phone",
        "city": "full_name",
        "phone": "employer",
    },
    "hint_words": {
        "full_name": "xyz",
        "employer": "email",
        "city": "company_name",
        "phone": "website",
    },
}


@pytest.mark.parametrize("label", sorted(RENAMES))
def test_1_rename_and_reorder_leave_candidates_unchanged(mixed: dict, label: str) -> None:
    base = mixed["got"]
    for order in (list(COLS), list(reversed(COLS)), ["city", "phone", "full_name", "employer"]):
        _, _, got = run(transform(mixed["records"], RENAMES[label], order))
        assert got == base, (
            f"{label}/{order}: candidate set changed ({len(got)} vs {len(base)} pairs)"
        )


def test_1b_per_record_column_order_is_irrelevant(mixed: dict) -> None:
    _, _, got = run(transform(mixed["records"], RENAMES["opaque"], list(COLS), per_record=7))
    assert got == mixed["got"]


def test_1c_no_hint_vocabulary_is_consulted_dynamically(
    mixed: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank every module-level field-name hint list: a blocker that reads names changes."""
    for name in dir(re_):
        if name.endswith("_HINTS") and isinstance(getattr(re_, name), tuple):
            monkeypatch.setattr(re_, name, ())
    if hasattr(re_, "_PARTIAL_NAME_FIELDS"):
        monkeypatch.setattr(re_, "_PARTIAL_NAME_FIELDS", frozenset())
    _, _, got = run(mixed["records"])
    assert got == mixed["got"]


class _OpaqueKey(str):
    """A column name that explodes when anything but its prefix or identity is inspected."""

    def _boom(self, *a, **k):
        raise AssertionError(f"attribute name {str(self)!r} was inspected")

    lower = upper = casefold = title = find = rfind = index = count = _boom
    split = rsplit = partition = endswith = replace = strip = __contains__ = _boom


def test_1d_attribute_names_are_never_read(mixed: dict) -> None:
    recs = []
    for r in mixed["records"]:
        new = {k: v for k, v in r.items() if k.startswith(PROVENANCE_PREFIX)}
        new.update({_OpaqueKey(k): v for k, v in r.items() if not k.startswith(PROVENANCE_PREFIX)})
        recs.append(new)
    _, _, got = run(recs)
    assert got == mixed["got"]


# 2 -- planted duplicates retained; PC / RR reported against planted truth ---------------
@pytest.mark.parametrize("seed", [5, 6, 7])
def test_2_planted_pairs_retained_pc_rr_reported(seed: int) -> None:
    f = planted_mixed(seed=seed)
    pairs, stats, got = run(f["records"])
    planted = pairs_of(f["truth"])
    pc, rr = pc_rr(got, planted, len(f["records"]))
    print(
        f"\n[P5] seed={seed} n={len(f['records'])} planted={len(planted)} found={len(planted & got)} "
        f"PC={pc:.3f} RR={rr:.3f} candidates={len(got)} precision={len(planted & got) / len(got):.4f} "
        f"stats={stats}  (report reference PC 0.96-0.99, RR 0.76-0.90; floors {PC_FLOOR}/{RR_FLOOR})"
    )
    assert pc >= PC_FLOOR, f"PC {pc:.3f} below floor; lost {sorted(planted - got)[:3]}"
    assert rr >= RR_FLOOR, f"RR {rr:.3f} below floor"
    # independent oracle: every proposed pair shares a token (block building is token based),
    # and the engine's own counters agree with what it returned.
    assert got <= _token_pairs(f["records"]), "pairs proposed that share no token"
    assert stats.get("candidate_pairs") == len(pairs)
    assert stats.get("uncompared_pairs", 0) == 0


def test_2b_constants_scale_with_the_corpus() -> None:
    """A vocabulary-disjoint copy of the corpus appended to itself must not change what
    either half proposes: a constant that is a corpus count scales with the corpus, a fitted
    absolute one (a top-N, a fixed size cut-off) does not."""
    a = planted_mixed(seed=5)["records"]

    def tag(recs: list[dict], p: str) -> list[dict]:
        out = []
        for r in recs:
            new = dict(r)
            new["_origin_id_row"] = p + r["_origin_id_row"]
            for c in COLS:
                new[c] = " ".join(p + t for t in str(r[c]).split())
            out.append(new)
        return out

    b = tag(a, "z")
    _, _, ga = run(a)
    _, _, gb = run(b)
    _, _, gab = run(a + b)
    gb_ids = gb  # b's ids already differ from a's through the row prefix
    print(f"\n[P5] disjoint-union: |A|={len(ga)} |B|={len(gb)} |A+B|={len(gab)}")
    assert gab == ga | gb_ids, "constants are not computed from the block collection"


# 3 -- P2 + P5 together: no more comparisons than the pre-P5 uncapped count --------------
PRE_P5_UNCAPPED_P2_BIG = 800 * 799 // 2  # the one 'widget' block, nothing purged or pruned


def test_3_p2_big_block_no_more_comparisons_than_pre_p5_and_no_planted_loss() -> None:
    f = fx.p2_big_block()
    pairs, stats, got = run(f["records"])
    planted = pairs_of(f["truth"])
    pc, rr = pc_rr(got, planted, len(f["records"]))
    print(
        f"\n[P5] p2_big_block candidates={len(pairs)} (pre-P5 uncapped {PRE_P5_UNCAPPED_P2_BIG}) "
        f"PC={pc:.3f} RR={rr:.3f} stats={stats}"
    )
    assert len(pairs) <= PRE_P5_UNCAPPED_P2_BIG
    assert planted <= got, f"planted pairs lost: {sorted(planted - got)[:3]}"
    assert stats.get("uncompared_pairs", 0) == 0


def test_3b_p2_cleaning_still_applies_to_schema_agnostic_blocks() -> None:
    """Every record carries the token 'holdings' (one block of ~1.1M pairs under token
    blocking) plus one of 30 cities. P5 without P2's purging / meta-blocking would return it."""
    rng = random.Random(11)
    cities = fx._tokens(rng, 30)
    recs, truth = [], []
    for i in range(1500):
        recs.append(fx._rec("src_a", str(i), blob=f"holdings {rng.choice(cities)} k{i:05d}"))
    for i in range(60):  # planted: same city + same k-token, different column name and a typo token
        city, k = recs[i]["blob"].split()[1:]
        d = fx._rec("src_b", f"d{i}", other=f"holdings {city} {k} typo{i}")
        recs.append(d)
        truth.append([fx.rid(recs[i]), fx.rid(d)])
    pairs, stats, got = run(recs)
    planted = pairs_of(truth)
    pc, rr = pc_rr(got, planted, len(recs))
    print(
        f"\n[P5] holdings-corpus n={len(recs)} candidates={len(pairs)} PC={pc:.3f} RR={rr:.3f} stats={stats}"
    )
    assert pc >= PC_FLOOR and rr >= RR_FLOOR


def test_3c_p2_oom_guard_still_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(re_, "_MAX_CANDIDATE_PAIRS", 10)
    with pytest.raises(re_.CandidatePairBudgetExceeded):
        run(fx.p2_big_block()["records"])


# 4 -- field names differ across origins ----------------------------------------------------
def test_4_different_column_names_per_origin_still_block_true_pairs() -> None:
    rng = random.Random(41)
    toks = fx._tokens(rng, 1200)
    schema = {"A": ("company_name", "place"), "B": ("org_title", "town"), "C": ("xyz", "q9")}
    origins = list(schema)
    cities = toks[:15]
    recs, truth = [], {}
    for i in range(200):
        t1, t2 = toks[15 + 2 * i], toks[16 + 2 * i]
        city = rng.choice(cities)
        o1, o2 = origins[i % 3], origins[(i + 1) % 3]
        n2 = f"{t2} {t1} corp" if i % 3 == 0 else f"{t1} {t2}"
        r1 = fx._rec(o1, f"e{i}", **dict(zip(schema[o1], (f"{t1} {t2}", city), strict=True)))
        r2 = fx._rec(o2, f"e{i}", **dict(zip(schema[o2], (n2, city), strict=True)))
        recs += [r1, r2]
        truth[f"g{i}"] = [fx.rid(r1), fx.rid(r2)]
    for i in range(150):  # unmatched filler across the three schemas
        o = origins[i % 3]
        name = f"{toks[815 + 2 * i]} {toks[816 + 2 * i]}"
        recs.append(
            fx._rec(o, f"s{i}", **dict(zip(schema[o], (name, rng.choice(cities)), strict=True)))
        )
    _, stats, got = run(recs)
    src = fx.source_of(recs)
    strata: dict[frozenset, list[bool]] = {}
    for ids in truth.values():
        strata.setdefault(frozenset(src[x] for x in ids), []).append(_pair(*ids) in got)
    rates = {tuple(sorted(k)): sum(v) / len(v) for k, v in strata.items()}
    print(f"\n[P5] cross-origin PC by origin pair: {rates} stats={stats}")
    assert set(rates) == {("A", "B"), ("B", "C"), ("A", "C")}
    assert all(v >= PC_FLOOR for v in rates.values()), rates


# 5 -- memory peak ----------------------------------------------------------------------------
def test_5_memory_peak_is_measured_and_printed() -> None:
    peaks = {}
    for n in (250, 1000):
        recs = planted_mixed(n_entities=n - n // 10, n_dups=n // 10, seed=9)["records"]
        tracemalloc.start()
        try:
            run(recs)
            peaks[len(recs)] = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    print(
        "\n[P5] tracemalloc peak (whole blocking call, incl. block building): "
        + ", ".join(f"n={n}: {p / 1e6:.1f} MB ({p / n:.0f} B/record)" for n, p in peaks.items())
    )
    assert all(p < 1 << 30 for p in peaks.values()), "gross blow-up (>1 GiB at <=1.1k records)"


# 6 -- edge cases ---------------------------------------------------------------------------------
def test_6_empty_single_and_valueless_inputs_do_not_crash() -> None:
    assert run([])[0] == []
    assert run([fx._rec("s", "1", a="alpha beta")])[0] == []
    blanks = [fx._rec("s", str(i), a=None, b="", c=i) for i in range(4)]
    run(blanks)  # must not raise; whether blank records pair is not specified by the report


# 7 -- section 2.4: no field-name hints, no fitted constants ------------------------------------------
TREE = ast.parse(SRC.read_text(encoding="utf-8"))
FUNCS = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}


def _vocab_names() -> set[str]:
    """Module-level names bound to a collection of >=2 string literals (a vocabulary)."""
    out = set()
    for n in TREE.body:
        if isinstance(n, ast.Assign) and not isinstance(n.value, ast.Constant):
            strs = [
                c
                for c in ast.walk(n.value)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            ]
            if len(strs) >= 2:
                out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return out


def _reachable(root: str) -> set[str]:
    seen, todo = set(), [root]
    while todo:
        name = todo.pop()
        if name in seen or name not in FUNCS:
            continue
        seen.add(name)
        todo += [x.id for x in ast.walk(FUNCS[name]) if isinstance(x, ast.Name) and x.id in FUNCS]
    return seen


def test_7_blocking_path_consults_no_name_hint_list() -> None:
    vocab = _vocab_names() - VOCAB_ALLOWED
    bad = [
        (fn, x.id)
        for fn in sorted(_reachable(FUNC))
        for x in ast.walk(FUNCS[fn])
        if isinstance(x, ast.Name) and (x.id in vocab or x.id in LEGACY_NAME_HELPERS)
    ]
    assert not bad, f"field-name vocabulary reachable from {FUNC}: {sorted(set(bad))}"


# numeric literals present today (parent) in the functions reachable from FUNC: a baseline, not a licence
BASELINE_BODY_NUMBERS = {("_phonetic_key", v) for v in (3, 4, 5, 6)} | {("_domain_of", 4)}


def test_7b_no_fitted_numeric_constant_in_blocking_path() -> None:
    """Only structural numbers (0, 1, 2, 0.5) -- anything else (a purge ratio, a filter r, a
    top-N, a fixed threshold) is a constant a corpus count would not produce."""
    found = {
        (fn, x.value)
        for fn in _reachable(FUNC)
        for x in ast.walk(FUNCS[fn])
        if isinstance(x, ast.Constant)
        and type(x.value) in (int, float)
        and x.value not in SMALL_NUMBERS
    }
    new = sorted(found - BASELINE_BODY_NUMBERS, key=str)
    assert not new, f"non-structural numeric literals in the blocking path: {new}"


LITERALS = (
    "France", "US", "SAS", "Berhad", "Global", "Group", "Holdings", "companyname",
    "main_entity_id", "company_relation", "staff", "org_title",
)  # fmt: skip
BASELINE_HITS = {  # parent: docstring/comment text plus the `staff` synonym tuple (line 167)
    ("staff", '"employee_count": ("employee", "headcount", "staff", "size"),'),
    ("company_relation", "call by the caller (from the ``company_relation`` entity family's"),
    ("org_title", "differently (pg ``company_name`` vs ch ``org_title``) — a single global"),
    ("org_title", "concept differently (``company_name`` vs ``org_title``), and a key tied"),
    ("org_title", "``company_name`` and ``org_title`` both land in the ``name`` slot."),
    ("org_title", "# (e.g. ``company_name`` over ``org_title`` for ``name``)"),
    ("SAS", '("SE", "SAS", "Helicopters", ...) carry the discriminating weight instead.'),
    ("company_relation", "ids it asserts a relationship with, from the ``company_relation``"),
    ("company_relation", 'between a resolve_entities record and its company_relation rows."""'),
    ("org_title", "# `org_title` both mapped to the `name` slot) is a judgment call,"),
    ("org_title", "``org_title``), so common-field comparison alone can never match their"),
}


def test_7c_no_new_corpus_literals() -> None:
    """Rewriting blocking may DELETE baseline hits (the docstrings naming org_title go with the
    rewritten helpers) but must not add one."""
    out = set()
    for line in SRC.read_text(encoding="utf-8").splitlines():
        for w in LITERALS:
            if re.search(rf"(?<![A-Za-z0-9_]){w}(?![A-Za-z0-9_])", line):
                out.add((w, line.strip()))
    assert not sorted(out - BASELINE_HITS), sorted(out - BASELINE_HITS)
