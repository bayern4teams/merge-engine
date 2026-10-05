"""P6 attack (adversarial, written after the implementation, verifier).  Strict xfail = a defect found
(reproduced against parent 055446b: each of these was NOT a regression-free outcome there); the reason names it.
Controls (no xfail) pin behaviour that must keep holding.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p6_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import random

import pytest

from src.mcp_server.tools import resolve_cache
from src.mcp_server.tools import resolve_entities as tre


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(resolve_cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(resolve_cache, "put", lambda *a, **k: None)


def R(origin, row, **f):
    return {"_origin_id": origin, "_origin_table": "t", "_origin_id_row": row, **f}


def tk(g):
    r = random.Random(g)
    return "".join(r.choice("bdfgklmnprtvz") + r.choice("aeiou") for _ in range(3)).title()


def run(entity, recs, rules=None):
    res = asyncio.run(tre.resolve_entities_impl(entity, recs, matching_rules=rules))
    merged = [set(c["record_ids"]) for c in res["clusters"] if len(c["record_ids"]) > 1]
    pend = [frozenset(p["record_ids"]) for p in res["pending_merge_candidates"]]
    rel = [frozenset(e["record_ids"]) for e in res.get("possibly_related", [])]
    return merged, pend, rel


def cls(res, a, b):
    merged, pend, rel = res
    rid = tre._record_id
    a, b = rid(a), rid(b)
    if any({a, b} <= g for g in merged):
        return "merged"
    if frozenset((a, b)) in pend:
        return "pending"
    return "related" if frozenset((a, b)) in rel else "apart"


BASE = {"name": "Acme Widgets Ltd", "email": "info@acme.example", "address": "1 A St"}


# ── D1: an operator-declared unique id conflict is overridden by the anchor Match rule ──────────
@pytest.mark.parametrize("rule", ["unique_id_fields", "deterministic_id_fields"])
def test_declared_id_conflict_is_never_merged(rule):
    recs = [R("a", "1", **BASE, ref="R 100"), R("b", "1", **BASE, ref="R 200")]
    assert cls(run("company", recs, {rule: ["ref"]}), *recs) != "merged"


def test_declared_id_conflict_lowcard_corpus():
    fill = [
        R(
            "ab"[k % 2],
            f"f{k}",
            name=f"Other{k} Co",
            email=f"o{k}@x.example",
            address=f"{k} Elm",
            ledger="X",
        )
        for k in range(8)
    ]
    recs = [R("a", "1", **BASE, ledger="X"), R("b", "1", **BASE, ledger="Y")] + fill
    assert cls(run("company", recs, {"unique_id_fields": ["ledger"]}), recs[0], recs[1]) != "merged"


# ── D2: attributes that differ between origins for a TRUE duplicate (same email+address+name) ──
def _dupes(attr, n=30, differ=3):
    recs, pairs = [], []
    for i in range(n):
        t = tk(i)
        base = {
            "name": f"{t} Trading Ltd",
            "email": f"info@{t}.example",
            "address": f"{i + 10} {tk(i + 500)} Road",
        }
        a = {**base, "phone": f"07{i:03d}-{i * 7 % 1000:03d}", "postcode": f"LS{i % 40}{i % 9}AA"}
        b = dict(a)
        if i % differ == 0:
            b["phone"] = f"07{i:03d}-999"
        ra, rb = R("a", str(i), **a), R("b", str(i), **b)
        recs += [ra, rb]
        pairs.append((ra, rb))
    return recs, pairs


def test_true_duplicates_with_differing_phone_still_merge():
    recs, pairs = _dupes("phone")
    res = run("company", recs)
    assert all(cls(res, a, b) == "merged" for a, b in pairs[::3])


def test_true_duplicates_with_same_phone_merge_control():
    recs, pairs = _dupes("phone")
    res = run("company", recs)
    assert all(cls(res, a, b) == "merged" for i, (a, b) in enumerate(pairs) if i % 3)


def _persons(kind, n=30):
    recs, pairs = [], []
    for i in range(n):
        fn, ln = tk(i), tk(i + 500)
        a = R(
            "a",
            str(i),
            first_name=fn,
            last_name=ln,
            email=f"{fn}.{ln}@x.example".lower(),
            city=f"City{i}",
        )
        lb = ln[:-1] + "x" if i % 3 == 0 else ln
        b = R("b", str(i), first_name=fn, last_name=lb, email=a["email"], city=f"City{i}")
        recs += [a, b]
        pairs.append((a, b))
    return recs, pairs


def test_person_same_email_surname_typo_still_merges():
    recs, pairs = _persons("typo")
    res = run("person", recs)
    assert all(cls(res, a, b) == "merged" for a, b in pairs[::3])


# ── D3: unrelated entities sharing a (2-held) address with different phones flood the review queue ─
@pytest.mark.parametrize("origins", [2, 1])
def test_unrelated_companies_in_one_building_are_not_review_items(origins):
    recs = [
        R(
            "ab"[i % origins],
            str(i),
            name=f"{tk(i)} {tk(i + 1000)} Ltd",
            address=f"{i // 2 + 1} Tower Plaza",
            phone=f"555-{i:04d}",
        )
        for i in range(60)
    ]
    _m, pend, _r = run("company", recs)
    assert pend == []


def test_building_without_phone_column_yields_related_edges_only():
    recs = [
        R(
            "ab"[i % 2],
            str(i),
            name=f"{tk(i)} {tk(i + 1000)} Ltd",
            address=f"{i // 2 + 1} Tower Plaza",
        )
        for i in range(60)
    ]
    merged, pend, rel = run("company", recs)
    assert merged == [] and pend == [] and len(rel) == 30 and len(set(rel)) == 30
    assert all(len(e) == 2 for e in rel)  # no self-pairs


def test_building_shared_by_many_is_generic_no_edges():
    recs = [
        R(
            "ab"[i % 2],
            str(i),
            name=f"{tk(i)} {tk(i + 1000)} Ltd",
            address=f"{i // 20 + 1} Tower Plaza",
        )
        for i in range(60)
    ]
    assert run("company", recs) == ([], [], [])


# ── D4: single-origin / within-origin triplicates: cut = max(#origins, 2) = 2 makes every shared value generic ─
def test_single_origin_triplicates_with_name_variants_merge():
    recs, groups = [], []
    for g in range(6):
        t = tk(g)
        names = (f"{t} Widgets Ltd", f"{t} Widgets Limited", f"{t} WIDGETS")
        gi = [
            R("a", f"{g}_{j}", name=n, email=f"info@{t.lower()}.example", address=f"{g + 5} Road")
            for j, n in enumerate(names)
        ]
        recs += gi
        groups.append(gi)
    res = run("company", recs)
    assert all(cls(res, g[0], x) == "merged" for g in groups for x in g[1:])


def test_single_origin_pairs_and_exact_triplicates_merge_control():
    recs = [
        R("a", str(i), name="Acme Widgets Ltd", email="i@acme.example", address="1 A St")
        for i in range(3)
    ]
    res = run("company", recs)
    assert cls(res, recs[0], recs[1]) == "merged" and cls(res, recs[0], recs[2]) == "merged"


def test_shared_mailbox_of_three_unrelated_companies_is_not_evidence():
    recs = [
        R("ab"[i % 2], str(i), name=n, email="acc@shared.example")
        for i, n in enumerate(("Alpha Ltd", "Bravo Ltd", "Charlie Ltd"))
    ]
    assert run("company", recs)[0] == []


# ── controls that pass: invariants of the new key, order independence, person-name regressions ──
def test_related_edges_never_in_cluster_or_pending_and_no_self_or_duplicates():
    recs = [
        R("a", "1", name="Acme Widgets Ltd", address="1 Main St", phone="5"),
        R("b", "1", name="Zeta Foods Inc", address="1 Main St", phone="5"),
        R("a", "2", name="Qux Bar", address="9 Other", phone="7"),
    ]
    merged, pend, rel = run("company", recs)
    assert len(rel) == 1 and len(set(rel)) == 1 and not pend and not merged


def test_outcomes_are_order_independent():
    recs = []
    for i in range(24):
        t = tk(i)
        recs.append(
            R(
                "a",
                str(i),
                name=f"{t} Trading Ltd",
                email=f"i@{t}.example",
                address=f"{i // 3} Road",
                phone=str(i),
            )
        )
        if i % 3:
            recs.append(
                R(
                    "b",
                    str(i),
                    name=f"{t} Trading Limited",
                    email=f"i@{t}.example",
                    address=f"{i // 3} Road",
                    phone=str(i) if i % 2 else f"9{i}",
                )
            )

    def sig(rs):
        m, p, r = run("company", rs)
        return sorted(map(tuple, map(sorted, m))), sorted(map(sorted, p)), sorted(map(sorted, r))

    base = sig(recs)
    for s in range(4):
        rs = recs[:]
        random.Random(s).shuffle(rs)
        assert sig(rs) == base


@pytest.mark.parametrize(
    "a,b", [("John Smith", "Smith, John"), ("John Smith", "SMITH JOHN"), ("Li Wei", "Wei Li")]
)
def test_swapped_person_names_with_same_email_merge(a, b):
    fill = [
        R("ab"[k % 2], f"f{k}", name=n, email=f"f{k}@z.example")
        for k, n in enumerate(
            [
                "John Brown",
                "Mary Smith",
                "Peter Smith",
                "Ann Jones",
                "Tom Smith",
                "Zoe Smith",
                "Al Smith",
                "Bo Smith",
            ]
        )
    ]
    recs = [
        R("a", "1", name=a, email="x@y.example"),
        R("b", "1", name=b, email="x@y.example"),
    ] + fill
    assert cls(run("person", recs), recs[0], recs[1]) == "merged"


def test_splink_path_documents_missing_possibly_related_key():
    """Splink returns before _resolve_core: no 'possibly_related' key.  Consumers must tolerate absence."""
    import inspect

    assert "possibly_related" in inspect.getsource(tre.resolve_entities_impl)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Round 2 (against 19c1ca1): attacks on the leave-one-out / name-group / declared-wins design.
# Each case was also run on parent 055446b; the reason names the parent outcome where it matters.
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _fill(n, origins="ab", extra=None):
    return [
        R(
            origins[k % len(origins)],
            f"f{k}",
            name=f"{tk(k + 2000)} {tk(k + 3000)} Ltd",
            email=f"f{k}@{tk(k + 4000).lower()}.example",
            address=f"{k + 300} {tk(k + 5000)} Way",
            **(extra(k) if extra else {}),
        )
        for k in range(n)
    ]


def _taxdups(n_ok, n_typo, tax_field="tax_id"):
    """Evident duplicates (same name+email+address): n_ok agree on the tax id, n_typo carry a one-char typo."""
    recs = []
    for i in range(n_ok + n_typo):
        t = tk(i + 7000)
        base = dict(
            name=f"{t} Trading Ltd",
            email=f"info@{t.lower()}.example",
            address=f"{i + 40} {tk(i + 8000)} Road",
        )
        tax = f"GB{100000 + i * 17}"
        recs.append(R("a", f"d{i}", **base, **{tax_field: tax}))
        recs.append(R("b", f"d{i}", **base, **{tax_field: tax[:-1] + "x" if i >= n_ok else tax}))
    return recs


def _branch_pair(tax_field="tax_id"):
    """Same name, same address (no shared email), DIFFERENT tax ids: the report's 'Possible Match' example."""
    base = dict(name="Qelo Brands Ltd", address="9 Harbour Row")
    return (
        R("a", "p1", **base, **{tax_field: "GB900001"}),
        R("b", "p2", **base, **{tax_field: "GB900002"}),
    )


# ── N1: one evident duplicate in the corpus (leave-one-out has one observation) ──
def test_r2_one_evident_duplicate_then_conflicting_pair_is_possible_match():
    recs = _taxdups(1, 0) + list(_branch_pair()) + _fill(10)
    a, b = recs[2], recs[3]
    assert cls(run("company", recs), a, b) == "pending"


def test_r2_zero_evident_duplicates_hint_named_tax_id_still_conflicts():
    a, b = _branch_pair()
    assert cls(run("company", [a, b] + _fill(6)), a, b) == "pending"


# ── N2: sloppy hint-named key.  A typo elsewhere must not switch the key off for every other pair ──
@pytest.mark.xfail(
    strict=True,
    reason="D5: exclusive_against requires disagree <= 0 over ALL other evident dups (zero tolerance): one or more typos in a "
    "hint-named tax_id anywhere switch the conflict off corpus-wide; the report's 'same address + different tax IDs' pair is "
    "then auto-MERGED (parent: pending).",
)
@pytest.mark.parametrize("n_typo", [1, 2, 3])
def test_r2_a_few_tax_id_typos_do_not_disable_the_conflict_for_the_corpus(n_typo):
    """20 evident duplicates, n_typo of them carry a tax-id typo; a different pair at one address with two
    different tax ids is still a Possible Match (parent: hint-named tax_id always conflicts -> pending)."""
    base = _taxdups(20 - n_typo, n_typo)
    a, b = _branch_pair()
    assert cls(run("company", base + [a, b] + _fill(10)), a, b) == "pending"


def test_r2_typo_dups_themselves_are_not_merged_blind():
    """The typo'd pair itself (same name/email/address, tax id differs by one char): pending or merged, never apart."""
    recs = _taxdups(10, 2)
    res = run("company", recs)
    typo = [(recs[2 * i], recs[2 * i + 1]) for i in (10, 11)]
    assert all(cls(res, x, y) in ("merged", "pending") for x, y in typo)


@pytest.mark.xfail(
    strict=True,
    reason="D5: same root cause, all evident dups carry a typo -> hint-named tax_id loses exclusivity, distinct pair MERGED.",
)
def test_r2_only_evident_dups_disagree_on_tax_id_still_conflict_on_a_distinct_pair():
    """All 4 evident duplicates carry a typo (agree=0, disagree=4).  A different-tax-id pair at one address is then
    judged by hint, not silently Match (parent: pending)."""
    base = _taxdups(0, 4)
    a, b = _branch_pair()
    assert cls(run("company", base + [a, b] + _fill(6)), a, b) == "pending"


# ── N3: declared unique key with NULL on one side / sloppy values ──
def test_r2_declared_unique_key_null_one_side_is_not_a_conflict():
    recs = [R("a", "1", **BASE, ref="R 100"), R("b", "1", **BASE, ref=None)] + _fill(6)
    assert cls(run("company", recs, {"unique_id_fields": ["ref"]}), recs[0], recs[1]) == "merged"


def test_r2_declared_unique_key_agree_is_not_blocked():
    recs = [R("a", "1", **BASE, ref="R 100"), R("b", "1", **BASE, ref="R 100")] + _fill(6)
    assert cls(run("company", recs, {"unique_id_fields": ["ref"]}), recs[0], recs[1]) == "merged"


def test_r2_declared_key_conflict_never_merged_even_with_many_dups_agreeing():
    """Declared key always wins, whatever the data says about the field (20 duplicates all agree on `ref`)."""
    base = []
    for i in range(20):
        t = tk(i + 9000)
        d = dict(name=f"{t} Co", email=f"i@{t.lower()}.example", address=f"{i} Rd", ref=f"R{i}")
        base += [R("a", f"d{i}", **d), R("b", f"d{i}", **d)]
    x = R("a", "x", **BASE, ref="R 100")
    y = R("b", "x", **BASE, ref="R 200")
    assert cls(run("company", base + [x, y], {"unique_id_fields": ["ref"]}), x, y) != "merged"


def test_r2_declared_deterministic_key_conflict_never_merged_heuristic_and_model():
    x = R("a", "x", **BASE, ref="R 100")
    y = R("b", "x", **BASE, ref="R 200")
    for fill in (0, 40):
        res = run("company", [x, y] + _fill(fill), {"deterministic_id_fields": ["ref"]})
        assert cls(res, x, y) != "merged", fill


# ── N4: names "not disjoint" by one shared made-up generic token ──
def test_r2_two_unrelated_companies_sharing_only_a_legal_form_token_are_not_merged():
    """Same address, different tax ids, names share only the made-up legal form 'Qorpa' (held by every company)."""
    fill = [
        R("ab"[k % 2], f"f{k}", name=f"{tk(k)} {tk(k + 1)} Qorpa", address=f"{k} Far St")
        for k in range(30)
    ]
    x = R("a", "x", name="Velu Dano Qorpa", address="5 Shared Ct", tax_id="T1")
    y = R("b", "x", name="Morik Sela Qorpa", address="5 Shared Ct", tax_id="T2")
    res = run("company", fill + [x, y])
    assert cls(res, x, y) in ("apart", "related")  # not pending, not merged


def test_r2_small_corpus_legal_form_token_shared_address_unrelated_not_review():
    """4 records only: every token has df <= cut, so every shared token is 'partial'.  Parent: no review item."""
    x = R("a", "x", name="Velu Dano Qorpa", address="5 Shared Ct")
    y = R("b", "x", name="Morik Sela Qorpa", address="5 Shared Ct")
    z = R("a", "z", name="Pita Hoko Qorpa", address="77 Elsewhere")
    w = R("b", "z", name="Rudi Lafe Qorpa", address="78 Elsewhere")
    assert cls(run("company", [x, y, z, w]), x, y) in ("apart", "related")


# ── N5: generic labelling is a corpus property (re-evaluates prior records), order independent ──
def test_r2_value_becomes_generic_when_later_records_arrive():
    a = R("a", "1", name="Velu Dano Ltd", email="acc@mail.example")
    b = R("b", "1", name="Morik Sela Ltd", email="acc@mail.example")
    c = R("a", "2", name="Pita Hoko Ltd", email="acc@mail.example")
    d = R("b", "2", name="Rudi Lafe Ltd", email="acc@mail.example")
    for order in ([a, b], [a, b, c, d], [d, c, b, a]):
        m, _p, _r = run("company", order)
        assert m == [], len(order)


def test_r2_many_origins_raise_the_cut_but_unrelated_mailbox_holders_still_not_merged():
    """6 origins -> cut 6.  A mailbox held by 5 unrelated companies is not 'generic' (5 <= 6) but names are
    disjoint, so no merge (parent behaviour compared in the verdict)."""
    names = ["Velu Dano Ltd", "Morik Sela Ltd", "Pita Hoko Ltd", "Rudi Lafe Ltd", "Zane Kumi Ltd"]
    recs = [R("abcdef"[i], "1", name=n, email="acc@mail.example") for i, n in enumerate(names)]
    recs.append(R("f", "9", name="Lone Tree Ltd", email="lt@x.example"))
    assert run("company", recs)[0] == []


def test_r2_nameless_records_same_email_not_worse_than_parent():
    """Contacts without any name column: same email in 2 origins -> merged on parent (email is the key)."""
    recs = [
        R("a", "1", email="x@y.example", phone="1"),
        R("b", "1", email="x@y.example", phone="1"),
    ]
    recs += [R("ab"[k % 2], f"f{k}", email=f"f{k}@y.example", phone=str(k + 10)) for k in range(6)]
    assert cls(run("person", recs), recs[0], recs[1]) == "merged"


# ── N6: both paths on the report's three examples with a realistic noisy corpus around them ──
def _noisy():
    # evident duplicates with a phone that differs sometimes, tax ids that agree, etc.
    return _taxdups(12, 0) + _fill(12)


@pytest.mark.parametrize("force_heuristic", [True, False])
def test_r2_three_examples_in_a_noisy_corpus(force_heuristic, monkeypatch):
    if force_heuristic:
        monkeypatch.setattr(tre, "_MIN_PAIRS_FOR_MODEL", 10**9)
    s1 = (
        R("a", "s1", name="Neka Voro Ltd", email="hello@neka.example"),
        R("b", "s1", name="Neka Voro Limited", email="hello@neka.example"),
    )
    s2 = (
        R("a", "s2", name="Quil Bano Ltd", address="3 Pier Rd", tax_id="GB555001"),
        R("b", "s2", name="Quil Bano Ltd", address="3 Pier Rd", tax_id="GB555002"),
    )
    s3 = (
        R("a", "s3", name="Rova Kim Foods", address="8 Dock Rd", phone="020 7000 111"),
        R("b", "s3", name="Tepo Ludi Garage", address="8 Dock Rd", phone="020 7000 111"),
    )
    recs = _noisy() + [*s1, *s2, *s3]
    res = run("company", recs)
    assert cls(res, *s1) == "merged"
    assert cls(res, *s2) == "pending"
    assert cls(res, *s3) == "related"


# ── N7: order independence under shuffle with sloppy exclusives and a declared key ──
def test_r2_order_independent_with_tax_id_typos_and_branches():
    recs = _taxdups(8, 2) + list(_branch_pair()) + _fill(8)

    def sig(rs):
        m, p, r = run("company", rs)
        return sorted(map(tuple, map(sorted, m))), sorted(map(sorted, p)), sorted(map(sorted, r))

    base = sig(recs)
    for s in range(5):
        rs = recs[:]
        random.Random(s + 11).shuffle(rs)
        assert sig(rs) == base


# ── N8: name-group generic test is fooled by a shared token among holders (among = #holders) ──
@pytest.mark.parametrize("n_holders", [4, 6, 10])
def test_r2_shared_mailbox_of_unrelated_companies_with_a_common_made_up_token(n_holders):
    """n unrelated companies share one mailbox; their names all contain the made-up token 'Qorpa' (held by exactly
    those n names, so `among`=n makes them one 'name group' and the mailbox is not generic)."""
    recs = [
        R("ab"[i % 2], str(i), name=f"{tk(i)} {tk(i + 50)} Qorpa", email="acc@mail.example")
        for i in range(n_holders)
    ]
    recs += [
        R("ab"[k % 2], f"f{k}", name=f"{tk(k + 90)} {tk(k + 91)}", email=f"f{k}@z.example")
        for k in range(10)
    ]
    holders = {tre._record_id(r) for r in recs[:n_holders]}
    m, p, _r = run("company", recs)
    # residual (not a regression: parent leaves 4/6 review items here, r2 2/3): never merged; review items remain
    assert not [g for g in m if g & holders]


@pytest.mark.xfail(
    strict=True,
    reason="D5b: 'GB 100000' vs 'GB100000' is not normalised equal, so formatting variants count as disagreement and switch "
    "the conflict off (parent: hint-named, pending).",
)
def test_r2_tax_id_format_variants_among_duplicates_do_not_disable_conflict():
    """Formatting variance ("GB 123" vs "GB123") in 10/10 duplicates must not turn the key off."""
    recs = []
    for i in range(10):
        t = tk(i + 7000)
        base = dict(
            name=f"{t} Trading Ltd", email=f"info@{t.lower()}.example", address=f"{i + 40} Rd"
        )
        recs += [
            R("a", f"d{i}", **base, tax_id=f"GB{100000 + i}"),
            R("b", f"d{i}", **base, tax_id=f"GB {100000 + i}"),
        ]
    a, b = _branch_pair()
    assert cls(run("company", recs + [a, b] + _fill(8)), a, b) == "pending"
