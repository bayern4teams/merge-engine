[< back to docs/INDEX.md](../INDEX.md)

# The LLM layer and run profiles

## 11. The LLM layer: client, prompts, cache, cost ledger

`src/llm/client.py` wraps LangChain's `ChatOpenAI`-compatible client;
`src/llm/prompts/` holds `.system.txt`/`.human.txt` template pairs per LLM
call site (dependency analysis, entity-table selection, entity resolution,
field mapping, request parsing); `src/llm/cost.py` is the semantic cache and
cost ledger (Phase O.5).

**Why a semantic cache at all, and why its key includes more than just the
prompt text.** Identical (prompt, model, temperature) combinations recur
constantly across runs — re-running the same merge, or running variations of
it, regenerates prompts that are often character-for-character identical.
`cache_key()` hashes `(model, temperature, normalised system prompt,
normalised human prompt, response_format, json_mode)` — **every field that
can change the answer's shape or content is part of the key**, deliberately,
because a cache entry that could return an answer different from what the
model would currently produce is worse than no cache at all; the whole value
of caching collapses the moment it can silently serve a stale-shaped
response. `normalise_prompt()` collapses whitespace runs before hashing, so
two prompts differing only in incidental formatting still share a cache
entry.

**`SemanticCache` is a bounded, thread-safe LRU**
(`OrderedDict`-backed, `_max_entries=5000` default, `threading.Lock`
guarded). `.get()` returns `(found: bool, value)` rather than just `value`,
specifically so a legitimately cached `None` response is distinguishable
from a cache miss — returning bare `None` for both would make a cached
"the model said nothing useful" indistinguishable from "never asked."

**`CostLedger` records tokens, not currency, on purpose.** Prices aren't
hardcoded anywhere in this codebase, because the actual LLM model in use
changes whenever a quota runs out — as of 2026-08-01 this is handled
automatically (see below), not by manually editing `config.yaml` — and a
dollar figure computed against the wrong model's pricing is actively
misleading — worse than not reporting a cost at all. `token_usage()`
extracts real usage from a LangChain response's `usage_metadata` /
`response_metadata`, trying both OpenAI-style (`prompt_tokens`/
`completion_tokens`) and generic (`input_tokens`/`output_tokens`) field
names, and returns `(0, 0)` — never an estimate — if neither is present. A
cache **hit** still calls `COST_LEDGER.record(..., cached=True, ...)`, which
increments `tokens_saved` by an estimated token count
(`_estimate_tokens`, a character-count/4 heuristic used *only* for this
savings figure, never for real billing) instead of the real (zero) token
cost of a cache hit — so the ledger can report both "how much this run
actually cost" and "how much the cache saved it."

**The cost ledger initially recorded nothing, and the bug is worth
understanding because it's a generic trap.** The ledger keyed its
accumulation on `session_name`, a fixed constant string
(`"merge-engine"`), not on the actual run's `thread_id`. Every run therefore
accumulated into the same bucket forever, and because the ledger is queried
per-run via `GET /api/workflow/{id}/cost`, every individual run's query
showed **0 calls** even while the semantic cache correctly logged 18
lookups for that run. The fix threads the real `run_id` into `llm_config` at
both call sites in `get_initial_state()` (`{**llm_config, "run_id": run_id}`
— see §2), which `LLMClient.__init__` reads via
`config.get("run_id", "") or session_name` (falling back to the old
behaviour only if `run_id` is genuinely absent, e.g. very old checkpoints).

**Automatic quota fallback (2026-08-01).** `LLMClient.call()` builds
`self.model_chain = [config.model] + config.fallback_models` and tracks a
`_model_chain_idx`. On an exception whose text matches `_is_quota_error`
(substrings: `quota`, `429`, `403`, `rate limit`, `insufficient_quota`,
`allocationquota`, `billing`), `_advance_model()` moves to the next entry,
resets the cached `_model` so `_get_model()` rebuilds against the new name,
and the same `call()` invocation retries — the caller never sees the
failure unless every model in the chain is exhausted. A non-quota error
(malformed request, network blip) does **not** advance the chain — it would
fail identically on every model, so burning through the list would only
delay the real failure. This replaced a manual runbook (edit
`config.yaml`, kill the server, restart, re-launch the workflow) that cost
real wall-clock time during live verification: see `llm-model-fallback-chain`
in project memory for the current 9-model chain and why it changed.

**A module-scope placement bug that silently truncated a whole class.**
`_estimate_tokens` was initially inserted *inside* `client.py`'s `LLMClient`
class body, mid-method. Python doesn't raise an error for this — it just
ends the class definition early at the dedent, silently turning
`call_json`/`call_batch` (everything textually after the misplaced function)
into orphaned **module-level** functions instead of class methods. Five
previously-passing tests failed with
`'LLMClient' object has no attribute 'call_json'`. The lesson generalises:
a helper function accidentally placed at the wrong indentation level inside
a class body is a silent structural bug in Python, not a syntax error — it
only surfaces when something calls the now-missing method.

---

## 12. Run profiles — pinning non-deterministic decisions

`src/orchestrator/profiles.py` (Phase O). This subsystem exists because of a
measured, not assumed, problem: **two full runs of identical source data,
producing identical candidate pairs (28,170), identical clusters (2,482),
and identical row counts, nonetheless produced destination schemas with 15
columns in one run and 13 in the other** — because the LLM's field-mapping
call is not deterministic on where `customer_id` folds into `id`. Matching is
deterministic (it's arithmetic over a fitted model); the *metadata* decisions
around it are not, because they go through an LLM. A profile is what makes
"re-run the same merge" a meaningful, reproducible operation instead of a
coin flip on schema shape.

`PINNED_KEYS = ("entity_tables", "matching_rules", "conflict_policy",
"field_mappings")` — exactly the four things Phase O made pin-able,
corresponding to O.1 through O.4. `build_profile()` constructs the saved
JSON, `save_profile()` persists it under `config/profiles/<name>.json`
(preserving the original `created_at` across re-saves), `load_profile()` /
`list_profiles()` / `delete_profile()` round it out.

**Secrets are stripped twice, deliberately redundantly.**
`strip_secrets()` recursively blanks any dict key whose lowercased name
matches `password|passwd|secret|api_key|token`, and it's called both by the
caller building the profile *and again, unconditionally, inside
`save_profile()`* — "never trust the caller" is the explicit design
rationale, because a profile file is the one artifact in this system
explicitly intended to be portable/shareable, and a single missed call site
anywhere upstream must not be able to leak a live credential into it.

**Credentials are restored by connection coordinates, never by list
position.** `connection_key(origin)` builds
`"host|port|database|user|type"` (lowercased, stripped); when a saved
profile is replayed, `restore_credentials(saved, live)` matches each saved
origin to a *currently configured* live origin by this key, not by index in
a list. If origins were reordered (or one was removed) since the profile was
saved, matching by position would silently repoint the profile's pinned
decisions at the wrong database. `restore_credentials` also returns a
`missing` list of coordinates it couldn't match — the caller must treat any
non-empty `missing` as "this run cannot proceed," never merely as a
warning to log and continue past.

**A node reusing a pin must actually skip the LLM call it's pinning, or the
profile is decorative.** `analyze_dependencies` checks whether every target
entity already has a pinned table selection and, if so, skips calling
`select_entity_tables_impl` (§7's O.1 companion) entirely, recording a note
that it reused the profile. `map_fields_node` does the equivalent for a
pinned mapping (with the reconciliation fix described in §9). Simply storing
pins without a node actually branching on their presence would silently keep
calling the LLM every run and re-deriving a possibly-different answer,
making the profile a lie about what actually happened.

**`capture_from_state` only fires on clean completion, not on any run that
touched a profile.** `WorkflowManager._finalize_stream_status()` calls
`_capture_profile()` only inside the branch where `wf["status"] ==
"completed"` — never on a failed or partially-skipped run. Capturing from an
incomplete run would pin a half-formed mapping (e.g. only some entities
processed before a failure) that the *next* run would then silently inherit
as if it were a complete, validated decision.

---

