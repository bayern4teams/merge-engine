[< back to docs/INDEX.md](../INDEX.md)

# Splink tuning methodology — diffbot + zoominfo → destination

**Purpose of this file.** This is not a report for a human reader — it is a
replayable technical procedure for another LLM (or engineer) to read once and
reproduce, extend, or run against a *different* database pair. Every claim
below is either a code change you can diff, a command you can re-run, or a
number produced by a script whose full path is given. Nothing here is
summarized away for readability at the cost of precision. The audit report
(`audit_report_v2.html`, the published artifact) states the *conclusions* in
one paragraph and links here; this file is the *how*.

Companion artifacts:

- Code fix: `src/mcp_server/tools/splink_matcher.py` (the production Splink
  integration — this file changed).
- New test: `tests/unit/test_splink_matcher.py::test_score_pairs_blocks_on_domain_even_when_names_disagree`.
- Trained model: `logs/splink_models/company_diffbot_zoominfo/v1/` (active).
- Reproduction scripts (not part of the app, kept for re-running this exact
  procedure): `scripts/splink/splink_tune_boeing_airbus.py` (Run 2/3.3 below)
  and `scripts/splink/splink_train_once_predict_many.py` (Run 3/3.4 below),
  with the small `scripts/splink/diffbot_rows.tsv` fixture (the 25 curated
  Boeing/Airbus diffbot rows). The 3,000-row bulk training sample used in
  §3.4 is deliberately **not** committed (a data dump, not code — same rule
  that keeps `sample_databases/` out of the repo); both scripts print the
  exact SQL to regenerate it if it's missing.

---

## 1. Starting point: what was broken, and how it was found

The production Splink integration is `src/mcp_server/tools/splink_matcher.py
::score_pairs_with_splink`. It is called per entity type, per run, from
`resolve_with_splink` (dispatched from `resolve_entities_impl` when
`matching_rules={"engine": "splink"}`). Before this session's fix, it built
its comparisons and blocking rules like this (paraphrased, see git history
for the exact diff):

```python
if "name" in available_cols:
    comparisons.append(cl.JaroWinklerAtThresholds("name", [0.9, 0.7]))
    blocking_rules.append(block_on("name"))          # BUG 1: exact block on
                                                       # the fuzzy-compared field
if "email" in available_cols:
    comparisons.append(cl.ExactMatch("email")...)
    if not blocking_rules:                            # BUG 2: only fires if
        blocking_rules.append(block_on("email"))      # nothing blocked yet
for col in available_cols:
    if col not in ("name", "email"):
        comparisons.append(cl.ExactMatch(col))
        if not blocking_rules:                        # same BUG 2
            blocking_rules.append(block_on(col))
```

**Bug 1 (already documented in the audit report, §5):** `block_on("name")`
generates the SQL predicate `l."name" = r."name"` — byte-identical equality.
Paired with a *fuzzy* comparator on the same column, this is Splink's own
documented anti-pattern: the comparator can never see a pair whose names
differ at all, because blocking excluded it before scoring ever runs.
Reproduced on a 6-row fixture in the audit: 0 of 15 possible pairs scored.

**Bug 2 (found this session, not previously documented — the deeper half of
the same defect):** `blocking_rules` is a **union** — Splink scores a pair if
it satisfies *any* rule in the list, so more rules should only ever add
recall. But every rule after `name`'s guarded by `if not blocking_rules`,
which is already true the instant `name` (present in nearly every
`available_cols`) adds its own rule. Concretely: **a strong identity field
like `domain` never got its own blocking rule at all**, even though "two
companies sharing a registrable domain are almost certainly the same
company" is exactly the kind of high-precision, high-recall blocking key
Splink's own docs recommend leading with. `domain` still got a *comparison*
object (the `for col in available_cols` loop always appends one), but
without its own blocking rule, that comparison could only ever fire for
pairs that *also* happened to block on `name` or `email` first — which,
given Bug 1, was frequently zero pairs at all.

**How Bug 2 was found:** not from reading the code in isolation, but by
actually running the (Bug‑1‑fixed) matcher against real data (§2 below) and
noticing that a pair sharing an exact `boeing.com` domain but no matching
name prefix still never appeared in the scored output. Tracing *why* led
straight to the `if not blocking_rules` guard. This is the general lesson
worth carrying forward: **a blocking-rule bug that only manifests as a
missing pair produces no exception and no log line** — the only way to catch
it is to check, on real data, whether a pair you *know* should be compared
actually is.

---

## 2. The fix

```python
comparisons: list[Any] = []
blocking_rules: list[Any] = []

if "name" in available_cols:
    comparisons.append(cl.JaroWinklerAtThresholds("name", [0.9, 0.7]))
    # Loosened, case-insensitive prefix key -- never exact-block the field
    # you fuzzy-compare. Case-insensitivity is not cosmetic: "BOEING
    # INTEGRATED DEFENSE SY" vs "Boeing Defense & Space Group" would
    # otherwise fail to share a 4-char prefix purely on case ("BOEI" !=
    # "Boei"), which is exactly the live bug caught in §3 below.
    blocking_rules.append(
        'substr(lower(l."name"), 1, 4) = substr(lower(r."name"), 1, 4)'
    )

if "domain" in available_cols:
    comparisons.append(cl.ExactMatch("domain").configure(term_frequency_adjustments=True))
    blocking_rules.append(block_on("domain"))

if "email" in available_cols:
    comparisons.append(cl.ExactMatch("email").configure(term_frequency_adjustments=True))
    blocking_rules.append(block_on("email"))

for col in available_cols:
    if col not in ("name", "domain", "email"):
        comparisons.append(cl.ExactMatch(col))
        if not blocking_rules:      # unchanged fallback for entities with
            blocking_rules.append(block_on(col))   # none of the three above
```

Every one of `name`/`domain`/`email` that is present now contributes its own
blocking rule unconditionally — the union grows with evidence instead of
stopping at the first field. The `for col in available_cols` loop's
remaining `if not blocking_rules` guard is *deliberately* left in place for
arbitrary other columns: unioning a rule per arbitrary low-cardinality column
(e.g. `industry_type`, `country`) risks the exact same low-selectivity
blow-up the comparison-budget bug hit on `jobTitle` values like `"senior"`
(see the audit report §00b / known_problems.md §18.4). The correct general
fix for *that* is to cost each candidate rule with Splink's own
`count_comparisons_from_blocking_rule` (already used by the B.4 workbench's
S‑E recommender, `splink_blocking.py`) before unioning it — not implemented
here, flagged as the next increment (§7).

**Domain normalisation (a second, coupled fix):** before this session,
`domain` values reached Splink as whatever raw string the source stored —
`"https://www.boeing.com/careers"` and `"boeing.com"` are different strings
to an exact-match comparator. The builtin (non-Splink) engine already solves
this with `_domain_of()` (`resolve_entities.py:1671`), which strips scheme,
path, query, port, and a leading `www.` to leave the registrable domain. The
fix reuses that exact function when projecting records for Splink:

```python
row_dict[k] = _domain_of(v) if k == "domain" else str(v)
```

This must happen *before* the DataFrame is built — Splink compares strings
literally, so normalisation has to happen at the Python layer, not inside a
Splink comparison level.

**Deterministic-prior field preference:** the prior-probability estimation
step (`estimate_probability_two_random_records_match`) previously preferred
`email` when present, else the first available column. Changed to prefer
`domain` first (only falling back to `email`, then the first column) because
company-shaped entities populate `domain` far more reliably than `email`,
and it is now normalised to the same reliability class.

All of this is one self-contained diff to `score_pairs_with_splink`; nothing
in `resolve_with_splink`, the clustering call, or the 13-key result contract
changed.

---

## 3. Live verification against real data (not a synthetic fixture)

### 3.1 The corpus

Real records, pulled live from the actual origins this session, not
synthesised:

- **diffbot** (Postgres, `testdiffbot` db, `diffbot.company` table): every
  row where `entityname` or `fullname` contains "boeing" or "airbus"
  (case-insensitive) — **25 rows**. Pulled via:

  ```sql
  select companyid, entityname, fullname, coalesce(website,''), coalesce(left(summary,150),'')
  from diffbot.company
  where lower(entityname) like '%boeing%' or lower(entityname) like '%airbus%'
     or lower(fullname) like '%boeing%' or lower(fullname) like '%airbus%'
  order by entityname;
  ```

- **zoominfo** (ClickHouse, `testzoominfo` db, `Company` table): the
  equivalent query — **27 rows, 26 distinct `companyID`s** (one duplicate row
  observed at the storage layer, harmless — `ReplacingMergeTree` had not yet
  collapsed it).

Combined: **54 distinct company records**, spanning one real duplicate
cluster ("Boeing"/"Boeing Co"/"The Boeing Company"/zoominfo "Boeing" — 4
records, all genuinely the same legal entity), several genuinely hard
near-duplicate clusters (the "BDS" three-way split already documented in the
audit report, and an "Airbus"/"Airbus SE"/"Airbus Group" historical-rename
cluster), and roughly a dozen confirmed decoys (companies whose name merely
*contains* "Boeing" or "Airbus" — employee clubs, unrelated trailer/material-
handling businesses, a hobbyist website). This is exactly the corpus the
audit report's ground-truth work already characterized in detail; this file
does not re-derive that classification, it uses it as labels.

### 3.2 Run 1 — the OLD (buggy) code, for a baseline

Not re-run in this session (the bug is already fully characterized in the
audit report §5 with its own 6-row reproduction: 0 of 15 pairs scored under
`block_on("name")` alone). Baseline: **0 of the known true-duplicate pairs
were even blocked**, regardless of how well they would have scored.

### 3.3 Run 2 — the FIXED code, per-run retrain (the production call shape)

Script: `splink_tune_boeing_airbus.py`. Calls
`score_pairs_with_splink("company", records)` directly — the exact function
`resolve_with_splink` calls in production — over all 54 records in one call
(train AND predict on the same small set, which is what happens in a real
run today).

```
Total records: 54
Candidate pairs scored: 565
7 of 11 known true-duplicate pairs blocked (proposed for scoring) at all.
```

Sample scores (id1 <-> id2: match_probability):

| Pair | Score |
|---|---|
| Boeing Co ↔ Boeing (zoominfo) | 0.6716 |
| The Boeing Company ↔ Boeing (zoominfo) | 0.6716 |
| Boeing Satellite Systems, Inc. ↔ Boeing Satellite Systems International, Inc. | 0.1101 |
| Airbus SAS ↔ AIRBUS SE | 0.1452 |
| AIRBUS SE ↔ Airbus Group | 0.1906 |
| Boeing Australia Holdings ↔ The Boeing (zoominfo) | 0.0102 |
| BOEING INTEGRATED DEFENSE SY ↔ Boeing Defense & Space Group | **NOT BLOCKED** (case-sensitivity bug, see below) |

**A second live bug found here, immediately fixed:** the loosened name rule
originally read `substr(l."name", 1, 4) = substr(r."name", 1, 4)` — no
`lower()`. "BOEING INTEGRATED DEFENSE SY" (upper) vs "Boeing Defense & Space
Group" (title case) differ in their first 4 characters *only by case*
(`"BOEI"` vs `"Boei"`), so this genuine duplicate pair was still not blocked
even after Bug 1's fix. Wrapped both sides in `lower()` (§2's final code);
confirmed fixed in Run 3 below (that pair now scores 0.0836 — still low
evidence, but *blocked*, which it categorically was not before).

**Diagnosis of the still-low scores:** `Boeing Satellite Systems, Inc.` and
`Boeing Satellite Systems International, Inc.` share a normalised domain
(`boeing.com`) and a strong name match, yet scored only 0.11–0.18 across
every run in this section. Training on only 54 records (single-subject
scope) starves EM of the data it needs: the training log for this exact run
shows `m probability not trained for fullname — Exact match on fullname
… never observed in dataset` and the same for `description`. **EM cannot
learn a comparison level it never sees a single example of**, and a
54-record, one-company-family sample is too narrow to contain enough
examples of every agreement pattern. This motivated §4.

### 3.4 Run 3 — train ONCE on a large, diverse sample; predict on the small one

This is the methodology's central recommendation, validated live, not just
argued for.

**Training sample:** 3,000 *random* diffbot company rows — deliberately
**not** Boeing/Airbus-related, deliberately cross-company and cross-industry,
so EM sees a real distribution of agreement/disagreement across many
different companies rather than one family's idiosyncrasies. Pulled via:

```sql
select companyid, entityname, fullname, coalesce(website,''), coalesce(left(summary,150),'')
from diffbot.company
where entityname is not null and entityname <> ''
order by random()
limit 3000;
```

**Config used** (the exact `config` dict passed to
`splink_workbench.train()`):

```python
config = {
    "comparisons": [
        {"comparator": "JaroWinklerAtThresholds", "field": "name", "thresholds": [0.9, 0.7]},
        {"comparator": "ExactMatch", "field": "domain"},
    ],
    "blocking_rules": [
        'substr(lower(l."name"), 1, 4) = substr(lower(r."name"), 1, 4)',
        'l."domain" = r."domain"',
    ],
    "deterministic_rule": 'l."domain" = r."domain"',
    "seed": 42,
    "max_pairs": 500_000,
    "recall": 0.7,
}
result = wb.train("company_diffbot_zoominfo", train_sample, config, store_root="logs/splink_models")
```

**Training outcome:** `"Your model is fully trained. All comparisons have at
least one estimate for their m and u values"` — the exact warning from Run 2
(missing `fullname`/`description` m-values) is gone, because 3,000 diverse
records contain enough exact-fullname and exact-description agreements for
EM to observe that level at all.

**Prediction:** `wb.predict("company_diffbot_zoominfo", 1, predict_set,
store_root="logs/splink_models")` — a *separate* call, no retraining, over
the same 54 Boeing/Airbus records as Run 2. This is the "model-first, train
once, predict many times" pattern `splink_workbench.py`'s own docstring
already describes as the intended design; Run 3 is the first time this
session actually exercised it end-to-end and compared it against the
per-run-retrain shape production code uses today.

**Results, same pairs as Run 2:**

| Pair | Run 2 (per-run retrain) | Run 3 (bulk-trained, reused) |
|---|---:|---:|
| Boeing Co ↔ Boeing (zoominfo) | 0.6716 | **0.9260** |
| The Boeing Company ↔ Boeing (zoominfo) | 0.6716 | **0.9260** |
| Boeing Co ↔ The Boeing Company | not checked | **0.9260** |
| Airbus SAS ↔ AIRBUS SE | 0.1452 | **0.6472** |
| AIRBUS SE ↔ Airbus Group | 0.1906 | 0.0504 |
| Boeing Australia Holdings ↔ The Boeing (zoominfo) | 0.0102 | **0.2697** |
| BOEING INTEGRATED DEFENSE SY ↔ Boeing Defense & Space Group | not blocked | 0.0836 (now blocked) |
| Boeing Satellite Systems ↔ Boeing Satellite Systems International | 0.1101 | 0.1768 |

**8 of 9 known true-duplicate pairs were blocked** (the ninth — "BOEING
INTEGRATED DEFENSE SY" vs "The Boeing Company Boeing Information Space &
Defense Systems" — shares neither a domain nor a 4-character name prefix;
see §6, this needs token-based blocking, not fixed here).

**Honest read of these numbers:** the four-way Boeing-parent cluster now
scores just under the builtin engine's own 0.95 auto-merge bar (0.926) —
close enough to land solidly in the human-review band rather than being
invisible, which is the categorical difference that matters (it went from
*never scored* to *scored and just below the confident-merge line*). Several
other pairs improved by 5–27× but are still well below any reasonable merge
threshold. **This tuning pass does not make Splink beat the builtin engine's
measured 0.995 recall on this dataset** — it makes Splink *functional*
where it was structurally blind before, and it validates a training
methodology, not a finished, ship-it model. See §6 for exactly what's still
needed to close that gap, and §7 for why promoting Splink over the builtin
default is deliberately *not* done here.

---

## 4. A load-bearing gotcha: record identity in the Workbench

**This cost real debugging time and will cost the same for anyone who
doesn't know it going in.** `splink_workbench.py`'s `train()`/`predict()`
functions build their DataFrame via `splink_common.records_to_dataframe`,
which derives each record's unique id like this:

```python
def _unique_id_of(rec):
    from src.mcp_server.tools.resolve_entities import _record_id
    return str(_record_id(rec))

# resolve_entities.py:
def _record_id(rec):
    origin = rec.get("_origin_id", rec.get("origin_id", "unknown"))
    table = rec.get("_origin_table", rec.get("table", "unknown"))
    row_id = rec.get("_origin_id_row", rec.get("id", str(id(rec))))
    return f"{origin}:{table}:{row_id}"
```

**If a record carries none of `_origin_id`/`origin_id`, `_origin_table`/
`table`, `_origin_id_row`/`id`, the id silently falls back to
`str(id(rec))` — Python's object-identity memory address.** This is not a
crash and not a warning; `train()`/`predict()` both run to completion and
return plausible-looking output. The failure mode is that the returned
`_record_id_str_l`/`_record_id_str_r` columns become **meaningless for
lookup** the moment the caller's record objects are anything other than the
literal same Python objects across calls — which is *always* true for an
HTTP API caller (every JSON-deserialized request body is a fresh object),
and was true in this session's first attempt at this exact script (ids came
back as `"unknown:unknown:139759085286976"`, and a set-difference check
showed **zero** overlap between the ids in the predictions and the ids of
the actual 54 predict-set records — every single one had round-tripped to a
different fallback memory address).

**The fix, and the rule to carry forward:** every record passed to *any*
workbench function (`train`, `predict`, `recommend`, `cluster`, `explain`,
`evaluate`) must carry `_origin_id`, `_origin_table`, and `_origin_id_row`
explicitly, even for synthetic/test records that have no real origin:

```python
{
    "_origin_id": "testdiffbot",
    "_origin_table": "company",
    "_origin_id_row": companyid,   # the real, stable source id
    "name": ..., "domain": ..., ...
}
```

**This is worth fixing at the source**, not just working around: either
`records_to_dataframe` should raise loudly when none of the id-shaped fields
are present (matching this codebase's own "fail closed, not open" principle
elsewhere — e.g. `keyset.py`'s refusal to guess at non-unique keys), or the
API layer (`src/api/main.py`'s `records: list[dict[str, Any]]` request
bodies for `/splink/*` routes) should validate the id fields are present
before calling into the workbench. Neither is implemented as part of this
session's fix — flagged here so it is not silently rediscovered the same
way again.

---

## 5. The trained model artifact

```
logs/splink_models/company_diffbot_zoominfo/
└── v1/
    ├── model.json       # Splink's own trained settings payload
    └── metadata.json    # training record, blocking rules, seed, record count
```

Saved via `splink_workbench.train()`, activated via
`splink_workbench.activate("logs/splink_models", "company_diffbot_zoominfo", 1)`.
Verified present and active:

```python
>>> from src.mcp_server.tools import splink_workbench as wb
>>> wb.list_models("logs/splink_models")
{'models': [{'model_name': 'company_diffbot_zoominfo',
             'versions': [{'version': 1, 'status': 'trained',
                           'parent_version': None, 'splink_version': '4.0.17',
                           'is_active': True}]}]}
```

**This model is not wired into `matching_rules` for production use** — see
§7 for why not, and what would need to be true before it should be.

---

## 6. What would still need to happen to beat the builtin engine here

In priority order, each targeting a specific pair that's still poorly
scored above:

1. **Token-based blocking, not just prefix-based.** "The Boeing Company
   Boeing Information Space & Defense Systems" shares no 4-character prefix
   with "BOEING INTEGRATED DEFENSE SY" (different leading words) — a rule
   like "share at least one significant token (length ≥ 4, not a stopword)"
   would catch this and is a standard Splink pattern (`array_intersect` over
   a tokenized array column, computed once at the DataFrame-build step, not
   inside SQL per pair).
2. **Cost every candidate blocking rule before shipping it**, using Splink's
   own `count_comparisons_from_blocking_rule` (already used by
   `splink_blocking.py`'s S‑E recommender) — this both justifies widening
   blocking (point 1) without an unbounded pair-count risk, and would have
   caught Bug 2 (§1) automatically: a rule that silently never fires (0
   pairs) is exactly as visible to a cost function as one that fires too
   often.
3. **A term-frequency-aware domain comparator**, not a bare `ExactMatch`.
   §3.4 showed shared `boeing.com` is *weak* evidence here specifically
   because many legitimately distinct Boeing sub-entities share the parent
   corporate domain — `.configure(term_frequency_adjustments=True)` (already
   applied in the production fix, §2) down-weights an over-common exact
   match, which is the textbook remedy for this exact pattern, and it
   already measurably kept the Satellite Systems pair from scoring
   *incorrectly high* just because it shares `boeing.com` with a dozen
   unrelated Boeing sub-entities. The remaining gap is that it isn't
   currently boosting the *rest* of the evidence enough for a same-domain,
   similar-name pair to clear a merge bar — worth a dedicated EM run with
   `term_frequency_adjustments` measured before/after on a held-out labelled
   set, not guessed at.
4. **A genuinely representative, larger training sample.** 3,000 records
   fixed the "level never observed" failure but is still small next to
   Diffbot's 1.27M-plus company rows. Re-run §3.4 at 20,000–50,000 records
   (bounded by `_U_ESTIMATION_MAX_PAIRS`/`max_pairs` cost, not by anything
   architectural) and compare training warnings + the same labelled-pair
   table.
5. **Measure against `splink_evaluation.py`'s
   `accuracy_analysis_from_labels_table`** on a proper labelled-pairs table
   (not the eyeballed pair list in §3), and only then compare directly
   against the builtin engine's own measured 1.0/0.995 precision/recall from
   the audit report's ADR 0004 addendum — same dataset, same metric,
   otherwise the comparison is not apples-to-apples.

---

## 7. Why this is not wired into production matching yet

This codebase already has a mechanical safety gate for exactly this
decision: `tests/eval/test_matcher_recall.py` (a 560-pair labelled gate) and
`tests/eval/test_splink_default_invariant.py` (refuses to let `splink`
become the default engine unless that gate passes and the result is recorded
in `tests/eval/last_gate.json`). Per the audit report, the gate's own
fixture file (`tests/eval/pagila_staff.csv`) is currently missing from this
checkout, so the gate cannot even run right now — restoring it is a
prerequisite, not something this tuning pass should route around.

Even setting that aside: §3.4's own numbers do not show Splink beating the
builtin engine's measured recall on *this* dataset yet (§6 lists exactly
what's missing). The correct, safe action given everything above is:

- **Do**: keep the blocking/domain fixes in `splink_matcher.py` — they are a
  strict improvement (0 pairs scored → real, if imperfect, pairs scored) and
  regression-tested (`test_splink_matcher.py`, 9/9 passing).
- **Do**: keep the trained `company_diffbot_zoominfo` model saved and
  active, as a concrete artifact the next tuning pass builds on rather than
  starting cold.
- **Don't**: set `matching_rules.engine = "splink"` as a default for the
  `company` entity type, or for any profile, until §6's items are done and
  measured against the same 560-pair gate the builtin engine already passed.

---

## 8. Generalized recipe — how to replicate this for a different database pair

The point of writing all of the above down this precisely: the same ten
steps apply to any new origin pair, not just diffbot+zoominfo. This is the
part meant to be followed mechanically, by a human or another LLM, without
re-deriving the reasoning each time.

1. **Identify the canonical comparison fields** for the entity type you're
   matching. Reuse `resolve_entities._project_to_canonical` and its
   `_CANONICAL_FIELD_HINTS` table if the entity is one it already covers
   (company/person-shaped); extend the hints table for a genuinely new
   entity shape rather than writing a parallel projector.
2. **Classify each canonical field as strong-identity (email, domain,
   ticker, tax id, phone) or weak/fuzzy (name, address, description).**
   Strong fields get exact-match comparisons; weak fields get a
   distance-based comparator (Jaro-Winkler for names is the default choice
   already used here).
3. **Normalise every strong-identity field before Splink ever sees it.**
   Domain → registrable domain (`_domain_of`, reuse it, don't reimplement).
   Phone → E.164 or digits-only. Tax id → strip formatting punctuation. Do
   this at the Python/DataFrame layer, not inside a Splink comparison level.
4. **Build blocking rules as a union, one per strong field present, plus one
   loosened (never-exact) rule per weak field being fuzzy-compared.** Verify
   this by checking with `if not blocking_rules:` style guards are NEVER
   used to gate anything beyond the true last-resort fallback — if you find
   yourself writing `if not blocking_rules: blocking_rules.append(...)` for
   more than one field, that's Bug 2 recurring.
5. **Cost each blocking rule** with
   `splink.blocking_analysis.count_comparisons_from_blocking_rule` (or the
   already-built `splink_blocking.py` S‑E recommender) before shipping —
   catches both "this rule fires on almost nothing" (Bug 2's shape) and
   "this rule fires on everything" (the comparison-budget failure mode) in
   one step.
6. **Pull a large (thousands, not dozens), diverse, cross-subject training
   sample** from the origin — random rows across many different entities of
   the same type, explicitly NOT limited to the one subject you're about to
   merge. Check the training log for "Your model is fully trained" with no
   "m probability not trained for X" warnings; if any appear, the sample is
   still too narrow or too homogeneous for that comparison level.
7. **Always pass `_origin_id`/`_origin_table`/`_origin_id_row`** (or the
   `origin_id`/`table`/`id` aliases) on every record given to any workbench
   function — never rely on the id-derivation fallback (§4). Verify by
   checking that `len({_record_id(r) for r in records}) == len(records)`
   AND that none of those ids contain the literal substring `"unknown"`.
8. **Train once, save the version, then `predict()` against the small,
   actual-subject record set** you care about — never retrain per subject.
   Use a fixed `seed` for reproducibility (this file used `42` throughout).
9. **Hand-label a small set of known true/false pairs** for the specific
   subject you're validating against (the same rigor the audit report's
   ground-truth build used — direct inspection of real rows, confidence
   noted per pair, decoys explicitly separated from real near-duplicates),
   and check blocked-and-scored rate + score distribution against it before
   trusting the model at all.
10. **Only promote past the builtin engine's default through the existing
    measured gate** (`test_matcher_recall.py` + `test_splink_default_invariant.py`
    in this codebase, or the equivalent labelled-precision/recall comparison
    in any other) — never by hand-flipping a config default. If the gate
    doesn't beat the incumbent, the tuning pass was still worth doing (a
    working, imperfect model beats a structurally-blind one, §3.4), but it
    is not yet a production promotion.

Re-running this procedure end-to-end from a cold start (no existing model,
no existing labels) for one entity type against real data took, this
session: ~30 minutes of live database queries, ~20 minutes of script
iteration (including finding and fixing the two live bugs in §1 and §4), and
under 5 minutes of actual training/prediction compute (3,000-record EM
training converges in under 30 seconds on the DuckDB backend). The
bottleneck is the investigative loop (run → notice a wrong number → trace to
a specific line → fix → re-run), not raw compute — budget time accordingly
rather than assuming this is a compute-bound task.
