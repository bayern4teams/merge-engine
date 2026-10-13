# PRE-D3 verdict (attack) -- REJECT (core fix verified; two small, concrete defects)

Fix under test: 41bd7cb `src/llm/client.py`. Checks: `tests/part1/test_pre_d3_acceptance.py` (29 pass) and
`tests/part1/test_pre_d3_attack.py` (11 pass + 3 `xfail(strict)` = the defects below; ruff check + format clean).
Run: `TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_pre_d3_attack.py -q -p no:cacheprovider`.

## What holds (evidence)
* Old failure reproduced by acceptance (e) on the parent and gone now: real ChatOpenAI -> stub server, one client,
  two `asyncio.run`: both requests carry `response_format=json_object`, flag stays False.
* Only `openai.APIStatusError` 400/422 sets `_json_mode_unsupported`; transport/5xx/401/quota errors do not (acceptance a,b,c,d).
  A 400 that is not about response_format (context length) still disables JSON mode, and the doomed no-JSON retry
  still happens -- identical to before (every error did), so NOT worse; pinned as a known limitation.
* Rebuild cost: one chat-model build per event loop, not per call (5 calls in one loop = 1 build; 20 `asyncio.run` = 20).
  `_model_chain_idx`, `model_name` (after a quota fallback) and the flag survive a rebuild; the rebuild uses the advanced model.
* fds: 300 sequential `asyncio.run` against a subprocess stub: peak 21 client sockets, back to baseline after `gc.collect()`.
  The clients are never closed, so it is GC-dependent: with `gc.disable()` it leaks exactly 1 socket per loop (300/300),
  and ~338 `ResourceWarning: unclosed transport` fire per 200 loops. Acceptable under default GC; a `ulimit -n` risk only if GC is off.
* Timeout: float (also str config) reaches `httpx.Timeout(5.0)`; None and `httpx.Timeout` objects pass through. `self.timeout` is
  always `float(...)` from config (None in config crashes in `__init__`, as before).
* anthropic: kwargs exactly {model, api_key, temperature, timeout, max_retries}, no httpx client. (It still rebuilds per loop -- harmless.)
* In-flight call keeps the model it started with when another thread rebuilds (local `base_model`).
* Caches unchanged: `tests/unit/test_llm_client.py` 32/32 pass. mypy on client.py: 2 errors before and after. No new numeric literal beyond 400/422.
* Suites `tests/part1 tests/unit tests/integration` vs parent 463319a: 3753 pass/29 fail (new) vs 3722/31 (old). No test that passed on the
  parent fails now because of PRE-D3. Failure-set diff: only-new = `test_d15_acceptance::test_g3_only_the_named_files_changed`
  (diffs all of `src/` against D15's base, so it trips on the PRE-D3 edit to client.py: cross-item interference, not a PRE-D3 defect --
  caller should re-baseline it); only-old = three `*_arch_lint_clean` (worktree artefact). The 29 common failures are pre_d1/pre_d2/d5/d1/p6/p13/p14
  acceptance tests of other, unfixed items. Worktree `.tmp/wt-pd3` removed.

## Defects (xfail strict in the attack file; each flips to XPASS when fixed)
1. **Regression from replacing langchain's default async client.** Bare `httpx.AsyncClient(timeout=...)` has `follow_redirects=False`
   and pool limits 100/20; the client langchain/openai used before is `openai.DefaultAsyncHttpxClient` (follow_redirects=True, 1000/100).
   Measured on both trees: a 307-redirecting endpoint returns `{'ok': True}` on parent, raises `APIStatusError 307` now
   (`test_redirecting_endpoint_still_works`, `test_connection_pool_limits_match_openai_defaults`). Fix (verified with a scratch monkeypatch, not
   in `src/`): `openai.DefaultAsyncHttpxClient(timeout=self.timeout)`; 3 loops x {v1, redir} all return and keep JSON mode.
2. **Thread race reintroduces the bug.** `_get_model` writes `self._model_loop` before `self._model` and has no lock. Interleaving
   B(model_loop=B, building) / A(full rebuild) / B(model=M_B) leaves state (M_B, loop A); A's next `_get_model()` returns B's model
   whose httpx client belongs to B's loop (`test_concurrent_cold_builds_leave_model_bound_to_the_right_loop`: "A was handed B's model").
   The app does run workflows on background threads with their own loops (`workflow_manager._run_workflow/_continue_workflow`).
   Fix: build into locals and assign `(model, loop)` as one tuple, under a `threading.Lock`.
3. (note, not blocking) Two long-lived loops sharing one client rebuild on every alternate call (10 calls = 10 builds, each leaking an unclosed client).

## Implemented, unverified (needs live data)
* LAN endpoint 10.10.9.45:8000: that a multi-loop run logs no "Event loop is closed" and that JSON mode stays on
  (the stub only models a 200; it cannot model a genuine response_format rejection or the real gateway's redirect/keep-alive behaviour).
* langchain's TCP keepalive/`TCP_USER_TIMEOUT` socket options: not measurable here (HTTPS_PROXY is set in this sandbox, which bypasses them);
  the bare client does not apply them either way.
