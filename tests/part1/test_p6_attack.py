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
