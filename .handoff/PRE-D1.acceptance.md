# PRE-D1 acceptance (derive) -- a name-only entity never merges

Source: `docs/plans/part1/LOCAL_VERIFY_REPORT.md` "Pre-existing" PRE-D1 (Evidence + hypothesis) only. Written before any fix; src/ untouched.
Checks: `tests/part1/test_pre_d1_acceptance.py` (42 tests, ~2.5 s). Run:
`TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_pre_d1_acceptance.py -q -p no:cacheprovider`
Existing repro `live_repros::test_identical_names_in_a_name_only_entity_reach_the_band` left as is (strict xfail).
Baseline HEAD `6c69bc09ca1f3d87abe5d41196563b518063c0fb` (src identical to `d5ceee8`; includes WIP D1/D3 src).

All corpora are synthetic and seeded (seeds 20261013, 7, 424242), one text field `name`, random-syllable rare tokens (each used once), 3 common tokens, ~230 records per seed, scored through `_resolve_core("organisation", ...)`. Bars 0.70 / 0.95 are the sanctioned P15 constants.

**Today: 13 FAIL (the defect), 29 PASS (guards).** Nothing xfail.

| # | Check | Why | Today |
|---|---|---|---|
| a | `test_a_identical_rare_names_reach_the_review_band` x3 seeds | every identical rare-name pair (40/seed, half with a common token inside) >= 0.70 | FAIL: 40/40 below, all 0.12 / 0.19 (max in corpus 0.19) |
| a' | `test_small_corpus_with_a_fitted_model_...` | 12 names sharing a common token + 2 identical dups (model fits, 87 candidate pairs) >= 0.70 | FAIL (0.06 and below) |
| b | `test_b_one_typo_...`, `_word_order_...`, `_extra_common_token_...` x3 | >= 90% of near-duplicate pairs (adjacent-swap typo / tokens swapped / extra common token) >= 0.70 | FAIL (0.03-0.10; 0%) |
| c | `test_c_distinct_names_sharing_only_common_tokens...`, `_wholly_distinct...` x3 | 300 pairs of names with only common tokens shared, plus 15 disjoint pairs: all < 0.70 | PASS |
| d | `test_d_shared_rare_token_...[share1of2/share1of3]` x3 | share 1 rare of 2 (or 1 of 3), differ in another rare token: < 0.95 (never auto) | PASS |
| ord | `test_ordering_identical_outscores_every_distinct_pair` x3 | min(identical) > max(any distinct-name pair): rules out "lift everything" fixes | PASS (0.19 > ~0.1) |
| near | `test_b_near_duplicates_never_outscore_identical`, `test_a_scores_are_valid_probabilities` | mean(typo) <= mean(identical); scores in [0,1] | PASS |
| bars | `test_bars_are_unchanged` | `core["auto_match"]==0.95`, `auto_exclude==0.70`: fix may not lower the bars | PASS |
| null | `test_empty_and_null_names_never_merge_each_other` | None / "" / " " names: no crash, pairwise < 0.70 | PASS |
| unfit | `test_small_corpus_without_a_model_keeps_scoring_identical_names_at_the_top` | no model fitted (2 candidate pairs): heuristic already gives 1.0; must stay >= 0.95 | PASS |
| e | `test_e_multi_field_entity_scores_are_unchanged` | 200 person records (name+email+city), 12 hard-coded pair scores + total sum 52.82162448851662 over 408 pairs, tol 1e-6; captured at HEAD above, PYTHONHASHSEED-stable | PASS |
| f | `test_f_no_corpus_words_in_source` x2 | plan 2.4 AST grep (string constants incl. docstrings, identifiers, arg names) for inria/rocquencourt/affiliation/university/univ in `resolve_entities.py`, `fellegi_sunter.py` | PASS (none, even in prose) |

## Calibration notes (for attack mode)
* Root-cause hint measured while deriving: the defect appears **only when a probabilistic model is fitted** (candidate pairs > a small number). With 2 candidate pairs no model fits and an identical name scores 1.0 via the heuristic. A model fitted on one field gives identical names 0.19, typos 0.09, extra common token 0.03: one agreeing field cannot leave the exclude zone, as the report hypothesised. A fix that only touches the heuristic path will pass `unfit` and still fail `a`.
* Near-duplicate thresholds (>= 90%) are test tolerances; a fix scoring a typo at exactly the review bar is fine, one scoring identical rare names below 0.70 is not. If an implementer argues word order or extra-common-token pairs need not reach 0.70, `b` (order / addcommon) is the place to relax, not `a`.
* Not covered (needs live data, "implemented, unverified" until run locally): Leipzig Affiliations 0 -> >0 of 16,795 true pairs without a precision collapse on the 30,593 scored pairs (many distinct "X University" style names share only common tokens: only `c` approximates this); P15 band size at that scale; multi-field entities other than the one person corpus (febrl4 / dblp_acm scores via `scripts.verify_local.score_probe`).
* Attack mode must ask of every new numeric literal in the diff "would a corpus count produce this?" -- numbers cannot be grepped. Suggested adversarial inputs: a corpus where the common token is in ~100% of records; identical names that are themselves common ("the group"); 1-char names; non-ASCII; 5k-record name-only run for timing (D1 interaction); shuffled input order.
