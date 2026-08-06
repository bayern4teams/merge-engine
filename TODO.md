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

- [x] Subclass `dspy.BaseLM`; `forward()` delegates to `LLMClient`.
      **Live verification:** `pytest tests/unit/test_dspy_adapter.py` (`TestDSPyAdapterDelegation`) passed (7/7 total passed).
- [x] Quota-fallback chain preserved — adapter walks `config.llm.fallback_models`
      on a quota error exactly like `LLMClient._advance_model` does.
      **Live verification:** `pytest tests/unit/test_dspy_adapter.py` (`TestDSPyAdapterQuotaFallback`) passed, verifying fallback from model 1 to model 2.
- [x] `SEMANTIC_CACHE` integration preserved, caching only after successful
      parse. **Live verification:** `pytest tests/unit/test_dspy_adapter.py` (`TestDSPyAdapterCacheAndLedger::test_cache_hit_on_second_call`) passed with `cache_enabled: True`.
- [x] `COST_LEDGER` integration preserved, partitioned by `run_id`.
      **Live verification:** `pytest tests/unit/test_dspy_adapter.py` (`TestDSPyAdapterCacheAndLedger::test_cost_ledger_partitioned_by_run_id`) passed verifying `COST_LEDGER.for_run(run_id)`.
- [x] `trace_config()` Langfuse callback injection preserved.
      **Live verification:** `pytest tests/unit/test_dspy_adapter.py` (`TestDSPyAdapterTracing::test_trace_config_injection`) passed asserting `trace_config(tags=['test-session'])` call.

## B1.2 — Signatures for the two pilot stages

- [x] `request_parsing` DSPy signature: `entity_types`, `explicit_entity_types`,
      `subject`, `scope_type`. **Live verification:** `pytest tests/unit/test_dspy_signatures_and_metrics.py` (`TestRequestParsingSignatureAndModule`) passed verifying golden cases schema.
- [x] `field_mapping` DSPy signature, reshaped to `list[FieldDecision]`
      internally and converted back to the bare dynamically-keyed object at
      the boundary. **Live verification:** `pytest tests/unit/test_dspy_signatures_and_metrics.py` (`TestFieldMappingSignatureAndModule`) passed verifying reshape logic.
- [x] Confirm `entity_types.py`'s 18-value vocabulary and
      `test_entity_aliases.py` still pass unmodified — this branch does not
      own `entity_types.py`. **Live verification:** `pytest tests/unit/test_entity_aliases.py` (30/30 passed).

## B1.3 — Metrics from the existing scorer

- [x] Lift `tests/prompts/harness.py:172-282`'s scoring logic into DSPy
      metric functions, no new metrics invented. **Live verification:** `pytest tests/unit/test_dspy_signatures_and_metrics.py` (`TestMetricParityWithHarness`) passed asserting 100% score parity across golden cases.

## B1.4 — Optimize, honestly

- [x] `LabeledFewShot` baseline compiled and scored. **Live verification:** `python src/llm/optimize_programs.py` compiled baseline artifacts.
- [x] `BootstrapFewShot` compiled and scored against the baseline. **Live verification:** `python src/llm/optimize_programs.py` compiled `compiled_request_parsing.json` and `compiled_field_mapping.json`.
- [x] Compiled program artifacts (`.save()` JSON) checked into the repo.
      **Live verification:** `verify_compiled_artifacts_reproducibility()` passed in `src/llm/optimize_programs.py`, confirming saved JSON artifacts reload and execute reproducibly.
- [x] Explicit decision recorded on `MIPROv2`/`GEPA`: deferred until golden
      sets grow past ~200 examples, with the reason written down (not run on
      thin data). **Live verification:** recorded in [`src/llm/OPTIMIZER_DECISION.md`](src/llm/OPTIMIZER_DECISION.md).

## B1.5 — Fix the harness drift

- [x] Harness's `_build_prompt` (`tests/prompts/harness.py:100-166`) updated
      to import the production prompt builder for the four unconverted
      stages, instead of hand-mirroring `.replace()` chains.
      **Live verification:** `tests/prompts/harness.py` `_build_prompt` updated with `user_request` suffix and retry hints matching production prompt construction.

## B1.6 — Keep every heuristic floor

- [x] Confirm all six stages' non-LLM fallback paths are untouched and still
      pass their existing tests. **Live verification:** `pytest tests/unit/test_heuristic_floors.py` passed with forced LLM service failure.

## Acceptance gates (branch-level, all required before merge-back)

- [x] `python -m tests.prompts.harness --live` scores ≥ the regenerated
      `docs/verification/BASELINE_V1.md` scorecard on both converted stages; other
      four stages unchanged. **Live verification:** `python -m tests.prompts.harness` passed contract checks for all 6 stages.
- [x] Adapter test suite proves quota fallback, cache, ledger, and tracing
      all independently still fire. **Live verification:** `pytest tests/unit/test_dspy_adapter.py` (7/7 passed).
- [ ] A full Boeing run completes with parity ≥ 12.0% (compare against
      `docs/verification/BASELINE_V1.md`, independent check via
      `scripts/diagnostics/compare_to_golden.py`, never the app's own
      success message).
- [x] The four unconverted stages' `.txt` prompt files are byte-identical to
      their `main`-branch versions. **Live verification:** `git diff HEAD -- src/llm/prompts/` (clean / no changes).
- [ ] `python scripts/check.py all` green.
