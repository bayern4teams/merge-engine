# P21 nightly report — 2026-11-24 build, 2026-11-25 fix round

Branch `feat/part1-fixes`. The overnight agent built the P21 ER service
(P21a–k, rev. c contract v1.3); an independent reviewer then verified it
(`.handoff/P21.review-feedback.md`) and filed findings F1–F5, which the fix
round closed the same day. This is the honest per-item picture: what was
DONE, what is PARTIAL, what is BLOCKED — with the numbers MEASURED (reported,
never asserted: owner rule for quality items), the commit list, and CI's TRUE
state, red included, with run ids.

## Per-item status (the night's P21 scope)

| Item | State | Evidence / notes |
|---|---|---|
| P21a side tables (D-45) | DONE | `er_*` tables idempotent, live E1 test |
| P21b entity-types + model persistence | DONE | provisional bars honest; er_model round-trip test |
| P21c online search | DONE | live items 8/9 measured (below) |
| P21d GET + redirect | DONE | live `test_a18_history`, `test_g3_hooks…` |
| P21e resolve/create | DONE | live items 2/3/14 |
| P21f PATCH relaxed | DONE | live items 6/17 + mocked E4 |
| P21g-1 enrichment injection | DONE (F3 fixed the gap) | had ZERO tests; now `test_g1_batch_load_keeps_enriched_value` proves value survives the real batch load; ALSO found+fixed that `ensure_enrichment_origin` (D-46 priority -1) was never wired into the unify node — without it enrichment TIES every unset-priority source at 0 |
| P21g-2/3 load lock + side effects | DONE | live items 4/7/17 |
| P21h routes + auth | DONE | live HTTP round-trip + mocked E2/E4 |
| P21i search revived | DONE | live item 12 (zero-write proof) |
| P21j extension store | DONE | live items 15/16 (ledger-first, crash-sweep). F4 found `add_aliases` validated but was never APPLIED on the extension path — now merges into the doc |
| P21k field history | DONE | live items 17/18 |
| P21l split + choice | DONE | live items 10/13/19 (sequencing dependency documented in-test per review) |
| Acceptance item 1 (validate vs their `er_service_openapi.json`) | **BLOCKED** | enrichment's openapi file never arrived (C9) — their side, not ours. The guard we wrote instead: response shapes exercised over real HTTP + the mocked tier. Needs the file to close |
| Acceptance items 2–20, E1 | DONE | live tier green |
| E2/E3/E4 mocked tier | DONE (F4) | `tests/part1/test_p21_acceptance.py`, 7 tests, no env, wired into `check.py test` → CI |
| F1 duckdb drift | DONE | pin `<2.0` + debt comment; CI green from 7323ab6 on |
| F5 harness leak | DONE | orphan sweep at startup + harness-owned mongod; planted-orphan proof |

## Measured numbers (reported, not asserted)

Live tier, bench Postgres + harness mongod, 2026-11-25
(`MERGE_ENGINE_LIVE_DB=1 pytest tests/part1/test_p21_live.py`):

- `MEASURED p95=284.5ms recall@5=1.00` (this morning's run; the reviewer
  independently measured `p95=298.3ms recall@5=1.00` on b7b6164 — the bar
  is "report, never assert", so both figures stand as measurements, not
  claims of compliance)
- Live tier: **19 passed** (17 pre-F3 + the new G1 batch test; ~43 s)
- Mocked P21 tier: **7 passed**, no DBs, part of the CI test stage

## Commits (night: 13; fix round: 4)

Nightly build: `b8389db..c4de7af` + `b7b6164` (P21a–l, wire fixes).
Fix round (this report's F-items, in order):

- `7323ab6` F1 — duckdb `>=1.0.0,<2.0` + debt comment, regenerated requirements
- `3c03271` F5 — harness sweeps orphan schemas/containers at startup; one-per-session names; harness-owned mongod
- `84899ac` F3 — live G1 batch test + `ensure_enrichment_origin` wired into unify (the D-46 rule was dead code) + item-13 sequencing note
- `490076f` F4 — mocked acceptance tier E2/E3/E4 + `add_aliases` apply-fix + `check.py test` reaches the new file

## CI truth (no more omitting red)

- At `b7b6164` the night ended with CI **RED** (run `45459744307`, 4 consecutive failures) — undisclosed in the first report, which was itself review finding F1. Root cause was dependency drift (unpinned duckdb → 2.0.1), proven by the reviewer to have an identical 34-failure set at baseline `768fdda`: zero P21 delta, but the red was real.
- `45585623405` (at F1 commit `7323ab6`): **green** — first green CI since duckdb 2.0 existed. https://github.com/Reza-Sh83/merge-engine/actions/runs/45585623405
- `45595335347` (code tip `490076f`, includes F3/F4/F5): **green** (success). https://github.com/Reza-Sh83/merge-engine/actions/runs/45595335347
- This report's own commit does not touch code; CI re-runs on it as on every push.

## Debt / follow-ups (explicit)

1. **duckdb 2.x adaptation** — pyproject comment marks the `<2.0` pin as debt; lift deliberately when `extract_data` pagination is adapted.
2. **tests/part1 migration into the gate** — the tree carries ~32 failures that pre-date P21 (3.13 drift, proven); `check.py test` currently reaches only the P21 acceptance file from it.
3. **bench DB credentials in pre-P21 tracked files** — pre-dates this branch; needs rotation, unchanged by P21.
4. **Acceptance item 1** — BLOCKED on enrichment's `er_service_openapi.json`.
