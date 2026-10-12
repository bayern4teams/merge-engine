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
