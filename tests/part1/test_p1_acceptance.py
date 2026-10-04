"""P1 acceptance checks: dictionary-driven split of no-delimiter field names.

Derived from TECHNICAL_REPORT_2026-09-26.md ("P1 - Field-name tokeniser cannot split
lowercase identifiers") and PART1_IMPLEMENTATION_PLAN.md section 9 (P1) / 2.4, plus the
Phase 0 baseline (.handoff/P0-6-schema.report.md, tests/part1/fixtures_schema.py).
Written BEFORE the implementation exists; it only touches the public-ish seams
`_tokenize_field(name[, vocabulary])` and `map_fields_impl(...)`.

API the implementer must expose (the one contract this file imposes):
    _tokenize_field(name, vocabulary=None) -> list[str]
where `vocabulary` is an iterable of lowercase words (the words observed in the
destination schema at runtime). Called with one argument (or vocabulary=None/empty) the
result must equal today's. The second parameter may be positional or any keyword name;
the helper below tries the common ones. Where the ambiguity report lives is the
implementer's choice (see `_ambiguity_entries`).

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p1_acceptance.py -q
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
from pathlib import Path

import pytest

from src.mcp_server.tools import map_fields as mf
from tests.part1 import fixtures_schema as fx

SRC = Path(mf.__file__)
OLD_REASON = "No matching destination column found; destination schema is authoritative"


# ── helpers ────────────────────────────────────────────────────────────────
def _dest(table: str, cols: list[str]) -> dict:
    return {"table": table, "columns": [{"name": c, "type": "text"} for c in ["id", *cols]]}


def _map(table: str, fields: list[str], dest: dict) -> dict:
    return asyncio.run(
        mf.map_fields_impl(
            table, fx.p1_unified_schema(fields), dest, llm_config=None, connector=None
        )
    )


def _mapped(res: dict) -> dict[str, str]:
    return {k: v["dest_column"] for k, v in res["field_mapping"].items()}


def _tok(name: str, vocab) -> list[str]:
    """Call _tokenize_field with a vocabulary, whatever the second parameter is called."""
    fn = mf._tokenize_field
    params = [p for p in inspect.signature(fn).parameters.values()][1:]
    if not params:
        pytest.fail(
            "_tokenize_field takes no vocabulary parameter: the split must be driven by the "
            "destination vocabulary, so expose _tokenize_field(name, vocabulary=None)"
        )
    return (
        fn(name, vocab)
        if params[0].kind != params[0].KEYWORD_ONLY
        else fn(name, **{params[0].name: vocab})
    )


# ── 1. end-to-end: the 4 missed fields map, the 3 planted negatives do not ──
CONCAT = fx.P1_SETS["concat"]
MISSED_FOUR = {
    "foundingdate": "founded_date",
    "taxregistration": "tax_registration_number",
    "contactemail": "primary_contact_email",
    "postalcode": "mailing_postal_code",
}
NEGATIVES = [k for k, v in CONCAT.items() if v is None]


def test_four_missed_fields_now_map_to_planted_columns():
    got = _mapped(_map("org", list(CONCAT), fx.p1_destination_schema()))
    for src, want in MISSED_FOUR.items():
        assert got.get(src) == want, f"{src}: expected {want}, got {got.get(src)}"


def test_already_mapping_seven_do_not_regress():
    got = _mapped(_map("org", list(CONCAT), fx.p1_destination_schema()))
    for src, want in CONCAT.items():
        if want is not None and src not in MISSED_FOUR:
            assert got.get(src) == want, f"{src} regressed: {got.get(src)}"


def test_planted_negatives_stay_unmapped_with_old_reason():
    res = _map("org", list(CONCAT), fx.p1_destination_schema())
    got = _mapped(res)
    reasons = {u["field"]: u["reason"] for u in res["unmapped_fields"]}
    for neg in NEGATIVES:  # legacyflag, colorcode, presspoint
        assert neg not in got, f"{neg} falsely mapped to {got.get(neg)}"
        assert neg in reasons
        assert reasons[neg] == OLD_REASON


def test_controls_camel_and_snake_sets_unchanged():
    for name in ("camel", "snake"):
        truth = fx.P1_SETS[name]
        got = _mapped(_map("org", list(truth), fx.p1_destination_schema()))
        assert got == {k: v for k, v in truth.items() if v is not None}, name


# ── 1b. second, unrelated destination: nothing here is a corpus literal ──
VEH_COLS = [
    "vehicle_registration_plate",
    "engine_displacement_volume",
    "fuel_tank_capacity",
    "driver_license_number",
    "insurance_policy_holder",
]
VEH_CAMEL = {
    "engineDisplacement": "engine_displacement_volume",
    "licenseNumber": "driver_license_number",
    "policyHolder": "insurance_policy_holder",
    "registrationPlate": "vehicle_registration_plate",
    "fuelCapacity": "fuel_tank_capacity",
}


def test_second_destination_concat_maps_like_camel():
    """Property: concatenating a camel name must map exactly as the camel name does."""
    dest = _dest("vehicle", VEH_COLS)
    # camel baseline (current code): 5 map to the planted columns
    assert _mapped(_map("vehicle", list(VEH_CAMEL), dest)) == VEH_CAMEL
    concat = {k.lower(): v for k, v in VEH_CAMEL.items()}
    res = _map("vehicle", [*concat, "wheelbase", "zzqxv"], dest)
    got = _mapped(res)
    assert got == concat  # 5 correct, wheelbase / zzqxv left alone
    assert {u["field"] for u in res["unmapped_fields"]} == {"wheelbase", "zzqxv"}


# ── 2. tokeniser is driven by the runtime vocabulary ───────────────────────
def test_split_uses_vocabulary():
    v = {"company", "name", "business", "address", "postal", "code"}
    assert _tok("companyname", v) == ["company", "name"]
    assert _tok("businessaddress", v) == ["business", "address"]


def test_no_split_when_vocabulary_lacks_a_word():
    v = {"company", "name", "address", "postal", "code"}  # no 'business'
    assert _tok("businessaddress", v) == ["businessaddress"]
    assert _tok("companyname", {"company"}) == ["companyname"]  # 'name' unknown: no partial split


def test_split_words_come_from_vocabulary_not_a_builtin_list():
    """Invented words: any hard-coded dictionary cannot produce this."""
    v = {"zorb", "quix", "flim"}
    assert _tok("zorbquix", v) == ["zorb", "quix"]
    assert _tok("flimzorbquix", v) == ["flim", "zorb", "quix"]
    assert _tok("zorbquix", {"zorb"}) == ["zorbquix"]


@pytest.mark.parametrize("vocab", [None, set(), frozenset()])
def test_empty_vocabulary_is_todays_behaviour(vocab):
    for n in ("companyname", "businessaddress", "x", ""):
        assert _tok(n, vocab) == mf._tokenize_field(n)


def test_tokenizer_still_callable_with_one_argument():
    assert mf._tokenize_field("companyName") == ["company", "name"]
    assert mf._tokenize_field("companyname") == ["companyname"]  # no vocabulary, no split


# ── 3. regression snapshot: captured from the CURRENT code (pre-fix) ────────
SNAPSHOT = {
    "companyName": ["company", "name"],
    "foundingDate": ["founding", "date"],
    "taxRegistrationNumber": ["tax", "registration", "number"],
    "postalCode": ["postal", "code"],
    "parentOrgRef": ["parent", "org", "ref"],
    "orgLabel": ["org", "label"],
    "userID": ["user", "id"],
    "ISO3166Code": ["iso3166", "code"],
    "company_name": ["company", "name"],
    "legal_title": ["legal", "title"],
    "Tax_Registration": ["tax", "registration"],
    "__id__": ["id"],
    "postal-address": ["postal", "address"],
    "contact-email-address": ["contact", "email", "address"],
    "website.url": ["website", "url"],
    "org_label.v2": ["org", "label", "v2"],
    "a.b.c": ["a", "b", "c"],
    "employee count": ["employee", "count"],
    "phoneNumber2": ["phone", "number2"],
    "id": ["id"],
    "x": ["x"],
    "": [],
}
# every piece above, as a plausible runtime vocabulary
_VOCAB = {t for toks in SNAPSHOT.values() for t in toks} | {
    "founded",
    "primary",
    "mailing",
    "phone",
    "number",
    "employee",
}


@pytest.mark.parametrize("name,expected", list(SNAPSHOT.items()))
def test_snapshot_without_vocabulary(name, expected):
    assert mf._tokenize_field(name) == expected


@pytest.mark.parametrize("name,expected", list(SNAPSHOT.items()))
def test_snapshot_with_vocabulary_delimited_and_camel_unchanged(name, expected):
    assert _tok(name, _VOCAB) == expected


# ── 4. ambiguity is reported, never guessed ────────────────────────────────
# vocabulary words: bar, ton, bart, on  ->  'barton' = bar|ton  or  bart|on (equal length 2)
AMB_COLS = ["bar_code", "ton_count", "bart_flag", "on_hand"]


def _ambiguity_entries(res: dict, field: str) -> list:
    """Loose shape: any entry (item of a top-level list/dict value, or the value itself) outside
    field_mapping whose serialised form mentions `field` and 'ambig' (case-insensitive).
    Implementer's choice of key/location: unmapped_fields[*].reason, a new top-level key, an
    extra key on the unmapped entry... all pass. A bare OLD_REASON entry does not."""
    found = []
    for key, val in res.items():
        if key == "field_mapping":
            continue
        items = list(val.items()) if isinstance(val, dict) else val
        for it in items if isinstance(items, list) else [items]:
            blob = json.dumps(it, default=str).lower()
            if field in blob and "ambig" in blob:
                found.append(it)
    return found


def test_ambiguous_split_is_reported_not_guessed():
    res = _map("item", ["barton"], _dest("item", AMB_COLS))
    assert "barton" not in _mapped(res), "ambiguous name was guessed onto a column"
    assert _ambiguity_entries(res, "barton"), (
        "no report entry mentioning 'barton' and 'ambig...' found in " + json.dumps(res)[:400]
    )


def test_ambiguity_report_not_emitted_for_unambiguous_or_unsplittable():
    res = _map(
        "org", ["legacyflag", "colorcode", "presspoint", "zzqxv"], fx.p1_destination_schema()
    )
    for f in ("legacyflag", "colorcode", "presspoint", "zzqxv"):
        assert not _ambiguity_entries(res, f), f"{f} is merely unmatched, not ambiguous"


def test_ambiguity_tokenizer_does_not_pick_a_side():
    toks = _tok("barton", {"bar", "ton", "bart", "on"})
    assert toks not in (["bar", "ton"], ["bart", "on"])


# ── 5. failure reason for an unsplittable name remains ─────────────────────
def test_unsplittable_name_keeps_reason_string():
    res = _map("org", ["zzqxv", "wheelbase"], fx.p1_destination_schema())
    assert {u["field"]: u["reason"] for u in res["unmapped_fields"]} == {
        "zzqxv": OLD_REASON,
        "wheelbase": OLD_REASON,
    }
    assert res["field_mapping"] == {} and res["schema_changes"] == []


def test_no_new_destination_columns_ever():
    res = _map("org", list(CONCAT), fx.p1_destination_schema())
    assert res["schema_changes"] == []


# ── 6. no fitted constants (plan 2.4) ──────────────────────────────────────
CORPUS_RE = re.compile(
    r"\b(France|US|SAS|Berhad|Global|Group|Holdings|companyname|main_entity_id|"
    r"company_relation|staff|zoominfo)\b"
)
# Baseline (pre-P1) word-list literals (>=3 all-string elements) in map_fields.py:
# lines 875, 909, 957-961 (qualifier / role-prefix / synonym lists in _heuristic_match
# and coalesce guards). P1 may not add one.
BASELINE_WORD_LISTS = 7


def _word_list_literals(tree: ast.AST):
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Set | ast.List | ast.Tuple)
            and len(n.elts) >= 3
            and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts)
        ):
            yield n


def test_grep_no_corpus_literals_in_map_fields():
    hits = [(i, ln) for i, ln in enumerate(SRC.read_text().splitlines(), 1) if CORPUS_RE.search(ln)]
    assert hits == []  # baseline today: 0 hits


def test_grep_no_new_hardcoded_word_list():
    tree = ast.parse(SRC.read_text())
    assert len(list(_word_list_literals(tree))) <= BASELINE_WORD_LISTS
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and re.search(
            r"token|split|segment|vocab", fn.name, re.I
        ):
            assert not list(_word_list_literals(fn)), f"word list inside {fn.name}"
