"""D4 attack (adversarial; r1 = 883a02c, r2 = 5c5f857).  Strict xfail = a defect found that the
worker's / derive checks miss (each reason names it, with the measured numbers).  Controls (no xfail)
pin behaviour that must hold.  BASE (pre-D4) = 893da8f; "base" numbers in reasons were measured in a
throwaway worktree of it.  Sections 7-11 are the r2 attack.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d4_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json
import random

import pytest

from src.mcp_server.tools.cluster_quality import PART_SEP, quality_signals
from src.mcp_server.tools.resolve_entities import _record_id
from src.orchestrator.nodes import load_data as L

from .test_d4_acceptance import KINDS, _typo, _word, flags, person_world, rec, state_of


def _cl(recs_by_cluster):
    return [{"cluster_id": cid, "record_ids": [_record_id(r) for r in rs]}
            for cid, rs in recs_by_cluster.items()]  # fmt: skip


def _pop(seed, n=150, G="given_name", S="surname", common=0.3, spool=None):
    """n true two-record person clusters; ``common`` of the people share one given name; ``spool``
    limits surnames to that many distinct values (None = all distinct)."""
    rng = random.Random(seed)
    gp = sorted({_word(rng, 2, 3) for _ in range(60)})
    sp = sorted({_word(rng, 2, 4) for _ in range(spool)}) if spool else None
    used: set[str] = set()

    def sname():
        while True:
            s = rng.choice(sp) if sp else _word(rng, 2, 4)
            if sp or s not in used:
                used.add(s)
                return s

    def pick():
        return (gp[0] if rng.random() < common else rng.choice(gp)), sname()

    return rng, gp, sname, pick


def _true(pick, n, G="given_name", S="surname"):
    by = {}
    for i in range(n):
        g, s = pick()
        by[f"c{i}"] = [rec("fa", f"r{i}a", **{G: g, S: s}), rec("fb", f"r{i}b", **{G: g, S: s})]
    return by


def _planted_pairs(by, k, rng, gp, sname, share):
    """k planted clusters of two different people sharing exactly ``share`` ('given'|'surname'|None)."""
    bad = set()
    for p in range(k):
        g1, g2 = rng.sample(gp, 2)
        s1, s2 = sname(), sname()
        if share == "given":
            g2 = g1
        elif share == "surname":
            s2 = s1
        by[f"p{p}"] = [
            rec("fa", f"p{p}a", given_name=g1, surname=s1),
            rec("fb", f"p{p}b", given_name=g1, surname=s1),
            rec("fa", f"p{p}c", given_name=g2, surname=s2),
            rec("fb", f"p{p}d", given_name=g2, surname=s2),
        ]
        bad.add(f"p{p}")
    return bad


def _flat(by):
    return [r for rs in by.values() for r in rs], _cl(by)


# ══ 1. over-linking: planted over-merges that share ONE whole part ═══════════════════════
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_control_planted_sharing_nothing_flagged(seed):
    rng, gp, sname, pick = _pop(seed)
    by = _true(pick, 150)
    bad = _planted_pairs(by, 20, rng, gp, sname, None)
    recs, cl = _flat(by)
    assert flags(recs, cl) == bad


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_control_planted_sharing_only_the_given_name_is_a_gap_at_base_too(seed):
    """Two different people with the SAME given name and different surnames.  Base also missed these
    (it judged the given name alone), so this is not a regression; pinned so a change is noticed."""
    rng, gp, sname, pick = _pop(seed)
    by = _true(pick, 150)
    bad = _planted_pairs(by, 20, rng, gp, sname, "given")
    recs, cl = _flat(by)
    assert not (flags(recs, cl) & bad)


@pytest.mark.xfail(
    strict=True,
    reason="D4 REGRESSION: two different people sharing ONLY a surname (family members, a common "
    "surname) are 0/20 flagged; base flagged 20/20 (judging given_name alone saw two names). The "
    "'whole shared part links' rule ignores that the OTHER part differs entirely.",
)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_planted_sharing_only_the_surname_still_flagged(seed):
    rng, gp, sname, pick = _pop(seed)
    by = _true(pick, 150)
    bad = _planted_pairs(by, 20, rng, gp, sname, "surname")
    recs, cl = _flat(by)
    assert bad <= flags(recs, cl)


@pytest.mark.parametrize("k", [3, 5, 8])
def test_control_bigger_over_merges_with_common_names_still_flagged(k):
    """Chaining through shared parts (30 % one given name, 30 surnames): k=3,5,8 different people in
    one cluster stay flagged (>= 19/20 in 3 seeds, same as base)."""
    hit = 0
    for seed in (1, 2, 3):
        rng, gp, sname, pick = _pop(seed, spool=30)
        by = _true(pick, 150)
        for p in range(20):
            ms = []
            for j in range(k):
                g, s = pick()
                ms += [rec("fa", f"q{p}_{j}a", given_name=g, surname=s),
                       rec("fb", f"q{p}_{j}b", given_name=g, surname=s)]  # fmt: skip
            by[f"p{p}"] = ms
        recs, cl = _flat(by)
        hit += len({f"p{p}" for p in range(20)} & flags(recs, cl))
    assert hit >= 57, hit


# ══ 2. organisations with a second *name* column ═════════════════════════════════════════
def _org_world(seed, extra, n=150, planted=20, share_extra=False):
    """true org clusters (company_name in both origins, upper-cased in one) + planted fusions of two
    different companies; ``extra``: column -> fn(org index) -> value, on every record."""
    rng = random.Random(seed)
    names = set()

    def nm():
        while True:
            w = (
                " ".join(_word(rng, 2, 3) for _ in range(2))
                + " "
                + rng.choice(["ltd", "sas", "inc"])
            )
            if w not in names:
                names.add(w)
                return w

    org = [nm() for _ in range(n + 2 * planted)]

    def pair(i, ex_i):
        cols = {c: f(ex_i) for c, f in extra.items()}
        return [rec("fa", f"o{i}a", company_name=org[i], **cols),
                rec("fb", f"o{i}b", company_name=org[i].upper() if i % 3 else org[i], **cols)]  # fmt: skip

    by = {f"c{i}": pair(i, i) for i in range(n)}
    bad = set()
    for k in range(planted):
        i, j = n + 2 * k, n + 2 * k + 1
        by[f"p{k}"] = pair(i, i) + pair(j, i if share_extra else j)
        bad.add(f"p{k}")
    return _flat(by), bad


@pytest.mark.parametrize("col", ["contact_name", "trading_name", "username", "country_name"])
def test_control_org_second_name_column_distinct_per_company(col):
    pool = [_word(random.Random(i), 2, 3) + f"{i}" for i in range(400)]
    (recs, cl), bad = _org_world(3, {col: lambda i: pool[i]})
    assert flags(recs, cl) == bad


@pytest.mark.parametrize("col", ["contact_name", "parent_name", "country_name"])
def test_org_planted_fusion_sharing_a_second_name_column_is_still_flagged(col):
    (recs, cl), bad = _org_world(3, {col: lambda i: f"shared {col} {i // 2 % 3}"}, share_extra=True)
    # every pair of merged companies carries the same value in the extra column
    assert bad <= flags(recs, cl)


def test_control_org_single_name_column_flags_unchanged_by_extra_nonname_columns():
    (r0, c0), bad = _org_world(5, {})
    (r1, c1), _ = _org_world(
        5, {"email": lambda i: f"x{i}@e.example", "company_id": lambda i: str(i)}
    )
    assert flags(r0, c0) == bad == flags(r1, c1)


# ══ 3. person worlds with a second schema in one cluster (new false flags vs base) ═════════
def _mixed(seed, kind, n=200):
    rng = random.Random(seed)
    pool = sorted({_word(rng, 2, 3) for _ in range(80)})
    sn: set[str] = set()
    by = {}
    for i in range(n):
        while True:
            s = _word(rng, 2, 4)
            if s not in sn:
                sn.add(s)
                break
        g = rng.choice(pool)
        a = rec("fa", f"r{i}a", given_name=g, surname=s)
        b = {
            "fused_swap": lambda: rec("fb", f"r{i}b", given_name=f"{s} {g}", surname=None),  # noqa: B023 (called at once)
            "fused_swap_typo": lambda: rec("fb", f"r{i}b", given_name=f"{_typo(rng, s)} {g}", surname=None),  # noqa: B023 (called at once)
            "full_name_comma": lambda: rec("fb", f"r{i}b", full_name=f"{s}, {g}"),  # noqa: B023 (called at once)
            "initial_and_typo": lambda: rec("fb", f"r{i}b", given_name=g[0], surname=_typo(rng, s)),  # noqa: B023 (called at once)
            "typo_given_no_surname": lambda: rec("fb", f"r{i}b", given_name=_typo(rng, g), surname=None),  # noqa: B023 (called at once)
            "fused_typo_surname": lambda: rec("fb", f"r{i}b", given_name=f"{g} {_typo(rng, s)}", surname=None),  # noqa: B023 (called at once)
            "full_name_same": lambda: rec("fb", f"r{i}b", full_name=f"{g} {s}"),  # noqa: B023 (called at once)
        }[kind]()  # fmt: skip
        by[f"c{i}"] = [a, b]
    return _flat(by)


@pytest.mark.parametrize("kind", ["fused_typo_surname", "full_name_same"])
def test_control_mixed_schema_true_clusters_not_flagged(kind):
    recs, cl = _mixed(1, kind)
    assert flags(recs, cl) == set()


@pytest.mark.parametrize(
    "kind",
    [
        "fused_swap",
        "fused_swap_typo",
        "full_name_comma",
        pytest.param(
            "initial_and_typo",
            marks=pytest.mark.xfail(
                strict=True,
                reason="D4 NEW FALSE FLAGS vs base: when one origin keys the name fused / in another order "
                "(full_name 'surname, given'; given_name='surname given', surname NULL) and the other origin has "
                "two columns, the joined 'given surname' is neither contained in nor token-linked to it "
                "(only multi-part names contribute parts): 58-70/200 flagged (swap), 165-172/200 (swap+typo); "
                "base 0. Initial+typo'd surname 28-35/200 (base 0); typo'd given + NULL surname 198-200/200 (base 0-1).",
            ),
        ),
        "typo_given_no_surname",
    ],
)
def test_mixed_schema_true_clusters_not_flagged(kind):
    recs, cl = _mixed(1, kind)
    assert flags(recs, cl) == set()


# ══ 4. determinism: record key order differing between the two origins ═════════════════
def _reorder(recs, origin, order):
    out = []
    for r in recs:
        if r["_origin_id"] != origin:
            out.append(r)
            continue
        meta = {k: v for k, v in r.items() if k.startswith("_")}
        rest = {k: v for k, v in r.items() if not k.startswith("_")}
        out.append(
            {
                **meta,
                **{
                    k: rest[k]
                    for k in sorted(rest, key=lambda k: order.index(k) if k in order else 99)
                },
            }
        )
    return out  # fmt: skip


@pytest.mark.parametrize(
    "kind", ["same", "swap", "swap_typo", "missing_given", "substituted_given", "typo_surname"]
)
def test_control_one_origin_surname_first_changes_nothing(kind):
    r, c, _ = person_world(1, [kind], n=200)
    assert flags(_reorder(r, "fb", ["surname", "given_name"]), c) == flags(r, c) == set()


def test_all_kinds_flag_set_independent_of_column_order():
    r, c, _ = person_world(1, list(KINDS), n=200)
    r2 = _reorder(
        _reorder(r, "fb", ["surname", "given_name"]), "fa", ["suburb", "surname", "given_name"]
    )
    assert flags(r2, c) == flags(r, c) == set()


def test_control_per_record_random_key_order_planted_still_caught():
    r, c, bad = person_world(7, ["same", "swap", "missing_given", "fused"], n=200, planted=20)
    rng = random.Random(0)
    shuf = []
    for x in r:
        it = list(x.items())
        rng.shuffle(it)
        shuf.append(dict(it))
    assert bad <= flags(shuf, c)


# ══ 5. NULL / blank parts, other best-field shapes ═══════════════════════════════════════
def test_control_nulls_blanks_and_non_strings_neither_crash_nor_flag():
    r, c, _ = person_world(2, ["same", "typo_surname", "swap"], n=60)
    for i, x in enumerate(r):
        if i % 7 == 0:
            x["given_name"] = ""
        if i % 11 == 0:
            x["surname"] = "   "
        if i % 13 == 0:
            x["middle_name"] = 12345  # non-string "name" column is ignored
        if i % 17 == 0:
            x["given_name"] = None
            x["surname"] = None
    flags(r, c)  # no crash is the claim; the flag set is not asserted (names removed)


def test_control_email_best_field_and_no_hint_records():
    recs, by = [], {}
    for i in range(40):
        a = rec(
            "fa", f"e{i}a", email=f"user{i}@ex.example", work_email=f"u{i}@w.example", phone=str(i)
        )
        b = rec("fb", f"e{i}b", email=f"user{i}@ex.example", phone=str(i))
        n = rec("fb", f"n{i}b", phone=str(1000 + i), age=i)  # no name-ish field at all
        by[f"c{i}"] = [a, b, n]
    recs, cl = _flat(by)
    assert flags(recs, cl) == set()


def test_control_part_sep_in_a_data_value_does_not_crash():
    r, c, _ = person_world(3, ["same"], n=30)
    r[0]["given_name"] = f"a{PART_SEP}b"
    r[1]["surname"] = f"{PART_SEP}"
    flags(r, c)


# ══ 6. PART_SEP must not leak ═════════════════════════════════════════════════════════════
def test_control_part_sep_not_in_report_or_gate_payload():
    r, c, bad = person_world(7, list(KINDS), n=120, planted=10)
    st = state_of(r, c)
    rep = L.build_reconciliation_report(st, {}, [])
    assert PART_SEP not in json.dumps(rep, default=str)
    assert PART_SEP not in json.dumps(L._entity_measures(st, "person"), default=str)
    assert "\\u001f" not in json.dumps(rep, default=str)


def test_control_display_names_single_column_reads_as_before_and_multi_joins_best_first():
    recs = [
        rec("fa", "1", company_name="Acme Ltd"),
        rec("fa", "2", surname="Pringle", given_name="Xanthe"),  # best = first of equal rank
        rec("fa", "3", given_name="", surname="Pringle"),
        rec("fa", "4", given_name=None, surname=None, suburb="x"),
    ]
    names = L._display_names(recs)
    ids = [_record_id(r) for r in recs]
    assert names[ids[0]] == "Acme Ltd"
    assert names[ids[1]] == f"Pringle{PART_SEP}Xanthe"
    assert names[ids[2]] == "Pringle"
    assert names[ids[3]] is None


def test_control_quality_signals_without_parts_unchanged_for_plain_names():
    names = {"a": "acme ltd", "b": "acme", "c": "zeta corp", "d": "zeta"}
    cl = [{"cluster_id": "x", "record_ids": ["a", "b"]}, {"cluster_id": "y", "record_ids": ["c", "d"]},
          {"cluster_id": "m", "record_ids": ["a", "c"]}]  # fmt: skip
    q = quality_signals(cl, names)
    assert q["flagged_clusters"] == ["m"]


# ══ r2 attack ═════════════════════════════════════════════════════════════════════════════
def _people(seed, n=150):
    """n true two-record clusters, every name distinct; -> (rng, by, fresh-word fn)."""
    rng = random.Random(seed)
    used: set[str] = set()

    def nm():
        while True:
            w = _word(rng, 2, 4)
            if w not in used:
                used.add(w)
                return w

    by = {}
    for i in range(n):
        g, s = nm(), nm()
        by[f"c{i}"] = [rec("fa", f"r{i}a", given_name=g, surname=s),
                       rec("fb", f"r{i}b", given_name=g, surname=s)]  # fmt: skip
    return rng, by, nm


def _plant(by, nm, k, **extra):
    """k clusters of two different people (different given AND surname); ``extra``: column -> fn(0|1)
    giving person 0 / person 1's value of an extra column on all their records."""
    for p in range(k):
        a, b = (nm(), nm()), (nm(), nm())
        by[f"p{p}"] = [
            rec(o, f"p{p}{o}{j}", given_name=g, surname=s, **{c: f(j) for c, f in extra.items()})
            for j, (g, s) in enumerate((a, b))
            for o in ("fa", "fb")
        ]
    return {f"p{p}" for p in range(k)}


# ── 7. a folded column that two different people can share ────────────────────────────────
@pytest.mark.parametrize("col", ["username", "nickname", "middle_name"])
def test_control_folded_column_distinct_per_person_planted_flagged(col):
    for seed in (1, 2, 3):
        rng, by, nm = _people(seed)
        for rs in by.values():
            v = nm()
            for r in rs:
                r[col] = v
        bad = _plant(by, nm, 20, **{col: lambda j: nm()})  # noqa: B023 (called at once)
        assert flags(*_flat(by)) == bad


@pytest.mark.xfail(
    strict=True,
    reason="D4 r2 REGRESSION (placeholder): a folded column (middle_name/username/nickname are in "
    "_PARTIAL_NAME_FIELDS) holding the SAME placeholder on every record ('n/a', 'unknown', 'none') "
    "links all of a cluster's names into one group: planted two-person fusions 0/30 flagged "
    "(3 seeds), base 30/30.  Not the inherent shared-surname shape: nothing about a value held by "
    "many unrelated records says anything about the person.",
)
@pytest.mark.parametrize("col", ["middle_name", "username", "nickname"])
def test_planted_fusions_with_a_placeholder_in_a_folded_column_still_flagged(col):
    for seed in (1, 2, 3):
        _, by, nm = _people(seed)
        for rs in by.values():
            for r in rs:
                r[col] = "n/a"
        bad = _plant(by, nm, 30, **{col: lambda j: "n/a"})
        assert bad <= flags(*_flat(by))


@pytest.mark.xfail(
    strict=True,
    reason="D4 r2 REGRESSION (low-cardinality handle): username drawn from {guest, admin, user} "
    "(a shared account name / role handle) hides the plant whenever the two people draw the same "
    "value: 18-21/30 flagged per seed, base 30/30 (a middle initial 28-29/30, nickname pool of 5 "
    "26-27/30, six common middle names 24-27/30).  Same family as the placeholder; no-fitted-constant "
    "fix = do not link on a part held by records of many clusters.",
)
def test_planted_fusions_sharing_a_low_cardinality_username_still_flagged():
    for seed in (1, 2, 3):
        rng, by, nm = _people(seed)
        pool = ["guest", "admin", "user"]
        for rs in by.values():
            v = rng.choice(pool)
            for r in rs:
                r["username"] = v
        bad = _plant(by, nm, 30, username=lambda j: rng.choice(pool))  # noqa: B023 (called at once)
        assert bad <= flags(*_flat(by))


# ── 8. one-column names that hold a part ───────────────────────────────────────────────────
@pytest.mark.xfail(
    strict=True,
    reason="D4 r2 REGRESSION (token rule too loose): a person (given, surname) beside a one-column "
    "ORGANISATION name that merely contains the surname ('<surname> <w> hardware') is one group: "
    "0/20 flagged x 3 seeds, base 20/20.  Keyed-differently only needs a one-column name made of the "
    "parts (all tokens, or >= 2); one shared token of a longer name is the 'Smith & Wesson' over-merge.",
)
def test_person_beside_a_company_named_with_the_surname_still_flagged():
    for seed in (1, 2, 3):
        rng, by, nm = _people(seed, 80)
        bad = set()
        for p in range(20):
            g, s = nm(), nm()
            by[f"p{p}"] = [rec("fa", f"p{p}a", given_name=g, surname=s),
                           rec("fb", f"p{p}b", company_name=f"{s} {_word(rng, 3, 4)} hardware")]  # fmt: skip
            bad.add(f"p{p}")
        assert bad <= flags(*_flat(by))


def test_control_one_column_names_of_unrelated_people_beside_two_column_are_the_gap_at_base_too():
    """A multi-part person with several one-column full names that merely share the GIVEN token:
    base missed these as well (0/60); r1 caught ~80 %.  Pinned so a change is noticed."""
    for seed in (1, 2, 3):
        rng, by, nm = _people(seed, 40)
        bad = set()
        for p in range(20):
            g, s = nm(), nm()
            by[f"p{p}"] = [rec("fa", f"p{p}a", given_name=g, surname=s)] + [
                rec("fb", f"p{p}_{j}", full_name=f"{g} {nm()} {nm()}") for j in range(3)
            ]
            bad.add(f"p{p}")
        assert not (bad & flags(*_flat(by)))


# ── 9. a missing part compares by the nearest part ─────────────────────────────────────────
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_single_word_of_another_person_beside_two_columns_is_flagged_but_missing_part_is_not(seed):
    """ "anna" alone vs (bob, smith): different people flagged 20/20; the same person with the given
    name missing (or the surname missing) not flagged -- base flagged ~99 of the latter."""
    rng, by, nm = _people(seed, 100)
    for i in range(0, 100, 2):
        by[f"c{i}"][1]["given_name"] = None  # surname only
    for i in range(1, 100, 4):
        by[f"c{i}"][1]["surname"] = None  # given only
    bad = set()
    for p in range(20):
        by[f"p{p}"] = [rec("fa", f"p{p}a", given_name=nm(), surname=nm()),
                       rec("fb", f"p{p}b", given_name=nm(), surname=None)]  # fmt: skip
        bad.add(f"p{p}")
    assert flags(*_flat(by)) == bad


def test_control_single_word_compared_to_each_part_not_to_the_joined_name():
    """Two FULL names still compare whole: ("anna", "smith") vs ("bob", "jones") is flagged even
    though neither string is close to the other's parts as a set; and a single word equal to a
    typo'd part is coherent."""
    names = {"a": f"anna{PART_SEP}smith", "b": f"bob{PART_SEP}jones", "c": f"jon{PART_SEP}smyth",
             "d": "jhon"}  # fmt: skip
    q = quality_signals([{"cluster_id": "x", "record_ids": ["a", "b"]},
                         {"cluster_id": "y", "record_ids": ["c", "d"]}], names)  # fmt: skip
    assert q["flagged_clusters"] == ["x"]


# ── 10. organisations unchanged vs the parent (r1, 722b12c) wherever no part column exists ──
@pytest.mark.parametrize(
    "col",
    ["contact_name", "street_name", "country_name", "parent_name", "filename", "trading_name"],
)
def test_org_planted_sharing_any_non_part_name_column_flagged_like_pre_d4(col):
    for seed in (1, 2, 3):
        (recs, cl), bad = _org_world(
            seed, {col: lambda i: f"shared {i // 2 % 3}"}, share_extra=True
        )
        assert flags(recs, cl) == bad, (col, seed)


def test_person_street_name_is_not_folded_so_a_shared_street_cannot_hide_a_fusion():
    for seed in (1, 2, 3):
        _, by, nm = _people(seed)
        for rs in by.values():
            for r in rs:
                r["street_name"] = "high street"
        bad = _plant(by, nm, 20, street_name=lambda j: "high street")
        assert flags(*_flat(by)) == bad


@pytest.mark.xfail(
    strict=True,
    reason="D4 r2 RESIDUAL (org with a person-name-part column): an organisation table that also "
    "carries first_name/last_name (a contact), username, nickname or middle_name folds them into the "
    "name; two different companies with the SAME contact/handle value are one group: planted "
    "fusions 0/20 x 3 seeds, base 20/20 (same columns with a value per company: 20/20).  Consistent "
    "with the shared-part rule; whether it bites depends on the live org tables' columns.",
)
@pytest.mark.parametrize("cols", [["first_name", "last_name"], ["username"], ["nickname"]])
def test_org_planted_fusion_sharing_a_person_part_column_flagged(cols):
    (recs, cl), bad = _org_world(
        3, {c: lambda i: f"shared {i // 2 % 3}" for c in cols}, share_extra=True
    )
    assert bad <= flags(recs, cl)


# ── 11. determinism and PART_SEP ───────────────────────────────────────────────────────────
def test_flags_independent_of_record_cluster_and_member_order():
    r, c, _ = person_world(1, list(KINDS), n=200, planted=20)
    base = flags(r, c)
    for k in range(4):
        rng = random.Random(k)
        rr = list(r)
        rng.shuffle(rr)
        cc = [{**x, "record_ids": rng.sample(x["record_ids"], len(x["record_ids"]))} for x in c]
        rng.shuffle(cc)
        assert flags(rr, cc) == base


def test_flags_independent_of_hash_seed():
    import subprocess
    import sys

    code = (
        "from tests.part1.test_d4_acceptance import person_world, KINDS, flags;"
        "r,c,_=person_world(1,list(KINDS),n=120,planted=12);print(sorted(flags(r,c)))"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONHASHSEED": h},
            check=True,
        ).stdout
        for h in ("0", "1", "4242")
    }
    assert len(outs) == 1


def test_display_names_fold_only_the_best_column_and_person_parts():
    r = rec("fa", "1", company_name="Acme Ltd", contact_name="J Smith", street_name="High St",
            country_name="UK", filename="a.csv", username="acme", first_name="Jo", last_name="Smith",
            nickname="", middle_name=None, family_name="Smith", email="a@b.example")  # fmt: skip
    got = L._display_names([r])[_record_id(r)]
    assert got is not None
    assert sorted(got.split(PART_SEP)) == ["Acme Ltd", "Jo", "Smith", "Smith", "acme"]


def test_part_sep_in_one_column_name_text_is_not_visible_in_flagged_output():
    r, c, _ = person_world(3, ["same", "fused"], n=30, planted=4)
    r[0]["full_name"] = f"x{PART_SEP}y"
    st = state_of(r, c)
    rep = L.build_reconciliation_report(st, {}, [])
    assert PART_SEP not in json.dumps(rep, default=str)
