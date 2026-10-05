"""P6 acceptance: three-way discrimination (derived from the report's Evidence + Fix text; no P6 code seen).

Report P6 / plan section 9 P6.  Three mechanisms:
  1. token IDF in the comparator (P14, already landed): "Group"/"Holdings"-type tokens are down-weighted by corpus
     df, so "Acme Group Ltd" vs "Beta Group Ltd" must NOT outscore "Acme Group Ltd" vs "Acme Holdings Ltd".
  2. conflicting EXCLUSIVE identifiers suppress the matching RULE, never the pair; exclusivity is decided from
     value-sharing frequency in the DATA, not from a field-name list; a value shared by many entities is labelled
     generic (no longer exclusive: its conflict stops suppressing, its agreement stops counting as Match evidence)
     and PRIOR records are re-evaluated (the label is a property of the corpus, so record order is irrelevant).
  3. three-way outcome: Match (same email) -> auto-merge; Possible Match (same address, different tax IDs) -> the
     P15 review band / HITL, "not rejected, not auto-merged"; Possibly Related (same address + phone, different
     company) -> a TYPED EDGE, no human, never a merge.
Accept: the report's three examples as fixtures, checked as outcome CLASSES (not scores), on BOTH the heuristic
and the fitted-model path.  Honest limit: no hierarchy DIRECTION without a declared edge (not tested: nothing to test).

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p6_acceptance.py -q -p no:cacheprovider

OBSERVABLE CONTRACT FOR "Possibly Related" (the report names the outcome, not the key; the ONE adaptation point is
RELATED_KEY below).  ``resolve_entities_impl(...)`` returns, beside ``clusters`` / ``pending_merge_candidates``:

    result["possibly_related"] = [ {"record_ids": [id_a, id_b], "type": "possibly_related"}, ... ]

one entry per unordered record pair, record ids as in ``clusters[*]["record_ids"]``.  A pair that is in this list is
NEVER in a multi-record cluster and NEVER in ``pending_merge_candidates`` (no human, no merge).  The key may be
absent/empty when there are no such pairs.  ``type`` may be any non-empty string equal to RELATED_TYPE.

HOW THE PATHS ARE FORCED: "fitted" = the corpus gives >= _MIN_PAIRS_FOR_MODEL candidate pairs and the harness
asserts a model really fitted (``pair_evidence`` non-empty).  "heuristic" = no fillers (Phase 0 isolated), or
``_MIN_PAIRS_FOR_MODEL`` monkeypatched high so the same corpus is scored without a model.
"""

from __future__ import annotations

import ast
import asyncio
import random
import re
import subprocess
from pathlib import Path

import pytest

from src.mcp_server.tools import resolve_cache
from src.mcp_server.tools import resolve_entities as tre
from tests.part1 import fixtures_matching as fx

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src/mcp_server/tools/resolve_entities.py"
BASE_REV = "d330f5e"  # HEAD when these checks were derived (before any P6 code)

# ── adaptation points ────────────────────────────────────────────────────────────────────────
RELATED_KEY = "possibly_related"
RELATED_TYPE = "possibly_related"

OUTCOME_RANK = {"apart": 0, "related": 0, "pending": 1, "merged": 2}


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """resolve_cache keys on content, so the same corpus under two paths would collide."""
    monkeypatch.setattr(resolve_cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(resolve_cache, "put", lambda *a, **k: None)


# ── harness ──────────────────────────────────────────────────────────────────────────────────
def _pair(a, b):
    return frozenset((a, b))


def run(records, force_heuristic=False, monkeypatch=None):
    """Run the real impl; return (result, model_fitted)."""
    if force_heuristic:
        monkeypatch.setattr(tre, "_MIN_PAIRS_FOR_MODEL", 10**9)
    core = tre._resolve_core("company", records, None, None, None)
    res = asyncio.run(tre.resolve_entities_impl("company", records))
    return res, bool(core["pair_evidence"]) and not force_heuristic


def related_pairs(res):
    out = set()
    for e in res.get(RELATED_KEY) or []:
        ids = e["record_ids"]
        assert len(ids) == 2, f"a related edge links exactly two records: {e}"
        assert e.get("type") == RELATED_TYPE, f"edge must be typed {RELATED_TYPE!r}: {e}"
        out.add(_pair(*ids))
    return out


def merged_groups(res):
    return {frozenset(c["record_ids"]) for c in res["clusters"] if len(c["record_ids"]) > 1}


def pending_set(res):
    return {_pair(*p["record_ids"]) for p in res["pending_merge_candidates"]}


def outcome(res, ia, ib):
    k = _pair(ia, ib)
    merged = any({ia, ib} <= g for g in merged_groups(res))
    pend = k in pending_set(res)
    rel = k in related_pairs(res)
    assert [merged, pend, rel].count(True) <= 1, (
        f"a pair is in exactly one class: merged={merged} pending={pend} related={rel}"
    )
    return "merged" if merged else "pending" if pend else "related" if rel else "apart"


# ── fixtures: the report's three examples ────────────────────────────────────────────────────
def phase0(path):
    """Phase 0 fixture (tests/part1/fixtures_matching.p6_cases): heuristic = isolated pair, fitted = + fillers."""
    cases = fx.p6_cases()
    fill = fx.p6_fillers() if path == "fitted" else []
    return {n: (c["records"] + fill, *c["records"]) for n, c in cases.items()}


def _alt_fillers(n=36, seed=11, reg_field="reg_no", reg="unique", email="unique", pool=3):
    """n unrelated companies sharing one name token (so >= 30 candidate pairs exist and a model fits),
    each with its own email/address/phone; the first 4 are duplicated in the other source."""
    rng = random.Random(seed)
    toks = fx._tokens(rng, n)
    base = []
    for i, t in enumerate(toks):
        r = f"RG{i:04d}" if reg == "unique" else f"RG-POOL-{i % pool}"
        e = f"{t}@{t}.example" if email == "unique" else "accounts@shared-mailbox.example"
        base.append(
            fx._rec(
                "src_a" if i % 2 else "src_b",
                f"g{i}",
                name=f"Nimbus {t} Ltd",
                email=e,
                address=f"{i + 40} {t} Street",
                phone=f"555-07{i:02d}",
                **{reg_field: r},
            )
        )
    dupes = [
        fx._rec(
            "src_b" if r["_origin_id"] == "src_a" else "src_a",
            f"gd{i}",
            **{k: v for k, v in r.items() if not k.startswith("_")},
        )
        for i, r in enumerate(base[:4])
    ]
    return base + dupes, [(fx.rid(a), fx.rid(b)) for a, b in zip(base[:4], dupes, strict=True)]


def alt_three_way(path):
    """A second, independent fixture (different names/values/structure from Phase 0), all three cases in one corpus."""
    a1 = fx._rec("src_a", "A1", name="Larkspur Dynamics Ltd", email="hello@larkspur.example")
    a2 = fx._rec("src_b", "A1", name="LARKSPUR DYNAMICS LTD.", email="hello@larkspur.example")
    b1 = fx._rec(
        "src_a",
        "B1",
        name="Tamarind Freight Ltd",
        address="3 Harbour Wall, Westby",
        reg_no="RG-9001",
    )
    b2 = fx._rec(
        "src_b",
        "B1",
        name="Tamarind Logistics Inc",
        address="3 Harbour Wall, Westby",
        reg_no="RG-9002",
    )
    c1 = fx._rec(
        "src_a", "C1", name="Sorrel Works Ltd", address="61 Cinder Row, Eastmoor", phone="555-0188"
    )
    c2 = fx._rec(
        "src_b", "C1", name="Pewter Forge Inc", address="61 Cinder Row, Eastmoor", phone="555-0188"
    )
    fill, _ = _alt_fillers() if path == "fitted" else ([], [])
    recs = [a1, a2, b1, b2, c1, c2] + fill
    return {
        "a_same_email": (recs, a1, a2),
        "b_diff_tax": (recs, b1, b2),
        "c_related": (recs, c1, c2),
    }


EXPECT = {
    "a_same_email": "merged",
    "b_same_address_diff_tax_id": "pending",
    "b_diff_tax": "pending",
    "c_same_address_phone_diff_company": "related",
    "c_related": "related",
}
SETS = [("phase0", phase0), ("alt", alt_three_way)]
PATHS = ["heuristic", "fitted"]


def _case_params():
    out = []
    for sname, _ in SETS:
        for case in (
            ("a_same_email", "b_same_address_diff_tax_id", "c_same_address_phone_diff_company")
            if sname == "phase0"
            else ("a_same_email", "b_diff_tax", "c_related")
        ):
            for path in PATHS:
                out.append(pytest.param(sname, case, path, id=f"{sname}-{case}-{path}"))
    return out


def _load(sname, case, path):
    build = dict(SETS)[sname]
    recs, ra, rb = build(path)[case]
    return recs, fx.rid(ra), fx.rid(rb)


def _run_set(sname, path, monkeypatch):
    # phase0 heuristic = isolated pairs (no model by size); alt heuristic = patched threshold
    force = sname == "alt" and path == "heuristic"
    return force


# ── A. the report's three examples, as outcome CLASSES, on both paths ─────────────────────────
@pytest.mark.parametrize("sname,case,path", _case_params())
def test_a_three_way_outcome_class(sname, case, path, monkeypatch):
    recs, ia, ib = _load(sname, case, path)
    res, fitted = run(
        recs, force_heuristic=_run_set(sname, path, monkeypatch), monkeypatch=monkeypatch
    )
    assert fitted == (path == "fitted"), f"harness: wanted the {path} path, fitted={fitted}"
    got = outcome(res, ia, ib)
    assert got == EXPECT[case], (
        f"{sname}/{case} on the {path} path: expected {EXPECT[case]!r}, got {got!r}"
    )


def test_a_c2_cooblocked_never_merged(monkeypatch):
    """Same address+phone, names share one token ('Kestrel'): either Possible Match or Possibly Related is
    defensible (the report only fixes the disjoint-name case), but it is NEVER auto-merged, on either path."""
    for path in PATHS:
        recs, ia, ib = _load("phase0", "c2_same_address_phone_cooblocked", path)
        res, _ = run(recs)
        assert outcome(res, ia, ib) in {"pending", "related"}, (
            f"{path}: c2 must not merge or vanish"
        )


@pytest.mark.parametrize("path", PATHS)
def test_a_possibly_related_is_only_the_related_pair(path, monkeypatch):
    """An implementation that labels every low-score pair 'related' must fail: in the alt corpus the only edges
    allowed are the planted C pair; fillers sharing only the name token 'Nimbus Ltd', and the other planted
    pairs, are never edges.  Edge ids exist in the input; no edge is duplicated."""
    recs, ia, ib = _load("alt", "c_related", path)
    res, _ = run(recs, force_heuristic=(path == "heuristic"), monkeypatch=monkeypatch)
    all_ids = {fx.rid(r) for r in recs}
    edges = res.get(RELATED_KEY) or []
    pairs = related_pairs(res)
    assert len(pairs) == len(edges), "duplicate edge"
    assert pairs <= {_pair(ia, ib)}, f"unexpected related edges: {pairs - {_pair(ia, ib)}}"
    assert all(i in all_ids for p in pairs for i in p)
    assert _pair(ia, ib) in pairs, (
        "the planted Possibly-Related pair must be an edge (not empty-list vacuity)"
    )


@pytest.mark.parametrize("path", PATHS)
def test_a_related_edge_is_not_a_merge_nor_a_gate_item(path, monkeypatch):
    recs, ia, ib = _load("alt", "c_related", path)
    res, _ = run(recs, force_heuristic=(path == "heuristic"), monkeypatch=monkeypatch)
    assert _pair(ia, ib) in related_pairs(res)
    assert not any(ia in g and ib in g for g in merged_groups(res)), "an edge is never a merge"
    assert _pair(ia, ib) not in pending_set(res), "an edge is never a gate item (no human)"
    # the two records stay separate clusters
    cl = {r: c["cluster_id"] for c in res["clusters"] for r in c["record_ids"]}
    assert cl[ia] != cl[ib]


# ── B. exclusivity is decided from the data, never from a name list ──────────────────────────
def _conflict_corpus(reg_field, reg):
    """Planted pair X: same unique email, near-identical names, DIFFERENT value in ``reg_field``.
    ``reg`` = 'unique' (each value belongs to ~1 entity -> exclusive) or 'generic' (a few values shared by many)."""
    fill, _ = _alt_fillers(reg_field=reg_field, reg=reg)
    ra = fx._rec(
        "src_a",
        "X1",
        name="Vesper Trading Ltd",
        email="sales@vesper.example",
        address="14 Mill Lane, Thornby",
        **{reg_field: "RG-8001"},
    )
    rb = fx._rec(
        "src_b",
        "X1",
        name="Vesper Trading Limited",
        email="sales@vesper.example",
        address="14 Mill Lane, Thornby",
        **{reg_field: "RG-8002"},
    )
    if reg == "generic":
        ra[reg_field], rb[reg_field] = "RG-POOL-0", "RG-POOL-1"
    return [ra, rb] + fill, fx.rid(ra), fx.rid(rb)


def _run_path(recs, path, monkeypatch):
    res, fitted = run(recs, force_heuristic=(path == "heuristic"), monkeypatch=monkeypatch)
    assert fitted == (path == "fitted")
    return res


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("reg_field", ["reg_no", "tax_id"])
def test_b_exclusive_by_data_conflict_suppresses_the_rule_not_the_pair(
    path, reg_field, monkeypatch
):
    """Same email, different identifier that is unique per entity in the data -> the email rule is suppressed and
    the pair is a POSSIBLE MATCH (review), not merged and not rejected.  ``reg_no`` is in no name list anywhere:
    only its value-sharing frequency can make it exclusive."""
    recs, ia, ib = _conflict_corpus(reg_field, "unique")
    res = _run_path(recs, path, monkeypatch)
    assert outcome(res, ia, ib) == "pending", f"{reg_field}/{path}: got {outcome(res, ia, ib)}"


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("reg_field", ["reg_no", "tax_id"])
def test_b_generic_values_do_not_suppress(path, reg_field, monkeypatch):
    """The SAME pair, same field name, but the field's values are each shared by ~1/3 of the corpus (generic).
    Not exclusive -> its conflict suppresses nothing -> same email is a Match.  ``tax_id`` is a name a list would
    call exclusive: the data overrules the name."""
    recs, ia, ib = _conflict_corpus(reg_field, "generic")
    res = _run_path(recs, path, monkeypatch)
    assert outcome(res, ia, ib) == "merged", f"{reg_field}/{path}: got {outcome(res, ia, ib)}"


@pytest.mark.parametrize("path", PATHS)
def test_b_no_conflict_control_same_email_merges(path, monkeypatch):
    """Control: same pair, no identifier column at all -> the email rule fires -> Match (passes today)."""
    recs, ia, ib = _conflict_corpus("reg_no", "unique")
    for r in recs:
        r.pop("reg_no", None)
    res = _run_path(recs, path, monkeypatch)
    assert outcome(res, ia, ib) == "merged"


def test_b_unique_vs_generic_is_the_only_difference():
    """Self-check of the fixtures: the two corpora differ only in the sharing frequency of reg_no."""
    u, _, _ = _conflict_corpus("reg_no", "unique")
    g, _, _ = _conflict_corpus("reg_no", "generic")
    assert (
        len({r["reg_no"] for r in u}) == len(u) - 4
    )  # the 4 planted filler duplicates repeat a value
    assert len({r["reg_no"] for r in g}) <= 5
    assert [r["name"] for r in u] == [r["name"] for r in g]
    assert [r["email"] for r in u] == [r["email"] for r in g]


# ── C. generic value: agreement stops counting; prior records are re-evaluated ───────────────
def _email_corpus(generic, order="given", n_sharers=10, seed=3):
    """Pair K shares an email.  generic=True: the same value is ALSO the email of ``n_sharers`` other
    companies with unrelated names; generic=False: the pair's email is its own and the others' are unique."""
    rng = random.Random(seed)
    toks = fx._tokens(rng, n_sharers + 8)
    ka = fx._rec("src_a", "K1", name="Kestrel Supply Ltd", email="ops@shared-box.example")
    kb = fx._rec("src_b", "K1", name="Kestrel Logistics Ltd", email="ops@shared-box.example")
    others = [
        fx._rec(
            "src_a" if i % 2 else "src_b",
            f"s{i}",
            name=f"{toks[i].capitalize()} {toks[i + 8].capitalize()} Ltd",
            email="ops@shared-box.example" if generic else f"{toks[i]}@{toks[i]}.example",
        )
        for i in range(n_sharers)
    ]
    fill, _ = _alt_fillers(n=30, seed=seed + 1)
    recs = [ka, kb] + others + fill
    if order == "reversed":
        recs = recs[::-1]
    elif order == "shuffled":
        random.Random(99).shuffle(recs)
    return recs, fx.rid(ka), fx.rid(kb), [fx.rid(o) for o in others]


@pytest.mark.parametrize("path", PATHS)
def test_c_agreement_on_a_generic_value_is_not_match_evidence(path, monkeypatch):
    recs, ka, kb, others = _email_corpus(generic=True)
    res = _run_path(recs, path, monkeypatch)
    assert outcome(res, ka, kb) != "merged", (
        "a mailbox shared by 12 companies must not auto-merge two of them"
    )
    sh = set(others)
    bad = [g for g in merged_groups(res) if len(g & sh) > 1]
    assert not bad, f"sharers of the generic value were merged with each other: {bad}"


@pytest.mark.parametrize("path", PATHS)
def test_c_same_pair_is_more_confident_when_its_value_is_not_generic(path, monkeypatch):
    """Frequency, not the field name, is what differs: identical pair, identical field, the value shared by 2
    records (exclusive, evidence) vs by 12 (generic).  The exclusive case must not be worse and not 'apart', and
    it must be strictly better in outcome class OR in final confidence (a relative order, not a score)."""
    g, ka, kb, _ = _email_corpus(generic=True)
    u, ua, ub, _ = _email_corpus(
        generic=False
    )  # the pair still shares the email; now only the pair does
    if path == "heuristic":
        monkeypatch.setattr(tre, "_MIN_PAIRS_FOR_MODEL", 10**9)
    cg = tre._resolve_core("company", g, None, None, None)
    cu = tre._resolve_core("company", u, None, None, None)
    assert bool(cg["pair_evidence"]) == bool(cu["pair_evidence"]) == (path == "fitted")
    og = outcome(_run_path(g, path, monkeypatch), ka, kb)
    ou = outcome(_run_path(u, path, monkeypatch), ua, ub)
    sg, su = _conf(cg, ka, kb), _conf(cu, ua, ub)
    assert ou in {"pending", "merged"}, (
        f"{path}: an email shared by exactly two records is evidence ({ou!r})"
    )
    assert OUTCOME_RANK[ou] >= OUTCOME_RANK[og]
    assert OUTCOME_RANK[ou] > OUTCOME_RANK[og] or (su is not None and (sg is None or su > sg)), (
        f"{path}: exclusive-email {ou!r}/{su} vs generic-email {og!r}/{sg}: frequency must change something"
    )


@pytest.mark.parametrize("path", PATHS)
def test_c_prior_records_are_reevaluated_order_independent(path, monkeypatch):
    """'labels that value as generic and reevaluates all prior records': the K pair appears BEFORE the other
    sharers in one order and AFTER them in another; the outcome must be identical in every order."""
    seen = {}
    for order in ("given", "reversed", "shuffled"):
        recs, ka, kb, _ = _email_corpus(generic=True, order=order)
        seen[order] = outcome(_run_path(recs, path, monkeypatch), ka, kb)
    assert len(set(seen.values())) == 1, f"order-dependent outcome: {seen}"
    assert "merged" not in seen.values()


@pytest.mark.parametrize("path", PATHS)
def test_c_generic_label_is_data_not_a_list(path, monkeypatch):
    """Same experiment on a field called 'support_line' (in no list): generic by frequency alone."""
    recs, ka, kb, others = _email_corpus(generic=True)
    renamed = []
    for r in recs:
        r = dict(r)
        r["support_line"] = r.pop("email")
        renamed.append(r)
    res = _run_path(renamed, path, monkeypatch)
    assert outcome(res, ka, kb) != "merged"


# ── D. IDF ordering: the suffix-list inversion must not hold ─────────────────────────────────
def _idf_corpus():
    """'Group' occurs in ~40% of names (so it is common by df); 'Acme' is in only the planted records."""
    toks = fx._tokens(random.Random(5), 40)
    recs = []
    for i, t in enumerate(toks):
        nm = f"{t.capitalize()} Group Ltd" if i < 16 else f"{t.capitalize()} Services Ltd"
        recs.append(fx._rec("src_a" if i % 2 else "src_b", f"n{i}", name=nm))
    pa = fx._rec("src_a", "P1", name="Acme Group Ltd")
    pb = fx._rec("src_b", "P1", name="Acme Holdings Ltd")  # plausibly the same
    qa = fx._rec(
        "src_a", "Q1", name="Beta Group Ltd"
    )  # different company, shares only the common token
    return recs + [pa, pb, qa], fx.rid(pa), fx.rid(pb), fx.rid(qa)


def _conf(core, a, b):
    k = _pair(a, b)
    for x, y, c in core["scored_pairs"]:
        if _pair(x, y) == k:
            return c
    for x, y, _1, _2, c in core["borderline"]:
        if _pair(x, y) == k:
            return c
    return None  # never compared == lowest


@pytest.mark.parametrize("path", PATHS)
def test_d_idf_ordering_not_inverted(path, monkeypatch):
    """Control on current code (P14 landed): plausibly-same must outrank different-companies-sharing-a-common-token,
    on the fitted AND the heuristic path.  Guards against P6 re-breaking it."""
    recs, pa, pb, qa = _idf_corpus()
    if path == "heuristic":
        monkeypatch.setattr(tre, "_MIN_PAIRS_FOR_MODEL", 10**9)
    core = tre._resolve_core(
        "company", recs, None, None, None, extra_candidate_pairs=[(pa, pb), (pa, qa)]
    )
    assert bool(core["pair_evidence"]) == (path == "fitted")
    good, bad = _conf(core, pa, pb), _conf(core, pa, qa)
    assert good is not None, "Acme Group vs Acme Holdings must at least be compared"
    assert bad is None or bad < good, f"inverted: Beta Group {bad} >= Acme Holdings {good}"


# ── E. 2.4: no fitted constants ──────────────────────────────────────────────────────────────
SCOPED = (
    "_has_conflicting_identity",
    "_deterministic_conflict",
    "_has_discriminating_agreement",
    "_compute_heuristic_confidence",
    "_score_pair",
)
BANNED = {
    "france",
    "us",
    "sas",
    "berhad",
    "global",
    "group",
    "holdings",
    "ltd",
    "inc",
    "gmbh",
    "corp",
    "llc",
    "plc",
    "limited",
    "companyname",
    "main_entity_id",
    "company_relation",
    "staff",
    "org_title",
    "subsidiary",
    "parent",
    "branch",
}


def _git_show(rev, path):
    try:
        return subprocess.run(
            ["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except subprocess.CalledProcessError:
        pytest.skip(f"baseline {rev} not available")


def _docstring_ids(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                ids.add(id(b[0].value))
    return ids


def _strings(node, doc):
    return {
        n.value.lower()
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc
    }


def _funcs(tree):
    return {
        n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _nums(node):
    return {
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, int | float)
        and not isinstance(n.value, bool)
    }


def _trees():
    old = ast.parse(_git_show(BASE_REV, "src/mcp_server/tools/resolve_entities.py"))
    new = ast.parse(SRC.read_text())
    return old, new


def test_e_no_new_corpus_string_literal_anywhere_in_the_file():
    old, new = _trees()
    new_strings = _strings(new, _docstring_ids(new)) - _strings(old, _docstring_ids(old))
    hit = sorted(
        s for s in new_strings if s in BANNED or any(w in BANNED for w in re.findall(r"[a-z_]+", s))
    )
    assert not hit, f"corpus/suffix literals added as code strings: {hit}"


def test_e_no_new_word_list_in_scoped_or_new_functions():
    """A name/suffix/identifier list is the thing the report retracts: no new collection of >= 3 lowercase words
    (tuple/set/frozenset/list/dict keys) in the scoped functions, in any new function, or as a new module constant."""
    old, new = _trees()
    of, nf = _funcs(old), _funcs(new)
    targets = [nf[n] for n in nf if n in SCOPED or n not in of]
    old_consts = {
        t.id
        for n in old.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    for n in new.body:
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id not in old_consts for t in n.targets
        ):
            targets.append(n)
    bad = []
    for t in targets:
        for c in ast.walk(t):
            if isinstance(c, ast.Tuple | ast.Set | ast.List):
                words = [
                    e.value
                    for e in c.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                if len(words) >= 3 and all(re.fullmatch(r"[a-z_ ]+", w) for w in words):
                    bad.append((getattr(t, "name", "module-const"), words))
    assert not bad, f"new word lists (fitted by hand?): {bad}"


def test_e_numeric_constants_in_changed_functions_are_derived():
    """For every numeric literal added to a scoped function, a NEW function, or a new module constant ask
    'would a count over the corpus produce this?'.  Allowed: whatever the baseline already had in that function
    (existing behaviour), plus 0,1,2 (identity / counting / halving).  Anything else (0.05, 0.3, 5, 10, ...) is a
    fitted constant and must instead be computed from value-sharing frequency at run time."""
    old, new = _trees()
    of, nf = _funcs(old), _funcs(new)
    allowed_always = {0, 1, 2}
    bad = []
    for name, fn in nf.items():
        if name in SCOPED or name not in of:
            base = _nums(of[name]) if name in of else set()
            extra = _nums(fn) - base - allowed_always
            if extra:
                bad.append((name, sorted(extra)))
    old_consts = {
        t.id
        for n in old.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    for n in new.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id not in old_consts:
                    extra = _nums(n.value) - allowed_always
                    if extra:
                        bad.append((t.id, sorted(extra)))
    assert not bad, f"numeric literals a corpus count would not produce: {bad}"


def test_e_source_grep_clean_for_corpus_words_in_added_lines():
    """git diff of the source against the pre-P6 baseline: no added CODE line (outside comments/docstrings the AST
    check above covers strings) may name the corpus's suffix/jurisdiction tokens as quoted strings."""
    diff = subprocess.run(
        ["git", "diff", BASE_REV, "--", "src/"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    pat = re.compile(
        r"""["'](?:[^"']*\b)?(France|US|SAS|Berhad|Global|Group|Holdings|companyname|main_entity_id|"""
        r"""company_relation|staff)\b[^"']*["']""",
        re.I,
    )
    hits = [ln.strip() for ln in added if pat.search(ln) and not ln.lstrip().startswith("#")]
    assert not hits, hits[:5]


def test_e_arch_lint_clean():
    r = subprocess.run(
        [".venv/bin/python", "scripts/lint_arch.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-400:]
