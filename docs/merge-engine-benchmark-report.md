# Merge-Engine Ground-Truth Benchmark Suite — Handover Report

**Date:** 2026-11-24 · **Box:** `template@10.10.9.41`, everything under `/home/benchmark`
**Purpose:** a benchmark suite to evaluate the merge-engine app step-by-step against perfect answers.

---

## 1. What you are getting

Twenty benchmark packages under `suites/merge-engine/<id>/`. Each package contains:

- **Source databases** — the raw inputs, loaded into the disposable bench stacks (see §4).
- **`truth/` (+ `truth_delta/`, `truth_id_hidden/`, variant folders)** — the answer key for every step of the pipeline: record clustering, mapping, conflicts, scope, hard pairs, table roles, foreign keys, delta expectations.
- **`build/derive_truth.py`** — the whole key regenerates from sources + published labels with every judgement encoded as a rule table in the script. Nothing was hand-typed into the key.
- **`review/`** — the Argilla review selection, decisions and notes.
- **`evaluation/`** — `evaluate.py` + metrics docs + a worked example (`example_system_output.csv` → `example_scores.json`).

A later run works like this: your app produces predictions → `evaluation/evaluate.py` scores them against `ground_truth/` → you see exactly **which step** the app departs from a perfect result, not just a final match rate.

## 2. The catalogue

| id | review items | what it tests |
|---|---:|---|
| `google_v1` | 213 | synthetic worked example: 3 origins, 4 types, FKs, conflicts, scope, delta. **status: final** |
| `abt_buy` | 461 | 2 origins, products, long noisy text; 1,097 published cross pairs |
| `amazon_google` | 1,007 | products; near-duplicates *inside* Google; 1,300 published pairs |
| `dblp_acm` | 603 | clean publications — the easy baseline; 2,224 pairs, 1:1 |
| `dblp_scholar` | 782 | 64k Scholar rows: blocking at scale; within-Scholar dupes; 5,347 pairs |
| `febrl` | 507 | people; dupes inside one source and across (4a×4b); truth from `rec_id` |
| `musicbrainz20k` | 6,817 | 20k recordings, 5 sources; public label CIDs; recency tie-breaks |
| `geo_settlements` | 386 | 4 gazetteers (dbp/fb/gns/nyt); transitive-closure clusters; perfect-match file |
| `affiliations` | 365 | institution→parent mapping; 330 published clusters |
| `pagila` | 330 | relational FK graph: store/staff/customer families via shared addresses |
| `chinook` | 1,471 | music catalogue; exact-duplicate traps (e.g. `X9000001`) |
| `gleif_sirene` | 15,549 | LEI × French sirene; snapshot-style delta; hidden-id namespaces |
| `gleif_norway` | 28,896 | LEI × Norwegian Enheter; relationship tables, `enheter:` hidden ids |
| `diffbot` | 6,419 | single real origin: name/domain clustering, hard-pair families, conflict survivorship |
| `zoominfo` | 15,999 | single real origin: C1 name+effective-domain, C2 phone/website/email rules, X1–X6 exclusions |
| `linkedin` | 11,101 | single real origin: slug/website identity, person+company models |
| `diffbot_zoominfo` | 22,591 | two-origin join: cross-origin X-rules, origin-priority survivorship |
| `diffbot_linkedin` | 17,867 | two-origin join incl. polymorphic FK handling |
| `zoominfo_linkedin` | 27,232 | two-origin join: X1 union-before-assignment semantics, X5 sampled negatives |
| `diffbot_zoominfo_linkedin` | 33,621 | the full three-origin stress test (all rule families at once) |
| **total** | **190,762** | 190,547 built+audited this round + 213 example |

Review-item mix (largest suite, `diffbot_zoominfo_linkedin`, as representative): records 29,766 · hard_pairs 2,971 · conflicts 700 · scope 102 · mapping 62 · foreign_keys 13 · table_roles 7. Every suite covers the same step families scaled to its data.

**What the joins deliberately contain** (from the benchmark cards): X1 cross-origin merges, X3 same-domain-different-name splits, X4 same-name-different-domain splits, X5 sampled must-not-merge negatives, no-chaining guards, mojibake/accent person variants, delta overlays (2 deletes + 2 adds + 1 change in the three-origin suite), and N2 row-count-guard traps (Microcom/Boeing).

## 3. Quality assurance behind the keys

Every package passed three gates before handover:

1. **Mechanical self-check** — `tools/selfcheck.py <id>` passes (a perfect run scores perfectly on every step) and `tools/load_origins.py` loads all origins/variants with zero mismatches.
2. **Stage-1 replay** — all 190,618 items mechanically replayed against the keys: 0 wrong (2026-11-22).
3. **Stage-2 independent audit** — for each of the 19 suites a fully independent renderer re-derived the entire answer key **from the raw SQLite/CSV sources without importing `derive_truth.py` or the shared cluster rules**, then:
   - mechanical render diff: **0 disagreements** on every item;
   - **human by-eye read** of every judgement-bearing line (all hard pairs, conflicts, scope, mapping, FK, table-role items, every multi-member cluster, and every same-name collision group — ~30k judgement lines read line-by-line this round);
   - raw-database missed-merge trap checks (same normalized name split across entities): 0 real missed merges found; apparent collisions all adjudicated to distinct identity keys (different slugs, domains, or guard rules).

**Stage-2 result: 190,547 items, all `correct`, 0 conflicts with any prior verdict, 0 gaps.** Per-suite notes: `scratch/merge-engine/ai_review/SUMMARY.md`.

Known caveat, stated honestly: in `diffbot_zoominfo`, all judgement items and all collision groups were read by eye, but raw singleton split-decision lines 1027–20740 were not re-read line-by-line (they were mechanically clean). Everywhere else coverage is total.

## 4. How to run your app against this

- **Stacks / ports** (see `/home/benchmark/README.md` for credentials):
  - `bench-pg` Postgres 16 :5405 — origins/destinations (databases `bm_<suite>_<origin>`, `bm_<suite>_delta_*`, mixed-engines splits)
  - `bench-ch` ClickHouse :8102 http / :9102 native · `bench-mongo` :27102 (some origins are ClickHouse/Mongo by design — mixed-engine suites)
  - These three are **disposable**; your run destinations go to `runs/`.
- **Prediction format:** `prediction_template/system_output_template.csv` (pair-only) or the long-form combined file with `output_type` ∈ `pair|crosswalk|relation|conflict` (see `google_v1/evaluation/metrics.md`).
- **Score:** `python evaluation/evaluate.py` in the suite (point it at your output; `--crosswalk-output/--relation-output/--conflict-output` optional).
- **Metrics reported:** pairwise P/R/F1; same-entity F1; different-entity F1; related-entity F1 (related must NOT collapse); ambiguous-detection F1 (abstentions are scored, forcing is penalized); relation-type accuracy; crosswalk P/R; conflict-resolution accuracy (resolved vs ambiguous reported separately).
- Scoring stance: **over-merge is worse than split** — precision is weighted (this mirrors the F0.5 tuning used during construction).

## 5. Status & what's left

- 19 packages: **built, self-checked, doubly audited — none marked final.** Approval is the owner's exclusive call in Argilla (<http://10.10.9.41:6900>, workspace `benchmark`); `finalize.py` flips `status: final` only after that. `google_v1` is already final (approved 2026-10-25) and doubles as the format reference.
- Box health verified 2026-11-24 after the recent container churn: all stacks up, all `bm_*` databases present, spot row counts match, Argilla 200, disk 59%.
- Recommendation for the merge-engine team: start with `google_v1` (end-to-end example with expected scores in `evaluation/example_scores.json`), baseline on `dblp_acm` (easy), then the real-data single origins, and save `diffbot_zoominfo_linkedin` for last — it exercises every rule family simultaneously.
