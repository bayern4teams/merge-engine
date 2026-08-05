# TODO — `feat/dspy-prompt-layer` (B1)

Live, continuously-updated task list for this branch, in the same style as
the root `TODO.md` on `main`: checkbox items, checked only after running the
thing against the real app/DBs and confirming with an *independent* check —
never the app's own success message (see `Branch_Plan.md`'s R9). Full task
detail lives in [`Branch_Plan.md`](Branch_Plan.md); this file tracks
progress against it.

Branched from `main` commit `e269a5f` on 2026-08-05. Nothing in this branch
has started yet.

## B1.1 — The `dspy.LM` adapter

- [ ] Subclass `dspy.BaseLM`; `forward()` delegates to `LLMClient`.
      **Live verification:** unit test proves the adapter actually calls
      through to a real (or realistically mocked) `LLMClient.call_json`, not
      a bypass.
- [ ] Quota-fallback chain preserved — adapter walks `config.llm.fallback_models`
      on a quota error exactly like `LLMClient._advance_model` does.
      **Live verification:** test forces a quota error on model N and
      asserts the adapter retried on model N+1, matching
      `TestLLMClientModelFallback`'s existing assertions.
- [ ] `SEMANTIC_CACHE` integration preserved, caching only after successful
      parse. **Live verification:** test with `cache_enabled: True` proves a
      second identical call hits the cache; a malformed response is never
      cached.
- [ ] `COST_LEDGER` integration preserved, partitioned by `run_id`.
      **Live verification:** test asserts ledger entries appear under the
      correct `run_id` after an adapter call.
- [ ] `trace_config()` Langfuse callback injection preserved.
      **Live verification:** test asserts the ambient callback/metadata
      merge behaviour (not replacement) still holds through the adapter —
      see Part 1.5 C.0-C.2 on `main` for what "correct" looks like here.

## B1.2 — Signatures for the two pilot stages

- [ ] `request_parsing` DSPy signature: `entity_types`, `explicit_entity_types`,
      `subject`, `scope_type`. **Live verification:** run against all 14
      golden cases in `tests/prompts/golden/`, compare output keys/values.
- [ ] `field_mapping` DSPy signature, reshaped to `list[FieldDecision]`
      internally and converted back to the bare dynamically-keyed object at
      the boundary. **Live verification:** run against all 8 golden cases;
      confirm `map_fields.py:492-495`'s filtering contract still receives
      the expected shape (byte-identical keys).
- [ ] Confirm `entity_types.py`'s 18-value vocabulary and
      `test_entity_aliases.py` still pass unmodified — this branch does not
      own `entity_types.py`. **Live verification:** `pytest
      tests/unit/test_entity_aliases.py`.

## B1.3 — Metrics from the existing scorer

- [ ] Lift `tests/prompts/harness.py:172-282`'s scoring logic into DSPy
      metric functions, no new metrics invented. **Live verification:** run
      the DSPy metric and the harness's own scorer on the same golden case
      and confirm identical scores.

## B1.4 — Optimize, honestly

- [ ] `LabeledFewShot` baseline compiled and scored.
- [ ] `BootstrapFewShot` compiled and scored against the baseline.
- [ ] Compiled program artifacts (`.save()` JSON) checked into the repo.
      **Live verification:** delete the artifact, reload from the checked-in
      file, confirm identical behavior (reproducibility, not just existence).
- [ ] Explicit decision recorded on `MIPROv2`/`GEPA`: deferred until golden
      sets grow past ~200 examples, with the reason written down (not run on
      thin data).

## B1.5 — Fix the harness drift

- [ ] Harness's `_build_prompt` (`tests/prompts/harness.py:100-166`) updated
      to import the production prompt builder for the four unconverted
      stages, instead of hand-mirroring `.replace()` chains.
      **Live verification:** confirm the harness's rendered prompt for an
      unconverted stage is byte-identical to what `select_entity_tables.py`
      actually sends in a real run (including the previously-missing
      user-request suffix).

## B1.6 — Keep every heuristic floor

- [ ] Confirm all six stages' non-LLM fallback paths are untouched and still
      pass their existing tests. **Live verification:** simulate an LLM
      outage (force every model in the fallback chain to fail) and confirm
      the pipeline still completes via heuristics, same as before this
      branch existed.

## Acceptance gates (branch-level, all required before merge-back)

- [ ] `python -m tests.prompts.harness --live` scores ≥ the regenerated
      `docs/verification/BASELINE_V1.md` scorecard on both converted stages; other
      four stages unchanged.
- [ ] Adapter test suite proves quota fallback, cache, ledger, and tracing
      all independently still fire.
- [ ] A full Boeing run completes with parity ≥ 12.0% (compare against
      `docs/verification/BASELINE_V1.md`, independent check via
      `scripts/diagnostics/compare_to_golden.py`, never the app's own
      success message).
- [ ] The four unconverted stages' `.txt` prompt files are byte-identical to
      their `main`-branch versions (`git diff main -- src/llm/prompts/`).
- [ ] `python scripts/check.py all` green.
