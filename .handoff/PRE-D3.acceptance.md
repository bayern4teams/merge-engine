# PRE-D3 acceptance (derive) -- "Event loop is closed" must not disable JSON mode

Source: `docs/plans/part1/LOCAL_VERIFY_REPORT.md` Pre-existing PRE-D3 (Evidence line only) plus the
call-site description in the brief. Checks: `tests/part1/test_pre_d3_acceptance.py` (29 tests).
Written before any fix; src/ untouched. Chat model is stubbed (no network) except check (e).
Run: `TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_pre_d3_acceptance.py -q -p no:cacheprovider`
(~3 s). `ruff check` and `ruff format --check` are clean on the file.

Today's result on the unfixed branch (HEAD 9ca3b9a6): **18 FAIL (the defect), 11 PASS (guards)**.

Defect: `LLMClient.call` treats any exception from a `json_object` call as "endpoint rejects
response_format": sets `_json_mode_unsupported` for the client's lifetime and retries without it.

| # | Check | Why | Today |
|---|---|---|---|
| a | `test_transport_error_does_not_set_json_mode_unsupported[x8]` | RuntimeError("Event loop is closed"), httpx ConnectError/ReadTimeout, openai APIConnectionError/APITimeoutError, TimeoutError, 500, 401 on the first JSON call: flag stays False; a later call still sends `response_format=json_object`. | 8 FAIL |
| b | `test_genuine_rejection_sets_flag_and_succeeds_without_json_mode[x3]`, `..._via_call_json_...` | `BadRequestError` 400, `UnprocessableEntityError` 422, plain `APIStatusError` 400: flag set, retry without JSON mode returns the answer, next call stays without JSON mode. | 4 PASS (guard) |
| c | `test_transient_error_then_success_returns_answer_and_keeps_json_mode[x6]` | transient error then success: `call_json` returns the parsed answer (no lost call), flag False, next call requests JSON mode. | 6 FAIL |
| d1 | `test_persistent_error_raises[x2]`, `..._through_call_json` | persistent non-rejection error still raises to the caller; bounded retries (stub aborts at 12 calls). | 3 PASS (guard) |
| d2 | `test_persistent_error_does_not_set_json_mode_unsupported[x2]` | the flag is not set even when the call finally fails. | 2 FAIL |
| d3 | `test_quota_error_advances_model_chain[json_mode=False/True]`, `..._with_exhausted_chain_raises` | quota error (429/insufficient_quota) on m1 advances `_advance_model` to m2 and returns m2's answer; exhausted chain re-raises with `model_name` at the chain end. Stub fails on a model name, so the retry-without-JSON cannot mask it. | 3 PASS (guard) |
| d4 | `test_quota_error_does_not_set_json_mode_unsupported` | a quota error is not a response_format rejection; fallback model is asked for JSON mode. | FAIL |
| e | `test_real_second_event_loop_first_call_keeps_json_mode` | real `ChatOpenAI` -> `http.server` on 127.0.0.1 (HTTP/1.1 keep-alive, JSON chat completion), ONE `LLMClient`, `asyncio.run(call_json)` twice. Neither call may flip the flag; both requests must carry `response_format=json_object`. | FAIL: **reproduced** -- loop 1 flag False, loop 2 logs `json_object response_format rejected by endpoint, retrying without it: Event loop is closed`, flag True; the call still returns (via the no-JSON retry) so the failure is silent. |
| f | `test_no_new_numeric_literal_in_client_diff` | plan 2.4: AST multiset of numeric constants in `src/llm/client.py` vs `git show 9ca3b9a6:src/llm/client.py`; anything new other than 0, 1, 400, 422 fails. (400/422 are the HTTP "client rejected request" statuses, not fitted values.) | PASS (no diff yet) |

## Finding for (e)
The report's cause is confirmed with a real stack: the first `asyncio.run` builds the model and a
pooled keep-alive connection; the second loop reuses it and the first request fails with "Event loop is
closed", which today is mislabelled a JSON-mode rejection. A fix that only reclassifies the error but
reuses the stale client would still lose the JSON-mode request on loop 2 (test e then fails on `fmts`,
because the retry would go out without `response_format`, or fail again). The fix needs to retry
with a fresh client/connection AND keep JSON mode.

## Notes for attack mode
* Not pinned: how many retries, backoff, whether the retry keeps JSON mode (c/e only require the answer and
  a JSON-mode request on a later call; e additionally requires both HTTP bodies to carry JSON mode).
* Not covered: a 400/422 that is NOT about response_format (e.g. context-length) -- a message-sniffing fix
  would be unverifiable here; attack should probe it. Also `call_batch` (never uses JSON mode) and the
  `with_structured_output` path are untouched by this item.
* Attack must check: fix does not close/replace the lru-cached default httpx client in a way that breaks
  other live clients; no module-level mutable state added (CLAUDE.md); ruff/mypy ratchet on client.py.
* "Implemented, unverified": the live endpoint (10.10.9.45) behaviour; the stub server only models a
  200 response, not an endpoint that really rejects `response_format`.
