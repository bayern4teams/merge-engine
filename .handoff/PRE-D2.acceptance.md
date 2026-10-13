# PRE-D2 acceptance (derive) -- product variants differing only in a model-code suffix never auto-merge

Source: `docs/plans/part1/LOCAL_VERIFY_REPORT.md` "Pre-existing" PRE-D2 (Evidence + hypothesis) only. Written before any fix; src/ untouched.
Checks: `tests/part1/test_pre_d2_acceptance.py` (42 tests, 3 skipped without the dataset, ~17 s default). Run:
`TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_pre_d2_acceptance.py -q -p no:cacheprovider -s`
Real data (adds ~20 s): `ABT_BUY_DIR=<dir with Abt.csv Buy.csv abt_buy_perfectMapping.csv> ...` (latin-1, as downloaded).
Baseline HEAD `22685c3ff87a894035eef040ace19ef9e55067fa`. Deterministic under PYTHONHASHSEED 0 / 99 / default.

**Today: 12 FAIL (the defect), 27 PASS (guards), 3 skip; with the dataset 14 FAIL, 28 PASS.** Nothing xfail.

## Synthetic world (seeds 20261013, 7, 424242; 516-529 records, origins `abt`/`buy`, model fits)
Random consonant-vowel syllables only (brands, title words, finish words, 2-letter suffixes, letter+digit bases). Per seed:
32 families (2-3 siblings, same title/features, code = base + colour suffix, finish word differs) in `abt`, 60% also copied to `buy`;
120 single products (70% with a `buy` copy); 90 unrelated filler per side. Descriptions share the feature text and the
name; buy copies are shorter, price missing 30%. Sibling prices equal in a third of the families.
Truth lists: variants `within` (60/68/70 pairs, abt-abt, the report's shape) and `cross` (69/77/92, abt X vs buy copy of Y);
same-code duplicates `exact` (28), `split` (28, buy writes `20-WL BK`-style: hyphen or space before the suffix), `oneside`
(28, buy name has no code at all), `family` (45-55, a sibling's own buy copy).

| # | Check | Why | Today |
|---|---|---|---|
| 1a | `test_variants_never_auto_match[seed-within/cross]` x3 | no variant pair >= `core["auto_match"]` | FAIL: within 25/60, 24/68, 30/70 auto; cross 24/69, 23/77, 25/92 |
| 1b | `test_variants_are_not_parked_just_under_the_bar[seed-within/cross]` x3 | worst variant < bar - 0.05 (anti-clamp; 0.05 is a test margin, as D5) | FAIL (1.0 / 0.995) |
| 2a | `test_true_duplicates_keep_reaching_the_band[seed-exact/split/family]` x3 | auto count and auto-or-band count >= today's - ceil(7% of category) (2 of 28, 4 of 55) | PASS. Today (auto/band-or-auto): exact 18/18, 24/24, 24/24; split 17/19, 19/22, 23/25; family 16/26, 16/26, 14/29 |
| 2b | `test_one_sided_code_is_not_penalised[seed]` x3 | code on one record only: same tolerance on counts, plus mean score >= today - 0.03 | PASS. Today oneside 20/22 (mean .819), 20/22 (.813), 20/21 (.782) |
| 3 | `test_guard_scores_unchanged_where_no_model_codes[persons / persons_handles / publications]` | name+email+city (200 people, 80 dup, typo), same with numeric email handles (`ab.cd87@`), 100 papers x 2 sources: pair count, score sum, 12 order statistics, 12 named true-dup scores frozen at HEAD, tol 1e-6 | PASS |
| 4 | `test_no_corpus_words_in_source[resolve_entities.py, fellegi_sunter.py]` | plan 2.4 AST grep: omnimount sanus sony abt buy amazon google wlbk wlwh as whole word in any string constant (docstrings excluded), name, attribute, def, arg, keyword (self-tested on a planted file) | PASS (none) |
| 5 | `test_world_shape`, `test_bars_are_unchanged`, `test_no_crash_on_empty_and_null_fields` | model fitted, sizes, bars 0.95/0.70 unchanged, None/""/" "/"-"/"N/A" names do not crash, scores in [0,1] | PASS |
| 6 | `test_informational_digit_variants` | prints how many siblings whose BASE code differs (digit change) auto-match (5/9, 5/11, 4/7); asserts nothing, beyond the report's evidence | PASS (info) |
| R1 | `test_real_variant_pair_is_below_the_auto_bar` (needs `ABT_BUY_DIR`) | abt:10101 vs abt:10102 < 0.95 | FAIL (0.9588) |
| R2 | `test_real_true_matches_do_not_drop` | perfectMapping pairs auto-matched >= 4 (today); true pairs >= band >= 36 - 2 | PASS (4, 36) |
| R3 | `test_real_false_auto_merges_drop` | auto pairs not in perfectMapping <= 936 // 2 = 468. 769 of the 936 have differing letter+digit codes in the two names (crude count), so a code-conflict fix has room; fixing only the report pair fails | FAIL (936) |

Real corpus today (2,173 records, 18 s): 940 auto pairs, 4 true, 936 false (935 are within-origin: Abt-Abt or Buy-Buy siblings;
5 cross-origin, 4 true); 658 band pairs, 47 cross-origin, 32 true; 984 of 1,097 true pairs even reach scoring. Spot sample of
12 false autos: all variants (colour code, size, model digit: `MB13-GRN-SEE-V2` vs `MB13-ORG-SEE-V2`, `KDL46W4100` vs `KDL52V4100`,
`Q 300` vs `Q 320`).

## Calibration notes (for attack mode)
* A throwaway monkeypatch (in scratch, not src) that caps a pair at 0.3 when both names carry letter+digit tokens, no token is
  shared and neither token appears squashed in the other name, applied **after** `_three_way`, passes everything incl. real data
  (satisfiable). The same cap with raw token comparison (no hyphen/space normalisation) **fails** `2a[split]` x3 and R2: the split
  duplicates and `KDL-32L4000`-style true pairs are exactly what a naive "codes differ" rule destroys.
* Applied only inside `_score_pair` (before `_three_way`) the cap does **not** pass 1a: `_three_way` lifts a pair that shares an
  identifying value (a price no other entity holds) and has "same" names to `max(conf, auto_match)` unless a conflicting exclusive
  exists. Siblings that share a price therefore stay auto-matched. A fix has to make the code conflict visible there too.
* The fix must not be a word list (plan 2.4): the data contain no `BK`/`WH`; suffixes are random pairs. Numeric constants cannot be
  grepped; ask "would a corpus count produce this?" of each new number in the diff (a fixed penalty, a code length, a cap).
* Attack ideas not covered: codes only in the description; same code in different case; code that is a prefix of the other
  (`X20` vs `X200`); three-way families; digit-only model numbers (`Q 300`/`Q 320`); a suffix that is a real colour word; unit
  tokens (`16GB`, `1080p`) mistaken for codes (sizes differ -> they are variants anyway, but `4GB` vs `4 GB` of one product must not
  conflict); price-only anchors; larger corpus (blocking/purging change which pairs exist).
* Not verified here (needs live data): the largest cluster 16 (main 19) in the report is a clustering figure outside `_resolve_core`;
  Amazon-Google not run.
