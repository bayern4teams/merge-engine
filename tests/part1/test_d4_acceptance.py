"""D4 acceptance (LOCAL_VERIFY_REPORT D4 / P16): person names are read from ONE column.

Derived from the report's Repro / Root cause / Fix shape / Verify text only, before any fix exists
(BASE = HEAD 893da8f).  Defect: ``load_data.py`` (``build_reconciliation_report`` and
``_entity_measures``) takes each record's name from ``_best_name_field`` -- ``given_name`` when
filled, else ``surname`` -- so a swapped, missing or substituted given name looks like two people and
the "label-free" flag fires on 18-39 % of CORRECT person clusters (Febrl4: 888 of 4,989, none wrong).

The checks go through the two real call sites (never a helper name the fix might introduce):
  * ``build_reconciliation_report(state, {}, [])["entities"][e]["quality"]``  (flagged ids + count)
  * ``_entity_measures(state, e)["flagged_clusters"]``  (what the pre-load gate P10 reads)
and assert both agree.  ``quality_signals`` is also called directly (the containment rule).

Targets-the-defect (fail on the old code): test_a_*.   Regression guards (pass on the old code and
must keep passing): test_b_*, test_c_*, test_d_*, test_e_* and the "same"/"typo" kinds of test_a_kind.
Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d4_acceptance.py -q -p no:cacheprovider
"""

from __future__ import annotations

import ast
import json
import random
import re
import subprocess
from pathlib import Path

import pytest

from src.mcp_server.tools.cluster_quality import quality_signals
from src.mcp_server.tools.resolve_entities import _best_name_field, _record_id
from src.orchestrator.nodes import load_data as L

ROOT = Path(__file__).resolve().parents[2]
BASE_REV = "893da8fbc61d2b21eee85f400cf2b58af7b63ef7"  # HEAD when these checks were derived
FIXTURE = Path(__file__).parent / "live_repros" / "p16_person_name_flags.json"
CHANGED = ("src/orchestrator/nodes/load_data.py", "src/mcp_server/tools/cluster_quality.py")
SEEDS = (20261013, 7, 424242)


# ── harness ───────────────────────────────────────────────────────────────────────────────
def state_of(records, clusters, entity="person"):
    return {
        "extracted_data": {entity: records},
        "entity_clusters": {entity: clusters},
        "field_mappings": {},
        "unmapped_fields": {},
    }


def flags(records, clusters, entity="person"):
    """-> (set of flagged cluster ids).  Both call sites must agree on the count."""
    st = state_of(records, clusters, entity)
    q = L.build_reconciliation_report(st, {}, [])["entities"][entity]["quality"]
    gate = L._entity_measures(st, entity)[L._FLAGGED]
    assert q["flagged_count"] == gate == len(q["flagged_clusters"]), (q["flagged_count"], gate)
    json.dumps(q, allow_nan=False)
    return set(q["flagged_clusters"])


def rec(origin, rid, **cols):
    return {"_origin_id": origin, "_origin_table": "public.t", "_origin_id_row": rid, **cols}


# ── a seeded, synthetic, Febrl-like person world (no corpus words: random syllables) ───────
_C = "bcdfghjklmnprstvwz"
_V = "aeiou"


def _word(rng, lo=2, hi=4):
    return "".join(rng.choice(_C) + rng.choice(_V) for _ in range(rng.randrange(lo, hi + 1)))


def _typo(rng, w):
    """One substitution inside the word (a Febrl-style keying error)."""
    i = rng.randrange(1, len(w) - 1)
    return w[:i] + rng.choice([c for c in _V + _C if c != w[i]]) + w[i + 1 :]


KINDS = (
    "same",
    "typo_surname",
    "swap",
    "swap_typo",
    "missing_given",
    "substituted_given",
    "abbreviated_given",
    "fused",
)
SAFE_FOR_PLANTED = ("same", "swap", "missing_given", "fused")


def _variant(rng, kind, g, s, pool, G, S):
    """-> dup's (given, surname) under ``kind``.  G/S are the column names."""
    if kind == "same":
        return {G: g, S: s}
    if kind == "typo_surname":
        return {G: g, S: _typo(rng, s)}
    if kind == "swap":
        return {G: s, S: g}
    if kind == "swap_typo":
        return {G: _typo(rng, s), S: g}
    if kind == "missing_given":
        return {G: None, S: s}
    if kind == "substituted_given":
        return {G: rng.choice([x for x in pool if x != g]), S: s}
    if kind == "abbreviated_given":
        return {G: g[0], S: s}
    if kind == "fused":  # the whole name keyed into one box
        return {G: f"{g} {s}", S: None}
    raise AssertionError(kind)


def person_world(seed, kinds, n=160, planted=0, cols=("given_name", "surname")):
    """-> (records, clusters, planted_ids).  ``n`` true two-record clusters (original in origin fa,
    duplicate in origin fb) whose duplicate is perturbed by ``kinds`` (cycled), plus ``planted``
    over-merges in the report's plant_febrl4 shape: the clusters of two DIFFERENT people (different
    given AND surname) joined by a bridge record carrying A's name and B's other fields."""
    G, S = cols
    rng = random.Random(seed)
    pool = sorted({_word(rng, 2, 3) for _ in range(90)})
    surnames: set[str] = set()
    people = []
    for i in range(n + 2 * planted):
        while True:
            s = _word(rng, 2, 4)
            if s not in surnames:
                surnames.add(s)
                break
        people.append((pool[rng.randrange(len(pool))], s))
    records, clusters, bad = [], [], set()

    def person(i, kind):
        g, s = people[i]
        extra = {"suburb": _word(rng, 2, 3), "date_of_birth": str(19200101 + rng.randrange(80000))}
        a = rec("fa", f"rec-{i}-org", **{G: g, S: s}, **extra)
        b = rec("fb", f"rec-{i}-dup-0", **_variant(rng, kind, g, s, pool, G, S), **extra)
        return a, b

    for i in range(n):
        a, b = person(i, kinds[i % len(kinds)])
        records += [a, b]
        clusters.append({"cluster_id": f"c{i}", "record_ids": [_record_id(a), _record_id(b)]})
    for k in range(planted):
        i, j = n + 2 * k, n + 2 * k + 1
        if people[i][0] == people[j][0]:  # different given AND surname, by construction
            people[j] = (pool[(pool.index(people[j][0]) + 1) % len(pool)], people[j][1])
        pk = SAFE_FOR_PLANTED
        a, a2 = person(i, pk[k % len(pk)])
        b, b2 = person(j, pk[(k + 2) % len(pk)])
        bridge = rec(
            "fb", f"bridge-{k}", **{G: people[i][0], S: people[i][1]}, suburb=b["suburb"],
            date_of_birth=a["date_of_birth"],
        )  # fmt: skip
        members = [a, a2, b, b2, bridge]
        records += members
        cid = f"p{k}"
        clusters.append({"cluster_id": cid, "record_ids": [_record_id(r) for r in members]})
        bad.add(cid)
    return records, clusters, bad


# ══ a. the defect: correct person clusters must not be flagged ════════════════════════════
def test_a_repro_fixture_true_pairs_are_not_flagged():
    """The report's 30 true Febrl4 pairs (live run flagged 14).  c8 = ('jack','moscatt') vs
    (None,'schmick'): the surname itself was substituted AND the given name is missing, so no
    name token survives -- not recoverable from names alone; the report says "≈ 0", so exactly
    this one cluster is tolerated and nothing else (the strict xfail repro wants 0)."""
    fx = json.loads(FIXTURE.read_text())
    got = flags(fx["records"], fx["clusters"])
    assert got <= {"c8"}, sorted(got - {"c8"})
    # swapped / missing / substituted given names, the report's three named causes, by cluster id
    assert not got & {
        "c0",
        "c1",
        "c2",
        "c3",
        "c4",
        "c5",
        "c6",
        "c7",
        "c9",
        "c10",
        "c11",
        "c12",
        "c13",
    }


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS[:2])
def test_a_kind_every_duplicate_perturbed_one_way_is_not_flagged(kind, seed):
    recs, clusters, _ = person_world(seed, [kind])
    got = flags(recs, clusters)
    assert not got, f"{kind}: {len(got)}/{len(clusters)} true person clusters flagged"


@pytest.mark.parametrize("seed", SEEDS)
def test_a_mixed_kinds_flag_none(seed):
    recs, clusters, _ = person_world(seed, KINDS, n=320)
    assert flags(recs, clusters) == set()


@pytest.mark.parametrize("cols", [("first_name", "last_name"), ("forename", "family_name")])
def test_a_other_column_names_behave_the_same(cols):
    """Column names are hints, not literals: a different pair of name columns, same verdict."""
    recs, clusters, _ = person_world(SEEDS[1], KINDS, n=240, cols=cols)
    assert flags(recs, clusters) == set()


def test_b_blank_and_missing_names_neither_crash_nor_flag():
    recs, clusters, _ = person_world(SEEDS[0], ["same", "typo_surname"], n=40)
    for r in recs[:10]:  # first five clusters lose every name column
        r["given_name"] = r["surname"] = None
    for r in recs[10:14]:
        r["given_name"], r["surname"] = "", "  "
    assert flags(recs, clusters) == set()


# ══ b. real over-merges are still flagged (plant_febrl4 shape) ═══════════════════════════
@pytest.mark.parametrize("seed", SEEDS)
def test_b_planted_over_merges_are_flagged_20_of_20(seed):
    recs, clusters, bad = person_world(seed, KINDS, n=300, planted=20)
    assert len(bad) == 20
    got = flags(recs, clusters)
    assert bad <= got, f"missed {sorted(bad - got)}"


@pytest.mark.parametrize("seed", SEEDS)
def test_b_flags_are_exactly_the_planted_clusters(seed):
    """The Verify line: flagged goes from 'many false' to the real ones only."""
    recs, clusters, bad = person_world(seed, KINDS, n=300, planted=20)
    assert flags(recs, clusters) == bad


def test_b_planted_with_other_column_names_still_flagged():
    recs, clusters, bad = person_world(
        SEEDS[1], KINDS, n=300, planted=20, cols=("first_name", "last_name")
    )
    assert bad <= flags(recs, clusters)


def test_b_planted_over_merge_with_an_abbreviated_duplicate_is_flagged():
    """A's duplicate keyed as an initial ('g', 'fihinala'): the initial must not excuse two
    different people (a single letter is a substring of almost any name)."""
    recs, clusters, bad = person_world(SEEDS[1], KINDS, n=300, planted=20)
    by_id = {_record_id(r): r for r in recs}
    for c in clusters:
        if c["cluster_id"] in bad:  # replace B's duplicate-free side: add an initial of A's given
            a = by_id[c["record_ids"][0]]
            ab = dict(
                a, _origin_id_row=a["_origin_id_row"] + "-init", given_name=a["given_name"][0]
            )
            recs.append(ab)
            c["record_ids"].append(_record_id(ab))
    assert bad <= flags(recs, clusters)


def test_b_planted_without_any_true_cluster_around_is_not_a_crash():
    """Fewer than two named clusters = no reference: nothing is asserted about the flag, only no error."""
    recs, clusters, _ = person_world(SEEDS[0], ["same"], n=0, planted=1)
    flags(recs, clusters)


# ══ c. organisations (one name column): flags frozen at BASE ═════════════════════════════
def org_world(seed, extra_hint_columns=False):
    """Two origins naming the same concept differently (``company_name`` / ``org_title``), 60
    organisations of 2-4 records each (case, legal form, dropped letter), 8 of them fused with an
    unrelated organisation (a wrong merge).  -> (records, clusters, fused_ids)."""
    rng = random.Random(seed)
    forms = ["", " ltd", " inc", " gmbh", " holdings"]
    records, clusters, fused = [], [], set()
    orgs = [_word(rng, 2, 4) + " " + _word(rng, 1, 3) for _ in range(68)]
    made = []
    for i, base in enumerate(orgs):
        members = []
        for j in range(2 + i % 3):
            nm = base if j == 0 else base.upper() if j == 1 else base.replace(" ", "")[: -1 or None]
            nm += forms[(i + j) % len(forms)]
            cols = {"company_name": nm} if j % 2 == 0 else {"org_title": nm}
            if extra_hint_columns:  # columns that match a name hint but are not the display name
                cols |= {
                    "company_id": f"C{rng.randrange(10**6)}",
                    "email": f"{_word(rng, 2, 2)}@{_word(rng, 2, 2)}.example",
                    "organization_type": rng.choice(["private", "public", "charity"]),
                }
            members.append(rec("pg" if j % 2 == 0 else "ch", f"o{i}-{j}", **cols))
        made.append(members)
    for i in range(60):
        members = made[i]
        if i < 8:
            members = members + made[60 + i]
            fused.add(f"o{i}")
        records += members
        clusters.append({"cluster_id": f"o{i}", "record_ids": [_record_id(r) for r in members]})
    return records, clusters, fused


# Frozen from the unchanged tree at BASE_REV (see .handoff/D4.acceptance.md for how to regenerate).
ORG_FROZEN = {
    (SEEDS[0], False): {f"o{i}" for i in (0, 1, 2, 3, 4, 5, 6, 7, 20, 22, 31, 38, 47, 50, 52)},
    (SEEDS[0], True): {f"o{i}" for i in (0, 1, 2, 3, 4, 5, 6, 7, 20, 22, 31, 38, 47, 50, 52)},
    (SEEDS[1], False): {f"o{i}" for i in (0, 1, 2, 3, 4, 5, 6, 7, 11, 23, 31, 56)},
}


@pytest.mark.parametrize("seed,extra", sorted(ORG_FROZEN))
def test_c_org_flags_equal_the_flags_at_base(seed, extra):
    """Single name column per record (company_name / org_title): the person fix must not change
    which organisation clusters are flagged.  ``extra`` adds company_id / email / organization_type
    columns that match a name HINT but are not names: a fix that reads "all hint columns"
    naively would fold them into the display name and move the flags."""
    recs, clusters, fused = org_world(seed, extra)
    got = flags(recs, clusters, "company")
    frozen = ORG_FROZEN[(seed, extra)]
    assert got == frozen, (sorted(got ^ frozen),)
    assert got  # the freeze is not vacuous
    assert got & fused  # and it contains real over-merges


def test_c_org_name_choice_is_still_one_column_when_only_one_is_a_name():
    r = rec("pg", "1", company_name="acme", company_id="C1", email="a@b.example")
    assert _best_name_field(r) == "company_name"


# ══ d. the containment rule is kept ══════════════════════════════════════════════════════
def _coherent_names(seed, n=14):
    rng = random.Random(seed)
    names, clusters = {}, []
    for i in range(n):
        base = _word(rng, 2, 4) + " " + _word(rng, 2, 3)
        ids = [f"x{i}-{j}" for j in range(3)]
        for j, rid in enumerate(ids):
            names[rid] = (base, base.upper(), base + " ltd")[j]
        clusters.append({"cluster_id": f"k{i}", "record_ids": ids})
    return names, clusters


def test_d_quality_signals_containment_and_unrelated_split():
    """Direct ``quality_signals``: one name contained in another (no shared token: an unspaced
    ticker inside the long form) is one entity; two unrelated names are two."""
    names, clusters = _coherent_names(3)
    names |= {"t-0": "bhp", "t-1": "bhpbilliton", "u-0": "zq wy plox", "u-1": "marvelous ducks"}
    clusters += [
        {"cluster_id": "contained", "record_ids": ["t-0", "t-1"]},
        {"cluster_id": "unrelated", "record_ids": ["u-0", "u-1"]},
    ]
    q = quality_signals(clusters, names)
    assert "unrelated" in q["flagged_clusters"]
    assert "contained" not in q["flagged_clusters"]
    assert not {c["cluster_id"] for c in clusters[:14]} & set(q["flagged_clusters"])


def test_d_containment_through_the_loader_path():
    recs, clusters, _ = org_world(SEEDS[2])
    recs += [rec("pg", "t-0", company_name="bhp"), rec("ch", "t-1", org_title="bhpbilliton")]
    clusters.append({"cluster_id": "contained", "record_ids": [_record_id(r) for r in recs[-2:]]})
    assert "contained" not in flags(recs, clusters, "company")


def test_a_person_token_containment_survives():
    """A cluster whose records are nested keyings of one person ('pringle' within 'xanthe pringle'
    within 'pringle xanthe') stays unflagged next to a coherent crowd."""
    recs, clusters, _ = person_world(SEEDS[0], ["same"], n=40)
    nested = [
        rec("fa", "n0", given_name="xanthe", surname="pringle"),
        rec("fb", "n1", given_name=None, surname="pringle"),
        rec("fc", "n2", given_name="pringle", surname="xanthe"),
    ]
    clusters.append({"cluster_id": "nested", "record_ids": [_record_id(r) for r in nested]})
    assert "nested" not in flags(recs + nested, clusters)


# ══ e. plan 2.4: no corpus literals in the lines this change adds ═════════════════════════
PERSON_ROOTS = ("given", "surname", "febrl", "first_name", "last_name", "firstname", "lastname",
                "forename", "family_name", "fname", "lname")  # fmt: skip
CORPUS_EXACT = {"france", "us", "sas", "berhad", "global", "group", "holdings", "companyname",
                "main_entity_id", "company_relation", "staff", "testdiffbot", "org_title",
                "pagila", "public.person", "person"}  # fmt: skip


def _docstring_lines(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out |= set(range(body[0].lineno, body[0].end_lineno + 1))
    return out


def corpus_literals_added(old_src: str, new_src: str) -> list[str]:
    """String constants (docstrings and comments excluded) on lines that differ from ``old_src``
    and look like corpus words.  A literal already present in ``old_src`` is baseline."""
    old_lits = {
        n.value
        for n in ast.walk(ast.parse(old_src))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    tree = ast.parse(new_src)
    skip = _docstring_lines(tree)
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.lineno not in skip:
            v = n.value.lower()
            if n.value in old_lits:
                continue
            if v in CORPUS_EXACT or any(r in v for r in PERSON_ROOTS):
                hits.append(f"line {n.lineno}: {n.value!r}")
    return hits


def test_e_the_grep_catches_a_corpus_literal():
    """Negative case: the checker itself fails on the fix the plan forbids."""
    old = "HINTS = ('name',)\n"
    assert corpus_literals_added(old, old + "PERSON = ('given_name', 'surname')\n")
    assert corpus_literals_added(old, old + "x = 'Berhad'\n")
    assert corpus_literals_added(old, old + "def f():\n    return k.endswith('surname')\n")
    assert not corpus_literals_added(
        old, old + 'def f():\n    """about given_name"""\n    # surname\n'
    )
    assert not corpus_literals_added(
        "A='given_name'\n", "A='given_name'\nB=A\n"
    )  # baseline literal


@pytest.mark.parametrize("path", CHANGED)
def test_e_no_new_corpus_literals_vs_base(path):
    old = subprocess.run(
        ["git", "show", f"{BASE_REV}:{path}"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    new = (ROOT / path).read_text()
    hits = corpus_literals_added(old, new)
    assert not hits, hits


@pytest.mark.parametrize("path", CHANGED)
def test_e_new_numeric_constants_are_listed_for_the_attacker(path, capsys):
    """Not a pass/fail on its own: numbers cannot be grepped for 'corpus-ness'.  Prints every numeric
    literal on a line the diff vs BASE adds, other than 0/1/2, for attack mode to ask 'would a count
    over the corpus produce this?'.  Fails only on a float that looks tuned (more than 2 decimals)."""
    diff = subprocess.run(
        ["git", "diff", "-U0", BASE_REV, "--", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    nums = []
    for ln in added:
        code = re.sub(r"#.*", "", ln)
        nums += [
            m
            for m in re.findall(r"(?<![\w.])\d+\.\d+|(?<![\w.])\d+(?![\w.])", code)
            if m not in {"0", "1", "2"}
        ]
    print(f"[D4 added numeric literals in {path}]: {nums}")
    assert not [m for m in nums if "." in m and len(m.split(".")[1]) > 2], nums


def test_e_repro_uses_best_name_field_still_importable():
    """The strict-xfail repro imports ``_best_name_field``/``_record_id``; keep them (the test
    file for the fix must not be broken by a rename)."""
    assert callable(_best_name_field) and callable(_record_id)
