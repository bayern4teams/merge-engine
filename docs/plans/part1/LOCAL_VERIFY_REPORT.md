# Part 1 — local live verification report

**Status: IN PROGRESS** (sections are filled in as each step is verified; resume from here + `git log`).

| | |
|---|---|
| date | 2026-10-12 |
| commit verified ("after") | `6d7c4b4` (`feat/part1-fixes`) |
| "before" | `507f9d4` (`origin/main` = merge-base), detached worktree `.tmp/wt_main` |
| environment | Linux, `.venv311` Python 3.11.15, `TMPDIR=.tmp/` |
| LLM | `Qwen/Qwen3.6-35B-A3B-FP8` (LAN llama.cpp endpoint), persistent cache OFF for every benchmark run (user decision) |
| benchmark DB server | dedicated container `merge_bench_pg`, PostgreSQL 17.11, 127.0.0.1:5439, loopback trust auth (user decision); no other DB written |

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
