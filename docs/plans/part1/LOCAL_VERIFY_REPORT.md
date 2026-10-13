# Part 1 — local live verification report

**Status: S3 public benchmarks done (11 scenarios); S4 gate = STOP (6 scenario failures) — S5 not started, waiting for the user's decision.**
Resume from this file + `git log`; every number below is regenerable from the commands in "Regenerate".

| | |
|---|---|
| dates | 2026-10-12 → 2026-10-13 |
| commit verified ("after") | `6d7c4b4` (`feat/part1-fixes`, Part 1 head) |
| "before" | `507f9d4` (`origin/main` = merge-base), detached worktree `.tmp/wt_main` (never checked out here) |
| environment | Linux, `.venv311` Python 3.11.15, `TMPDIR=.tmp/`; one app process per run, own cwd under `.tmp/verify_local/runs/<run>/` (no profile / LLM cache / resolve cache / checkpoint carries over); code root pinned by `PYTHONPATH` and asserted at start (the editable install points at the branch) |
| LLM | `Qwen/Qwen3.6-35B-A3B-FP8`, LAN llama.cpp endpoint; persistent LLM cache OFF for every run (user decision); one uncached call verified 1.36 s before S1 |
| databases written | only the dedicated container `merge_bench_pg` (PostgreSQL 17.11, 127.0.0.1:5439, loopback trust auth, user decision): origins `bm_<scenario>_<source>`, destinations `bm_dst_<scenario>_<version>_<tag>`. No other database was read or written. |
| driver | full app via REST (user decision): `/api/workflow/start`, every gate approved with a plain `{"decision":"approve"}` (oracle answers only in the P9/P15 run), scoring by SQL on `merge_engine_entity_map` |
| wall time | S1 ≈ 3 min; S3 ≈ 22 runs, 5–35 min each (runs paired before/after in parallel; LLM endpoint is the bottleneck, see D13) |

## Datasets (all under `.tmp/benchmarks/`, gitignored; 10.0 MB downloaded of the 2 GB cap)

Licences read: Leipzig sets — page statement "made available by the database group of Prof. Erhard Rahm under the Creative Commons license" (each listed CC BY 4.0, cite the site + VLDB 2010 / ADBIS 2017). Febrl — ANU Open Source License (MPL 1.1 derived; free use and redistribution of the data files), via the Python Record Linkage Toolkit repo (BSD-3). Magellan/DeepMatcher sets were **not used**: their labels cover blocked candidate pairs only, not a perfect mapping, so full-table recall cannot be scored (rule 5). Cora: licence not established → not used.

| dataset | URL | bytes | SHA-256 |
|---|---|---|---|
| abt_buy | https://dbs.uni-leipzig.de/files/datasets/Abt-Buy.zip | 134,745 | `b9ff2937d97371b7a00b0a97033213bfbdee24e8f1a0718a41ea8364472a8ff8` |
| affiliations | https://dbs.uni-leipzig.de/files/datasets/affiliationstrings.zip | 137,960 | `cc611eb36dc276967dd1b832859f9f1adc6a725d1b0924ad6ae24b3b17f73219` |
| amazon_google | https://dbs.uni-leipzig.de/files/datasets/Amazon-GoogleProducts.zip | 904,417 | `fcd72e223d51ce5b0d6b1d68034a438ba95cf2940431e209a57910b067cc4811` |
| dblp_acm | https://dbs.uni-leipzig.de/files/datasets/DBLP-ACM.zip | 270,239 | `e37e7ed8e06722499e6d9e94583fdec279207b13ce6270a37595b6a5d594bc40` |
| dblp_scholar | https://dbs.uni-leipzig.de/files/datasets/DBLP-Scholar.zip | 4,160,005 | `80b9ddbe89d6b78ab583f801e86a8c451e94dbf1a57ba88dfbd180245cff138b` |
| geo_settlements | https://dbs.uni-leipzig.de/files/datasets/geographicalSettelments.zip | 98,276 | `76a5b8d261607f6c77a9cd79eaf4b0bb60e8b4ebfad3802b4bd01a02d97cc02e` |
| musicbrainz20k | https://dbs.uni-leipzig.de/files/datasets/saeedi/musicbrainz-20-A01.csv.dapo | 2,170,333 | `527a94f24f7e813a9bc3fef35a635f13e195516966b308140a0dd2926afbb97d` |
| febrl dataset3 | https://raw.githubusercontent.com/J535D165/recordlinkage/master/recordlinkage/datasets/febrl/dataset3.csv | 514,019 | `0e667330458ae88dd3d6b9cab39af4e7629a2fef98a810d0ea5f15e48220bdbf` |
| febrl dataset4a | …/febrl/dataset4a.csv | 519,618 | `07c7cb3f0a8d88180e80317f2a60499dee4e8324a44c38059f4e7fed0a8b4488` |
| febrl dataset4b | …/febrl/dataset4b.csv | 515,463 | `2eed76c99fa2237be3ec013a123427926d4158abcb3a8f65874d6c7f1358cf2c` |
| febrl LICENSE | https://raw.githubusercontent.com/J535D165/recordlinkage/master/LICENSE | 1,499 | `2418e3a4fda022399a8a96f261836164c2da1b09488030182dd4eb517caf01c5` |

(febrl dataset1/2 were downloaded — `637acf9d…`, `0c86efe0…` — but not used.) Full manifest incl. extracted files: `.tmp/benchmarks/MANIFEST.json`.

Ground-truth facts that matter for reading the numbers: Geographic Settlements' perfect mapping puts id `4216` in two clusters (excluded from data and truth); its readme says "5 sources", the data has 4. Abt-Buy / Amazon-Google / DBLP-Scholar truth is many-to-many, so its connected components contain within-source pairs (21 / 253 / 8,290). MusicBrainz-20K and Geo have **no** within-source duplicates; Affiliations has only within-source ones.

## Summary

### Scenarios (pass rule of S2, applied mechanically by `scripts/verify_local/compare.py`)

| scenario | shape | verdict | key before → after (counts) | evidence |
|---|---|---|---|---|
| febrl4 | 2 origins, persons, 5,000 true pairs | **PASS** | FN 105 → 11, FP 0 → 0; F1 0.989 → 0.999 | `runs/febrl4_*_r1` |
| febrl3 | 1 origin, persons, within-source dups | **PASS** | FN 335 → 12, FP 0 → **23**, over-merged 0 → 8 (allowed 12); F1 0.974 → 0.997 | `runs/febrl3_*_r1` |
| febrl3x2 | febrl3 dealt into 2 origins (within + across) | **PASS** | FN 335 → 12, FP 0 → 23 (9 within, 14 cross); F1 0.974 → 0.997 | `runs/febrl3x2_*_r1` |
| geo | 4 origins, settlements | **PASS** | TP 1,308 → 1,983, FP 0 → 3; recall 0.298 → 0.452 | `runs/geo_*_r1` |
| affiliations | 1 origin, organisation names only | **PASS (vacuous)** | 0 merges on both (FN 16,795 = 16,795) — see PRE-D1 | `runs/affiliations_*_r1` |
| plant_febrl4 | febrl4 + 20 planted bridges | **FAIL** (rule 8) | rules 1–7 pass (FN 107 → 8); P16 flags 20/20 planted clusters, **P10 does not refuse** (would_fail False) | `runs/plant_febrl4_*_r1` |
| abt_buy | 2 origins, products | **FAIL** (rule 2) | better than main on every metric (FP 2,747 → 1,219, F1 0.013 → 0.026) but largest cluster 16 vs true 3 (main 19); both unusable | `runs/abt_buy_*_r1` |
| dblp_acm | 2 origins, publications | **FAIL** (rules 1, 2, 3) | recall 0.905 → 0.983 but cross-source precision 0.925 → 0.903, B-cubed precision 0.963 → 0.950, over-merged 65 → 107 (allowed 87), largest 18 (main 22); reproduced in a clean r2 | `runs/dblp_acm_*_r2` (r1 identical) |
| amazon_google | 2 origins, products, long descriptions | **FAIL** (rule 5) | main completed in 21 min; **after never left resolve_entities in 140 min** (driver timeout) — D1 | `runs/amazon_google_*_r1` |
| musicbrainz | 5 origins, recordings, cross-source truth only | **FAIL** (rule 2) | recall 0.433 → 0.661, F1 0.592 → 0.782, over-merged 136 → 124; but largest cluster 14 vs true 5 (main 10 = exactly 2×); cross-source precision 0.994 → 0.984 (inside the 1 pp tolerance) | `runs/musicbrainz_*_r1` |
| dblp_scholar | 2 origins, 66,879 records | **FAIL** (rule 5) | main completed in 620 s (TP 4,880 / FP 5,203 / FN 8,883, F1 0.409); after still inside resolve_entities after ≈ 70 min (0 destination rows), stopped by decision of the user — D1 (profile: ≈ 2.9 ms per pair in `_token_ratio`) | `runs/dblp_scholar_*_r1` |

### Items (what the public benchmarks can show; S5 items are UNVERIFIED until the user's databases are restored)

| item | verdict | before → after (counts) | evidence / defect |
|---|---|---|---|
| P1 | UNVERIFIED | benchmarks have no concatenated lowercase identifiers; Amazon `title → name` mapped identically on both (0.9) | S5 |
| P2 | **PARTIAL** | budget gone: febrl4 main C = 250,000 (cap), `uncompared_pairs` 993,586 vs true discard 466,880 (over-count ×2.1, as warned), 79 true pairs lost to the budget → after PC 1.000; musicbrainz main PC 0.536 at the cap → 0.990; dblp_scholar main reports 26.1 M uncompared; `uncompared_pairs` 0. But block filtering discards pairs it never counts as pairs (Abt-Buy G − C = 1,213,649 vs 161,535 reported) | blocking probe; **D9** |
| P3, P4, P7, P12, P17(fix) | UNVERIFIED | single-table benchmarks: no INDs, junctions, catalog/ETL columns or broken DDL | S5 |
| P5 | **PARTIAL** | PC ≥ main on 6 of 7 sets (febrl4 0.980 → 1.000, febrl3 0.981 → 0.999, dblp_acm 0.987 → 1.000, affiliations 0.459 → 0.757, musicbrainz 0.536 → 0.990, geo 0.973 = 0.973), PQ higher on all but geo (0.380 → 0.241); **Abt-Buy PC 0.928 → 0.897** (115 vs 80 true pairs lost in blocking) | blocking probe |
| P6 | UNVERIFIED | no parent/subsidiary truth in public sets. Observation: Abt-Buy within-source variant FPs 2,716 → 1,207 (still unusable) | — |
| P8 | **PARTIAL** | one pair per call confirmed (≈ 200 calls/entity vs 4–6 batched on main); precision of LLM-decided merges vs truth: persons 37/37, DBLP-ACM 10/17 (r2) and 11/14 (r1), **Abt-Buy 2/34**; wall time 2–4× main; under a serial endpoint a parallel run's preflight times out | audit probe; **D13** |
| P9 | **FAIL** | 3 oracle answers sent through the REST API: no memory file written; run 2 with the same memory path paused at the matches gate with 5 pending pairs (asks again) | **D2** |
| P10 | **FAIL** | planted bridges and every natural over-merge pass; single-entity proposals = 2 × own value and the largest-cluster slack beats the 1/2 cap (gate shows measured 0.69 > threshold 0.50, violated False) | **D3** |
| P11 | PASS (weak) | map_fields share 0.4–6.6 % of wall clock on 9/10 runs (Affiliations 44 % → 35 %); not a bottleneck on these sets; concurrency bound not observed directly | report timings |
| P13 | **PARTIAL** | model fitted on every branch run; but precision at the auto-match bar before clustering is lower: FP@0.95 febrl4 1 → 44, febrl3 0 → 41, dblp_acm 636 → 814 (clustering removes them on persons, not on DBLP) | score probe; **D12** |
| P14 | **FAIL** | persons recall@0.70 0.979 → 0.999 (febrl4), 0.981 → 0.995 (febrl3) = the intended gain; **but** (a) cost quadratic in tokens: Amazon-Google never finishes (**D1**); (b) "Addendum to X" auto-matches X (**D5**); (c) Abt-Buy cross-source recall@0.70 0.376 → 0.115 (**D6**) | score probe, repros |
| P15 | **FAIL** | band recorded (`band_summary`) but at most 50 pairs listed (Abt-Buy: 1,330 predicted possible, 50 shown) and the human's answers cannot reach `apply_band_decisions` through the API: 3 oracle "merge" answers → all 3 pairs still apart in the destination | **D2**, **D15** |
| P16 | **FAIL** | signals printed every run (homonymy, name variation, Hill numbers, B-cubed "not available" on run one); flags 20/20 planted clusters; **false flags 888 of 4,989 correct clusters (17.8 %) on febrl4, 450/1,167 (38.6 %) on febrl3**; real over-merges caught 1/8 (febrl3), 15/301 (Abt-Buy). B-cubed on run two **verified**: febrl4 run p9r2 with run p9r1's history prints 0.99990, the scorer on the two entity maps (SQL) gives 0.99990 | **D4** |
| P17 | PASS (weak) | `destination_defects = []` on all 10 healthy benchmark destinations (no false defect); the broken-table case is S5 | gate payloads |
| P18 | UNVERIFIED | `longest` / `first_seen` appear as winning rules (geo: 3 + 1 of 2,999 conflicts); numeric disagreement only fires under `numeric_max/min`, never used by these runs (geo lat/lon conflicts in 370/403 clusters go unflagged under the default `priority` policy — within P18's stated scope) | S5 |
| P19 | not re-verified | accepted-cloud; the blocking probe reproduces the engine's candidate counts exactly (Abt-Buy 30,680) | — |
| P20 | **FAIL** | over-merged clusters febrl3 0 → 8, febrl3x2 0 → 8, plant 18 → 20, geo 0 → 1, Abt-Buy 304 → 301, **DBLP-ACM 65 → 107** (rule 3); new over-merges are true pair + related paper chains (D5) | compare |

Item tally (20): **PASS 2** (P11, P17 — both weak, healthy-path only) · **PARTIAL 4** (P2, P5, P8, P13) · **FAIL 6** (P9, P10, P14, P15, P16, P20) · **UNVERIFIED 7** (P1, P3, P4, P6, P7, P12, P18) · **not re-verified 1** (P19, accepted-cloud).
Scenario tally (11): PASS 5 (one vacuous) · FAIL 6.


## S1 — static gate on `feat/part1-fixes` (`6d7c4b4`)

| check | result |
|---|---|
| `python scripts/check.py all` | **11/11 passed** (1 m 22 s; `test` stage 68.9 s) |
| `pytest tests/part1 tests/unit tests/integration -n 4` | **3296 passed, 1 failed, 1 skipped, 28 xfailed** |
| the 1 failure | `tests/part1/test_p19_attack.py::test_deterministic_shuffled_and_dict_order_and_hashseed`, `CalledProcessError`: the test shells out to the hard-coded `.venv/bin/python` (Python 3.14, deps missing; line 145) |
| same check under `.venv311` (code copied verbatim, PYTHONHASHSEED 0 / 1 / 4242) | **identical output for all 3 seeds** (one sha256) → the property holds; the failure is the interpreter path only |

Regenerate: `TMPDIR=$PWD/.tmp .venv311/bin/python scripts/check.py all`;
`TMPDIR=$PWD/.tmp .venv311/bin/python -m pytest tests/part1 tests/unit tests/integration -q -n 4 --basetemp=$PWD/.tmp/pytest_s1 -p no:cacheprovider`.

Defect (P2 priority, test-only): `tests/part1/test_p19_attack.py:145` should use `sys.executable`, not `ROOT / ".venv/bin/python"`.

## S2 — scorer and pass rule (fixed before the first run, confirmed by the user 2026-10-12)

Scorer: `scripts/verify_local/scorer.py`. Pair counts come from the contingency table, so a giant cluster cannot exhaust memory. It reports:
- pairwise TP/FP/FN/P/R/F1, split overall / within-source / cross-source;
- B-cubed P/R/F1;
- cluster-level exact P/R/F1;
- over-merged clusters, split true clusters, largest cluster and its share;
- missing records (in the truth, absent from the entity map: scored as singletons) and unknown records;
- named FP/FN samples per stratum.

Truth = connected components of the published mapping. For Abt-Buy, Amazon-Google and DBLP-Scholar the closure adds within-source pairs (21 / 253 / 8,290) that the pair file does not list.

Predictions come from `merge_engine_entity_map` by direct SQL on the run's destination database, never from the app's status.

Proof both ways:
- `python -m scripts.verify_local.scorer`: synthetic; identity is perfect; an injected FP, FN, dropped record and giant cluster are each caught and named in the right stratum.
- The same proof on all 10 real truth files (`.tmp/verify_local/scorer_proof_real.txt`): identity is perfect everywhere, and each injection is caught and named.

**Pass rule per scenario** (after = `6d7c4b4`, before = `507f9d4`; same data, same scorer, cold LLM cache):
1. Every metric — pairwise P/R/F1 overall, within and cross; B-cubed P/R/F1; cluster F1 — must satisfy **after ≥ before − 0.01** (absolute).
2. No giant cluster: the largest predicted cluster is ≤ 2 × the largest *true* cluster.
3. Over-merged clusters: after ≤ before + max(2, 1 % of true multi-member clusters).
4. Zero new destination columns.
5. The run reaches a terminal state and writes the entity map. A P10 refusal of a healthy benchmark is a **false refusal = FAIL**.
6. Review pairs at the matches gate are reported beside every precision figure. If they exceed 10 % of the scenario's true pairs, a precision PASS becomes **PARTIAL**.
7. If `before` crashes or writes nothing, `after` is at most **PARTIAL**, and only when it meets every structural condition (2-5).
8. Planted over-merge: the P16 name flag must fire on the planted cluster, and the P10 gate must refuse with 0 destination rows written. The plant is designed and confirmed before it runs.

Wall time and LLM calls (count of "LLM response received" lines in the run's own DEBUG log) are reported, not gated.

## S3 — scenario detail

All numbers: `python -m scripts.verify_local.compare <scenario> [--tag r2]` (pairwise counts within / cross, B-cubed, cluster F1, over-merged, largest, wall, LLM calls), `blocking_probe` (C, G, PC, PQ, true discard), `score_probe` (pre-LLM recall@0.70/0.90/0.95, FP@0.95, band), `audit_probe` (merge precision per method). Raw per-run JSON: `.tmp/verify_local/runs/<run>/{result,blocking_probe,score_probe}.json`, gate payloads `gates.jsonl`.

| scenario / version | TP | FP | FN | P | R | F1 | B³ F1 | over-merged | largest (true) | wall s | LLM calls |
|---|---|---|---|---|---|---|---|---|---|---|---|
| febrl4 before | 4,895 | 0 | 105 | 1.000 | 0.979 | 0.989 | 0.995 | 0 | 2 (2) | 280 | 4 |
| febrl4 after | 4,989 | 0 | 11 | 1.000 | 0.998 | 0.999 | 0.999 | 0 | 2 (2) | 421 | 8 |
| febrl3 before | 6,203 | 0 | 335 | 1.000 | 0.949 | 0.974 | 0.986 | 0 | 6 (6) | 316 | 4 |
| febrl3 after | 6,526 | 23 | 12 | 0.997 | 0.998 | 0.997 | 0.998 | 8 | 6 (6) | 1,085 | 20 |
| febrl3x2 before | 6,203 | 0 | 335 | 1.000 | 0.949 | 0.974 | 0.986 | 0 | 6 (6) | 299 | 4 |
| febrl3x2 after | 6,526 | 23 | 12 | 0.997 | 0.998 | 0.997 | 0.998 | 8 | 6 (6) | 599 | 22 |
| geo before | 1,308 | 0 | 3,080 | 1.000 | 0.298 | 0.459 | 0.655 | 0 | 4 (4) | 285 | 6 |
| geo after | 1,983 | 3 | 2,405 | 0.999 | 0.452 | 0.622 | 0.749 | 1 | 4 (4) | 673 | 203 |
| affiliations before | 0 | 0 | 16,795 | — | 0.000 | — | 0.255 | 0 | 1 (47) | 96 | 4 |
| affiliations after | 0 | 0 | 16,795 | — | 0.000 | — | 0.255 | 0 | 1 (47) | 93 | 4 |
| plant_febrl4 before | 4,893 | 36 | 107 | 0.993 | 0.979 | 0.986 | 0.993 | 18 | 3 (2) | 575 | 4 |
| plant_febrl4 after | 4,992 | 40 | 8 | 0.992 | 0.998 | 0.995 | 0.998 | 20 | 3 (2) | 803 | 8 |
| abt_buy before | 26 | 2,747 | 1,092 | 0.009 | 0.023 | 0.013 | 0.567 | 304 | 19 (3) | 443 | 4 |
| abt_buy after | 31 | 1,219 | 1,087 | 0.025 | 0.028 | 0.026 | 0.606 | 301 | 16 (3) | 1,047 | 204 |
| dblp_acm before (r2) | 2,013 | 667 | 211 | 0.751 | 0.905 | 0.821 | 0.960 | 65 | 22 (2) | 415 | 4 |
| dblp_acm after (r2) | 2,185 | 684 | 39 | 0.762 | 0.983 | 0.858 | 0.971 | 107 | 18 (2) | 1,716 | 199 |
| amazon_google before | see `runs/amazon_google_before_r1/result.json` (completed, 1,274 s) | | | | | | | | | 1,274 | 5 |
| musicbrainz before | 7,033 | 469 | 9,217 | 0.938 | 0.433 | 0.592 | 0.838 | 136 | 10 (5) | 307 | 8 |
| musicbrainz after | 10,744 | 481 | 5,506 | 0.957 | 0.661 | 0.782 | 0.904 | 124 | 14 (5) | 1,180 | 204 |
| dblp_scholar before | 4,880 | 5,203 | 8,883 | 0.484 | 0.355 | 0.409 | 0.960 | 1,919 | 29 (37) | 620 | 4 |
| dblp_scholar after | **no result: stopped after ≈ 70 min inside resolve_entities (user decision; D1)** | | | | | | | | | — | 4 |
| amazon_google after | **no result: driver timeout after 140 min inside resolve_entities** | | | | | | | | | 8,403 | 4 |

Stratified (within / cross-source) and review counts: febrl3x2 after within TP 3,317 FP 9 FN 5, cross TP 3,209 FP 14 FN 7; dblp_acm: every within-source pair is an FP on both (504 → 450; recurring column titles such as "Book review column", "Editor's Notes" merged across years); Abt-Buy within FP 2,716 → 1,207, cross TP 16 → 21 of 1,097. Review pairs listed at the matches gate (after): febrl4 7, febrl3 15, febrl3x2 13, geo 50, abt_buy 50, dblp_acm 50, plant 5 — all ≤ 10 % of true pairs, so rule 6 never downgraded a verdict; but see D15 (50 is a cap).

Blocking (pair completeness from the truth; `blocking_probe`):

| scenario | main C / PC / PQ | branch C / PC / PQ |
|---|---|---|
| febrl4 | 250,000 (cap) / 0.980 / 0.020 — `uncompared_pairs` 993,586, true discard 466,880 | 84,134 / **1.000** / 0.059 |
| febrl3 | 173,051 / 0.981 / 0.037 | 34,152 / 0.999 / 0.191 |
| dblp_acm | 136,183 / 0.987 / 0.016 | 87,099 / 1.000 / 0.026 |
| abt_buy | 126,200 / **0.928** / 0.008 | 30,680 / **0.897** / 0.033 |
| affiliations | 141,310 / 0.459 / 0.055 | 30,593 / 0.757 / 0.416 |
| geo | 11,241 / 0.973 / 0.380 | 17,701 / 0.973 / 0.241 |
| musicbrainz | 250,000 (cap) / **0.536** / 0.035 — `uncompared_pairs` 596,814, true discard 593,421 | 331,316 / **0.990** / 0.049 |
| dblp_scholar | 250,000 (cap) / **0.457** / 0.025 — `uncompared_pairs` 26,107,859 | not measured (run stopped) |

Pre-LLM scores (`score_probe`, recall over ALL true pairs; FP@0.95 before clustering):

| scenario | main recall@0.70 / @0.90 / FP@0.95 | branch recall@0.70 / @0.90 / FP@0.95 |
|---|---|---|
| febrl4 (persons) | 0.979 / 0.979 / 1 | 0.999 / 0.998 / 44 |
| febrl3 (persons) | 0.981 / 0.980 / 0 | 0.995 / 0.993 / 41 |
| dblp_acm | 0.964 / 0.934 / 636 | 0.992 / 0.985 / 814 |
| abt_buy cross-source | 0.376 / 0.043 / 8 | 0.115 / 0.106 / 4 |

## FAILURES AND DEFECTS

Priority: P0 data-corrupting · P1 wrong result · P2 quality. Root causes are **hypotheses** unless marked confirmed. Repro fixtures: `tests/part1/live_repros/` (each test `xfail(strict=True)`; a fix flips it to XPASS, which fails until the marker is removed). Run: `TMPDIR=$PWD/.tmp .venv311/bin/python -m pytest tests/part1/live_repros -q -p no:cacheprovider --runxfail`.

### D1 — P14: soft text similarity is quadratic in token count; Amazon-Google never finishes (P1)
- Repro: `python -m scripts.verify_local.run_bench amazon_google after` (driver timeout at 140 min, still in `resolve_entities`; main's whole run 1,274 s). Offline: branch `_resolve_core` on 60 of its records 62 s, 120 records 202 s. Fixture `test_p14_text_similarity_cost_is_not_quadratic_in_tokens` (2 Amazon records, 120 + 179 tokens → 42,960 `token_sim` calls = 2·|A|·|B|). Public dataset: **Leipzig Amazon-GoogleProducts**.
- Also at scale on short text: DBLP-Scholar (titles/authors, 66,879 records) — branch `_resolve_core` on 2,000 / 4,000 sampled records 51 s / 137 s (16k / 47k candidates, ≈ 2.9 ms per pair, 75 % in `_token_ratio`→difflib), main's whole run 620 s; see the dblp_scholar row.
- Observed vs expected: 5.4 M `_token_ratio` calls for 926 comparison vectors (cProfile, 40 records); Amazon descriptions median 38, max 2,877 tokens. Expected: resolution time comparable to main.
- Root cause (confirmed by profile): `fellegi_sunter.idf_soft_similarity` (`src/mcp_server/tools/fellegi_sunter.py:154`, inner `directed` at `:185`) takes `max(token_sim(t, u) for u in dst)` for every `t` — |A|·|B| per direction — called from `_comparison_level` (`src/mcp_server/tools/resolve_entities.py:1888`) for every candidate pair and the 3,000 sampled u-pairs (P13) via `_fit_match_model` (`:1520`); the `_seq_ratio` LRU (500k) thrashes on distinct description tokens.
- Fix shape: keep the soft Monge-Elkan for short name-like values only; for long text use IDF-weighted exact-token overlap (O(|A|+|B|)), or restrict `token_sim` candidates via a character-n-gram index. Add a wall-clock or comparison-cost guard beside P2's pair guard so a run fails loudly instead of hanging.
- Verify: repro test XPASS; Amazon-Google after completes and passes rules 1–5 against main; Abt-Buy and DBLP-ACM scores not lower.

### D2 — P15/P9: band answers are dropped by the API; P15 decisions and P9 memory are unreachable (P1)
- Repro: `run_bench febrl4 after --tag p9r1 --oracle --memory <file>`: 3 band pairs answered `merge` through `POST /api/workflow/{id}/feedback`; SQL on `bm_dst_febrl4_after_p9r1.merge_engine_entity_map`: all 3 pairs in different clusters; the memory file was never created. Fixture `test_api_feedback_forwards_band_decisions` (no data).
- Root cause (confirmed): `Feedback` (`src/api/main.py:260`) has no `pair_decisions` / `band_thresholds` field (pydantic drops them silently) and the route (`src/api/main.py:778-800`) forwards only the fields it lists; `src/ui/frontend/src` never sends them either (grep: 0 hits). Only tests that call `WorkflowManager.resume_workflow` directly reach `apply_band_decisions` (`src/orchestrator/workflow_manager.py:1430`).
- Fix shape: add both fields to `Feedback` and forward them; add the per-pair merge/keep controls to the matches gate in `Gates.tsx`.
- Verify: repro XPASS; rerun the p9r1 command: the 3 oracle pairs share a cluster id (SQL), the memory file holds 3 entries; a second run with the same memory (`--tag p9r2`) shows 0 pending pairs at the matches gate and `decisions_from_memory = 3`.

### D3 — P10: a single-entity run can never be refused for a giant cluster; the gate displays measured > threshold with violated False (P1)
- Repro: every benchmark (one entity each) — e.g. plant_febrl4 after: `would_fail False` with 20 planted two-person clusters; abt_buy after: thresholds exactly 2 × measured (flagged 21 → 42, largest share 0.0069 → 0.0138). Fixture `test_p10_single_entity_giant_cluster_is_refused` (30 Febrl records, one cluster of 21 people: measured 0.69, threshold 0.50, violated False).
- Root cause (confirmed by reading + fixture): `pre_load_gate` (`src/orchestrator/nodes/load_data.py:447`): proposals are medians over entities including the judged one; `slack` for `largest_cluster_share` (`typical = 2·median(largest) − 1`, `:483-490`) is computed from the entity itself, so `max(used, slack)` is always ≥ its own value and overrides the "never a majority" cap.
- Fix shape: compute the median and `typical` leave-one-out (other entities only); never let slack lift a ceiling above the 1/2 cap; with no sibling entity fall back to the absolute floors/caps. This is a user-facing policy change (F4 waiver) — the user decides.
- Verify: repro XPASS; plant_febrl4 after refuses with destination row counts unchanged (SQL before/after); febrl4 after still loads.

### D4 — P16: person names read from ONE column; 18–39 % false flags on correct person clusters (P2)
- Repro: febrl4 after flags 888 of 4,989 clusters, 0 of which are wrong merges; febrl3 450 / 1,167 (1 of 8 real over-merges caught). Fixture `test_p16_true_person_pairs_are_not_flagged` (30 true Febrl4 pairs; flags the same 14 the live run flagged).
- Root cause (confirmed): `load_data.py:183` (and `_entity_measures` `:442`) takes the name from `_best_name_field` (`resolve_entities.py:1051`), one column per record chosen per record: `given_name` when filled, else `surname`. Swapped given/surname, a missing given name, or a substituted given name then look like two people.
- Fix shape: build the display name from all name-hint columns of the record (order-insensitive token set), or from the destination's mapped identity columns; keep the containment rule.
- Verify: repro XPASS; febrl4 after flagged ≈ 0; plant_febrl4 still flags 20/20.

### D5 — P14 (+P20): "Addendum to X" auto-matches X; DBLP-ACM over-merged clusters 65 → 107 (P1)
- Repro: `run_bench dblp_acm before|after --tag r2` → over-merged 65 → 107, cross precision 0.925 → 0.903. Fixture `test_p14_addendum_is_not_an_auto_match` (40 DBLP-ACM records, LLM off): main 0.917 (band), branch 0.968 (63 records: 0.988) for CeriFPT94 vs CeriFPT95 ("Addendum to …", 1995). Public dataset: **Leipzig DBLP-ACM**.
- Root cause (hypothesis): the symmetric Monge-Elkan in `idf_soft_similarity` averages the two directions, so X → "Addendum to X" scores 1.0 and the rare extra token only halves one side; the year disagreement does not outweigh it. Clustering (P20, no name cut) then joins the true pair and the addendum through two confident edges.
- Fix shape: take the weaker direction (min) or charge unmatched high-IDF tokens on either side; P20 triangle support should require the third edge to be confident too.
- Verify: repro XPASS; DBLP-ACM after over-merged ≤ main + 2, cross precision ≥ main − 0.01.

### D6 — P14: product cross-source recall@0.70 drops 0.376 → 0.115 on Abt-Buy (P2)
- Repro: `score_probe abt_buy before|after`. Full dataset needed (**Leipzig Abt-Buy**); no small fixture (scores depend on corpus statistics).
- Root cause (hypothesis): the same soft-similarity change lowers product names with codes ("Denon DP-29F" vs "DP29F") below 0.70; the 0.90/0.70 level cut-offs were carried over from difflib (HANDOFF P14 debt).
- Fix shape: recalibrate the level cut-offs against labelled data (needs P13/§2.17 calibration).
- Verify: score_probe abt_buy after recall@0.70 ≥ main − 0.01.

### D9 — P2: pairs removed by block filtering are never counted as pairs (P2, reporting)
- Repro: `blocking_probe abt_buy after`: G − C = 1,213,649 distinct pairs vs `pairs_pruned_by_meta_blocking` 161,535 + `pairs_pruned_by_purging` 0 (+ "25,489 block memberships filtered", a membership count).
- Root cause (hypothesis): `_generate_candidate_pairs` (`resolve_entities.py:1152`) counts block filtering in memberships, not pairs.
- Fix shape: count distinct pairs lost to block filtering; `G − C` must equal the reported pruned pairs.
- Verify: blocking_probe on Abt-Buy and febrl4: G − C = sum of reported pruned counts.

### D12 — P13: precision at the auto-match bar lower before clustering (P2)
- Repro: `score_probe <scenario> before|after`: FP@0.95 febrl4 1 → 44, febrl3 0 → 41, dblp_acm 636 → 814 (dblp precision at the bar 0.760 → 0.728).
- Root cause (hypothesis): u from random pairs (P13) makes agreements look rarer, so more pairs cross 0.95; on persons clustering conflict repair removes them, on DBLP it does not.
- Fix shape: calibrate the score (Platt/isotonic, debt row 16) or keep the bar relative to the fitted model.
- Verify: score_probe FP@0.95 not above main on the three sets.

### D13 — P8: one LLM call per pair multiplies wall time; parallel runs fail preflight (P2)
- Observed: after/before wall febrl3 1,085 / 316 s, dblp_acm 1,716 / 415 s, geo 673 / 285 s, abt_buy 1,047 / 443 s; ≈ 200 calls per entity vs 4–6. With the LAN endpoint serving ~1 request at a time, a second app's preflight ("LLM reachable", 45 s) failed three times during this verification. LLM-decided merges on products: 2 of 34 correct (Abt-Buy), vs 37/37 on persons.
- Fix shape: user decision (report P8 accepted the cost); a per-run LLM budget in seconds, and not sending pairs from entities where the LLM's measured precision is low.

### D15 — P15: the matches gate lists at most 50 band pairs; the rest stay separate unseen (P2)
- Observed: Abt-Buy after: `band_summary.predicted_counts.possible` 1,330, `pending_merge_candidates` 50 (`_MAX_PENDING_CANDIDATES = 50`, `resolve_entities.py:203`, slice at `:871`; the cap pre-dates Part 1, main `:198`).
- Fix shape: page the band at the gate, or say "50 of N shown" in the payload.

### Pre-existing (also on main; not caused by Part 1)
- **PRE-D1 — a name-only entity never merges (P1).** Affiliations: 0 of 16,795 true pairs on both; max score over 30,593 scored pairs 0.109; identical "INRIA Rocquencourt" 0.058 (branch) / 0.085 (main). Fixture `test_identical_names_in_a_name_only_entity_reach_the_band`. Hypothesis: the heuristic/model needs more than one agreeing field to leave the exclude zone. Dataset: **Leipzig Affiliations**.
- **PRE-D2 — product variants auto-merge (P1).** Abt-Buy "Omnimount … 20WLBK" vs "… 20WLWH" (records `abt:10101`, `abt:10102`) score 0.957 on the full corpus (0.03 on 40 records, so no small fixture); largest cluster 16 (main 19). Hypothesis: model codes differing in a suffix are not treated as conflicting identifiers. Dataset: **Leipzig Abt-Buy**.
- **PRE-D3 — "Event loop is closed" on the first JSON-mode LLM call (P2).** Both versions log `json_object response_format rejected by endpoint, retrying without it: Event loop is closed` (`src/llm/client.py:302-304`), which sets `_json_mode_unsupported` for that client.
- **Test infra (P2):** `tests/part1/test_p19_attack.py:145` shells out to `.venv/bin/python` (3.14); use `sys.executable`.

## UNVERIFIED (and why)
- P1, P3, P4, P7, P12, P17 (broken-table half), P18 (numeric alert): need the user's origins / destination (S5); public single-table benchmarks have none of the structures.
- P6: no public dataset with parent/subsidiary truth was found.
- P19: accepted-cloud; not re-run, but its counting is consistent with the probes.

## S4 — gate
Six scenarios fail the pass rule (plant_febrl4, abt_buy, dblp_acm, amazon_google, musicbrainz, dblp_scholar). Per the brief: **STOP**; S5 waits for the user's decision and for the S5 preconditions (credentials, `ground_truth`, destination per §0.7).

## USER DECISIONS (from PHASE5_REPORT.md — not decided here; one deciding fact each)
1. **CI does not run `tests/part1`.** Deciding fact: the full suite runs in 69 s locally and its only failure is the `.venv` interpreter path (S1); the `P10_BASE_REV`-style git-revision tests are the remaining obstacle for a shallow CI clone.
2. **Audit cuts that need accepted tests rewritten.** Deciding fact: P20's verdict here is FAIL on DBLP-ACM over-merging (D5); the unreachable cohesion code is the obvious place a fix might want to live, so deleting it now could be premature.
3. **`sql_lineage.py` has no caller.** Deciding fact: no benchmark run touched it; graph + grep still show only its own unit test.
4. **`scipy` undeclared for P20's `one_to_one`.** Deciding fact: `one_to_one` was never reached in 20 runs (no caller); `.venv311` happens to have scipy, a clean install would not.
5. **`decision_memory_path` / `quality_history_path` not in `MergeState`.** Deciding fact: the env vars worked for quality history in every run; decision memory could not be exercised because the API drops the band answers (D2), so the state-key question is moot until D2 is fixed.
6. **ADR 0014 superseded by P2.** Deciding fact: live data confirms the supersession (main truncates at 250,000 pairs and over-reports `uncompared_pairs` ×2.1 on febrl4; the branch never truncates).

## Regenerate
```
python -m scripts.verify_local.load_bench all                       # origins + truth on 127.0.0.1:5439
python -m scripts.verify_local.scorer                               # scorer self-proof
python -m scripts.verify_local.run_bench <scenario> before|after [--tag r2] [--oracle --memory F --quality-history Q]
python -m scripts.verify_local.compare <scenario> [--tag r2]         # pass rule verdict
python -m scripts.verify_local.blocking_probe <scenario> before|after
python -m scripts.verify_local.score_probe <scenario> before|after
python -m scripts.verify_local.audit_probe <scenario> before|after
TMPDIR=$PWD/.tmp .venv311/bin/python -m pytest tests/part1/live_repros -q -p no:cacheprovider --runxfail
```
(`.venv311/bin/python`, `TMPDIR=$PWD/.tmp`; the main worktree: `git worktree add --detach .tmp/wt_main origin/main`; bench server: `docker run -d --name merge_bench_pg -p 127.0.0.1:5439:5432 -e POSTGRES_HOST_AUTH_METHOD=trust postgres:17`.)
