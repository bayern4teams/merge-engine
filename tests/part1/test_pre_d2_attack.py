"""PRE-D2 attack: `_conflicting_codes` / `_code_tokens` (commit 893da8f) beyond the acceptance corpus.

Method: the fix adds exactly two call sites of `_conflicting_codes` (`_score_pair`, `_resolve_core`).
Monkeypatching it to ``False`` therefore reproduces the pre-fix scoring, so one process can compare
"old" and "new" on any corpus (the worktree comparison of the whole suite is done separately).

Sections: A pure-function table (edge cases), B side-effect invariants (unflagged pairs are
untouched, flagged pairs only fall and never reach the bar), C documented gaps (xfail strict, they
flip to XPASS->fail when someone closes the gap), D real Abt-Buy (needs ABT_BUY_DIR, ~40 s).
Corpora are seeded random syllables; no corpus literal is used in src or here as data.
"""

from __future__ import annotations

import ast
import csv
import os
import random
import subprocess
from pathlib import Path

import pytest

from src.mcp_server.tools import resolve_entities as R

ROOT = Path(__file__).resolve().parents[2]
AM, AX = 0.95, 0.70


def cc(a, b, ka="name", kb="name") -> bool:
    return R._conflicting_codes({ka: a}, {kb: b})


# --------------------------------------------------------------------------- A. pure function
@pytest.mark.parametrize(
    "a,b,expected",
    [
        # same product, written differently -> never a conflict
        ("Mount 20WLBK", "mount 20wlbk", False),  # case
        ("Mount PS-LX350H", "Mount PSLX350H", False),  # separator on one side
        ("Mount 20-WL BK", "Mount 20WLBK", False),  # split code
        ("Flash Drive 4 GB", "Flash Drive 4gb", False),  # unit spaced vs not
        ("Stick ZX9 16GB", "Stick ZX9 16 GB", False),
        ("Cam DSC-T500", "Cam black camera", False),  # code on one side only
        ("Cam DSC-T500", "Cam DSC-T500B", False),  # one code contains the other
        ("3M Post-it", "3M Post it", False),
        ("A1 Telekom", "A1 Telekom Austria Group", False),
        ("", "x1", False),
        # different codes -> conflict
        ("Flash Drive 4GB", "Flash Drive 8GB", True),  # unit tokens are codes by shape
        ("TV 720p LCD", "TV 1080p LCD", True),
        ("Mount 20WLBK", "Mount 20WLWH", True),
        ("Cam CHS4001", "Cam CHS40001", True),  # not a substring either way
        ("Muller 4a", "Muller 4b", True),
        ("Müller 4a", "Müller 4b", True),  # non-ASCII letters
        # shape heuristic limits (pinned, not endorsed): see section C for the ones that are gaps
        ("Radio Q 300", "Radio Q 320", False),  # digit-only model numbers are not codes
        ("John Smith apt 4b", "John Smith apt 4c", True),  # a mixed token in a NAME field conflicts
        ("12a Main Street", "12b Main Street", True),
        ("3M Company", "A1 Telekom Austria", True),  # two different orgs: right answer
    ],
)
def test_code_table(a, b, expected):
    assert cc(a, b) is expected
    assert cc(b, a) is expected  # symmetric


def test_symmetry_random():
    rng = random.Random(5)
    toks = ["ab12", "x100", "x1000", "k9", "red", "mk2", "4gb", "pro", "9", "z"]
    for _ in range(2000):
        a = " ".join(rng.sample(toks, rng.randint(0, 4)))
        b = " ".join(rng.sample(toks, rng.randint(0, 4)))
        assert cc(a, b) == cc(b, a), (a, b)


def test_non_string_missing_and_odd_inputs_do_not_raise():
    for rec1, rec2 in [
        ({}, {}),
        ({"name": None}, {"name": "x1"}),
        ({"name": 12}, {"name": "x1"}),  # non-str: not a name field
        ({"name": "   "}, {"name": "a1"}),
        ({"name": "ab12"}, {"title": "cd34"}),  # different name field per origin
        ({"_name": "ab12"}, {"name": "cd34"}),  # private field ignored
        ({"name": "\x00�1a"}, {"name": "2b​"}),
    ]:
        assert R._conflicting_codes(rec1, rec2) in (True, False)
    assert R._conflicting_codes({"name": "ab12"}, {"title": "cd34"}) is True


def test_description_only_code_is_not_a_conflict():
    """Documented ceiling: only the best name field is read."""
    a = {"name": "Wall Mount", "description": "model 20WLBK"}
    b = {"name": "Wall Mount", "description": "model 20WLWH"}
    assert R._conflicting_codes(a, b) is False


def test_person_with_given_name_and_surname_uses_given_name_only():
    """febrl-like record: the picked 'name' field is given_name (first hint match), so an address or
    surname with a mixed token never conflicts."""
    p1 = {"given_name": "ann", "surname": "lee2", "street_number": "12a", "address_1": "apt 4b"}
    p2 = {"given_name": "ann", "surname": "lee3", "street_number": "12b", "address_1": "apt 4c"}
    assert R._best_name_field(p1) == "given_name"
    assert R._conflicting_codes(p1, p2) is False


def test_street_name_before_given_name_is_a_hazard():
    """First hint match in dict order wins (`street_name` contains 'name'): a column order with
    street_name first makes a street number the 'name'. Pinned as a hazard for the report."""
    p1 = {"street_name": "12a elm", "given_name": "ann"}
    p2 = {"street_name": "12b elm", "given_name": "ann"}
    assert R._best_name_field(p1) == "street_name"
    assert R._conflicting_codes(p1, p2) is True


def test_no_numeric_constants_in_new_functions():
    """Plan 2.4: no fitted constant. The two new functions contain no number other than the
    indices/booleans of tuple unpacking."""
    tree = ast.parse((ROOT / "src/mcp_server/tools/resolve_entities.py").read_text())
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in ("_code_tokens", "_conflicting_codes"):
            nums = [
                n.value
                for n in ast.walk(fn)
                if isinstance(n, ast.Constant)
                and isinstance(n.value, (int, float))
                and not isinstance(n.value, bool)
            ]
            assert nums == [], (fn.name, nums)


def test_nit_docstring_quotes_no_corpus_literal():
    out = subprocess.run(
        ["git", "diff", "22685c3", "HEAD", "--", "src/"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    added = "\n".join(
        ln for ln in out.splitlines() if ln.startswith("+") and not ln.startswith("+++")
    ).lower()
    for w in ("omnimount", "sanus", "sony", "wlbk", "wlwh"):
        assert w not in added, w


# --------------------------------------------------------------------------- B. invariants
_C, _V = "bdfgklmnprstvz", "aeiou"


def _w(rng, n=2):
    return "".join(rng.choice(_C) + rng.choice(_V) for _ in range(n))


def _score(entity, recs, patched_off: bool):
    orig = R._conflicting_codes
    if patched_off:
        R._conflicting_codes = lambda *a, **k: False
    try:
        core = R._resolve_core(entity, recs, None, None, None)
    finally:
        R._conflicting_codes = orig
    sc = {frozenset((a, b)): s for a, b, s in core["scored_pairs"]}
    sc |= {frozenset((a, b)): s for a, b, _1, _2, s in core["borderline"]}
    return core, sc


def _rec(origin, i, table="product", **kw):
    r = {"_origin_id": origin, "_origin_table": f"public.{table}", "_origin_id_row": str(i)}
    r.update(kw)
    return r


def _products(seed, with_price=True):
    rng = random.Random(seed)
    recs, fam = [], []
    n = 0
    vocab = [_w(rng) for _ in range(220)]
    for f in range(30):  # sibling families (abt only), shared description, codes differ in suffix
        brand, title = _w(rng, 2), " ".join(rng.sample(vocab, 3))
        base = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(2)) + str(
            rng.randint(10, 99)
        )
        price = f"{rng.randint(100, 9999) / 10:.2f}" if with_price else None
        ids = []
        for suf in rng.sample(["AA", "BB", "CC", "DD"], 2):
            n += 1
            desc = " ".join(rng.sample(vocab, 6))
            recs.append(
                _rec(
                    "abt",
                    n,
                    name=f"{brand} {title} - {base}{suf}",
                    description=f"{brand} {title} {desc}",
                    price=price,
                )
            )
            ids.append(f"abt:public.product:{n}")
        fam.append(tuple(ids))
    for _ in range(80):  # unrelated products on both sides + true duplicates with a code
        brand, title = _w(rng, 2), " ".join(rng.sample(vocab, 3))
        code = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(2)) + str(
            rng.randint(100, 999)
        )
        n += 1
        recs.append(_rec("abt", n, name=f"{brand} {title} {code}", description=f"{brand} {title}"))
        n += 1
        recs.append(_rec("buy", n, name=f"{brand} {title} {code.lower()}", description=brand))
    for _ in range(60):
        n += 1
        recs.append(_rec("buy", n, name=" ".join(rng.sample(vocab, 3)), description=_w(rng, 4)))
    return recs, fam


@pytest.mark.parametrize("seed", [11, 12])
@pytest.mark.parametrize("with_price", [True, False])
def test_flagged_pairs_fall_to_review_and_nothing_else_changes(seed, with_price):
    recs, fam = _products(seed, with_price)
    core, new = _score("product", recs, False)
    _, old = _score("product", recs, True)
    assert set(new) == set(old)
    by_id = {R._record_id(r): r for r in recs}
    flagged = {p for p in new if R._conflicting_codes(*(by_id[i] for i in sorted(p)))}
    assert flagged, "corpus produced no flagged pair"
    for p in new:
        if p in flagged:
            assert new[p] <= old[p] + 1e-4  # the fix only lowers (4-dp rounding aside)
            assert new[p] < core["auto_match"], (p, new[p])
        else:
            assert new[p] == pytest.approx(old[p], abs=1e-9), (p, old[p], new[p])
    # the sibling pairs themselves: all below the bar, price shared or not
    for a, b in fam:
        assert new.get(frozenset((a, b)), 0.0) < core["auto_match"]
    # and the pre-fix defect really was present on this corpus
    if with_price:  # a shared price is what lifted siblings back to Match (_three_way)
        assert any(old.get(frozenset(p), 0.0) >= core["auto_match"] for p in fam), (
            "old did not fail"
        )


def test_codes_in_a_single_pair_corpus_without_a_model():
    """Two records, no fitted model, heuristic path only."""
    a = _rec("abt", 1, name="Omni Wall Mount - 20WLBK", description="d")
    b = _rec("abt", 2, name="Omni Wall Mount - 20WLWH", description="d")
    core, sc = _score("product", [a, b], False)
    assert all(s < core["auto_match"] for s in sc.values())


def _people(seed):
    rng = random.Random(seed)
    recs = []
    for i in range(150):
        g, s = _w(rng, 2), _w(rng, 3)
        email = f"{g}.{s}{rng.randint(1, 99)}@{_w(rng, 2)}.org"
        city = _w(rng, 3)
        street = f"{rng.randint(1, 99)}{rng.choice('abc')} {_w(rng, 3)} st apt {rng.randint(1, 9)}b"
        recs.append(
            _rec("a", i, "person", given_name=g, surname=s, email=email, city=city, address=street)
        )
        if i % 2 == 0:  # duplicate with a different house number/apt (typo of the address)
            street2 = street.replace("apt", "apt ").replace("b", "c")
            recs.append(
                _rec(
                    "b",
                    i,
                    "person",
                    given_name=g,
                    surname=s,
                    email=email,
                    city=city,
                    address=street2,
                )
            )
    return recs


def test_persons_with_mixed_token_addresses_are_untouched():
    recs = _people(3)
    _, new = _score("person", recs, False)
    _, old = _score("person", recs, True)
    assert set(new) == set(old)
    assert max(abs(new[p] - old[p]) for p in new) < 1e-9
    by_id = {R._record_id(r): r for r in recs}
    assert not any(R._conflicting_codes(*(by_id[i] for i in sorted(p))) for p in new)


def test_organisation_names_with_codes_only_flag_disjoint_codes():
    rng = random.Random(8)
    recs = [
        _rec("a", 0, "org", company_name="3M Company", city="x1"),
        _rec("b", 0, "org", company_name="3M Corp", city="x1"),
        _rec("a", 1, "org", company_name="A1 Telekom Austria", city="y"),
        _rec("b", 1, "org", company_name="A1 Telekom", city="y"),
    ]
    for i in range(2, 80):
        recs.append(
            _rec("a", i, "org", company_name=f"{_w(rng, 3)} {_w(rng, 2)} ltd", city=_w(rng))
        )
    _, new = _score("org", recs, False)
    _, old = _score("org", recs, True)
    assert max(abs(new[p] - old[p]) for p in new) < 1e-9


# --------------------------------------------------------------------------- C. documented gaps
@pytest.mark.xfail(
    strict=True, reason="gap: a base code that is a prefix of the other's code hides the variant"
)
def test_gap_prefix_codes_x100_vs_x1000():
    assert cc("Amp X100 black", "Amp X1000 black") is True


@pytest.mark.xfail(
    strict=True, reason="gap: shared base code + differing colour/size code is not flagged"
)
def test_gap_shared_base_code_with_different_suffix_token():
    assert cc("Case 13in MB13-BLK-SEE-V2", "Case 13in MB13-PNK-SEE-V2") is True


@pytest.mark.xfail(
    strict=True, reason="gap: hyphen splits a code, 'WD-65736' is two digit-only/alpha tokens"
)
def test_gap_hyphenated_model_numbers():
    assert cc("TV WD-65736 projection", "TV WD-60735 projection") is True


def test_gap_shared_website_still_settles_organisations_with_code_suffixes():
    rng = random.Random(3)
    recs = [
        _rec("a", 0, "org", company_name="Zorvex Holdings A1", website="zorvex.com", city="x"),
        _rec("b", 1, "org", company_name="Zorvex Holdings B2", website="zorvex.com", city="x"),
    ]
    for i in range(2, 150):
        recs.append(
            _rec(
                rng.choice("ab"),
                i,
                "org",
                company_name=f"{_w(rng, 3)} {_w(rng, 3)} ltd",
                website=f"{_w(rng, 3)}.com",
                city="x",
            )
        )
    core, sc = _score("org", recs, False)
    assert sc[frozenset(("a:public.org:0", "b:public.org:1"))] >= core["auto_match"]


# --------------------------------------------------------------------------- D. real Abt-Buy
def _load(d: Path):
    recs = []
    for name, o in (("Abt.csv", "abt"), ("Buy.csv", "buy")):
        with open(d / name, encoding="latin-1", newline="") as f:
            for r in csv.DictReader(f):
                rec = {k: (v if v != "" else None) for k, v in r.items()}
                rec.update(_origin_id=o, _origin_table="public.product", _origin_id_row=r["id"])
                recs.append(rec)
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
        pytest.skip("set ABT_BUY_DIR")
    recs, truth = _load(Path(d))
    core, new = _score("product", recs, False)
    _, old = _score("product", recs, True)
    by_id = {R._record_id(r): r for r in recs}
    flag = {p for p in new if R._conflicting_codes(*(by_id[i] for i in sorted(p)))}
    return {"core": core, "new": new, "old": old, "truth": truth, "flag": flag}


def test_real_every_flagged_pair_is_below_auto_and_unflagged_are_untouched(real):
    new, old, flag = real["new"], real["old"], real["flag"]
    assert set(new) == set(old)
    assert max(new[p] for p in flag) <= 0.93
    assert all(new[p] == pytest.approx(old[p], abs=1e-9) for p in new if p not in flag)
    assert all(new[p] <= old[p] + 1e-4 for p in flag)


def test_real_measured_review_band_effect(real, capsys):
    """Numbers behind the 0.93-vs-exclude recommendation (printed with -s)."""
    new, old, flag, truth = real["new"], real["old"], real["flag"], real["truth"]
    auto = lambda s: s >= AM  # noqa: E731
    band = lambda s: AX <= s < AM  # noqa: E731
    n = {
        "old_auto": sum(map(auto, old.values())),
        "old_band": sum(map(band, old.values())),
        "new_auto": sum(map(auto, new.values())),
        "new_band": sum(map(band, new.values())),
        "auto_to_band": sum(auto(old[p]) and band(new[p]) for p in new),
        "auto_to_band_true": sum(auto(old[p]) and band(new[p]) and p in truth for p in new),
        "true_flagged": sum(p in truth for p in flag),
        "true_flagged_reaching_band_or_auto_old": sum(old[p] >= AX for p in flag if p in truth),
        "true_auto_old": sum(auto(old[p]) for p in truth if p in old),
        "true_auto_new": sum(auto(new[p]) for p in truth if p in new),
        "true_band_old": sum(band(old[p]) for p in truth if p in old),
        "true_band_new": sum(band(new[p]) for p in truth if p in new),
        "if_excluded_band": sum(band(new[p]) for p in new if p not in flag),
        "if_excluded_true_band": sum(band(new[p]) for p in truth if p in new and p not in flag),
    }
    with capsys.disabled():
        print("\n[PRE-D2 attack real]", n)
    assert n["auto_to_band_true"] == 0  # no true pair was demoted out of auto
    assert n["true_auto_new"] == n["true_auto_old"]
    assert n["true_band_new"] == n["true_band_old"]
    assert n["true_flagged_reaching_band_or_auto_old"] <= 1  # excluding would cost at most 1
