"""LLM cost controls and accounting — Phase O.5.

Two separate jobs, deliberately kept apart:

* a **cache**, so the same question is not paid for twice, and
* a **ledger**, so "use the LLM more" stays a decision backed by a number
  rather than a preference.

The cache is keyed on the *normalised* prompt pair, which matters more than it
sounds: entity resolution asks about record pairs, and across runs — and
within one run across blocks — the identical pair recurs constantly. Two
prompts differing only in whitespace are the same question and must hit.

Nothing here changes an answer. A cache that could return a different result
than the model would have is worse than no cache, so the key covers every
input that can affect the output: model, temperature, both prompts, and the
requested response format.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("merge-engine.llm.cost")

# Runs of whitespace differ between prompt builds without changing meaning.
_WS = re.compile(r"\s+")

# Entries are small (a prompt hash and a response); this bounds a long session
# rather than being a tuning knob.
_MAX_ENTRIES = 5000


def normalise_prompt(text: str) -> str:
    """Collapse formatting noise so equivalent prompts share a key."""
    return _WS.sub(" ", (text or "").strip())


def cache_key(
    *,
    model: str,
    temperature: Any,
    system_prompt: str,
    user_prompt: str,
    response_format: Any = None,
    json_mode: bool = False,
) -> str:
    """A stable digest of everything that can change the model's answer.

    Response format is part of the key on purpose: the same prompt asked with
    and without a schema returns differently shaped output, and serving one
    for the other would corrupt the caller silently.
    """
    payload = json.dumps(
        {
            "model": model,
            "temperature": temperature,
            "system": normalise_prompt(system_prompt),
            "user": normalise_prompt(user_prompt),
            "format": response_format,
            "json_mode": bool(json_mode),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SemanticCache:
    """Bounded, thread-safe LRU over normalised prompts.

    Thread-safe because the workflow runs the graph on a background thread
    while the API serves status reads on another.
    """

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        self._entries: OrderedDict[str, Any] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> tuple[bool, Any]:
        """``(found, value)`` — a tuple because ``None`` is a legal value."""
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                self.hits += 1
                return True, self._entries[key]
            self.misses += 1
            return False, None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "hits": self.hits,
                "misses": self.misses,
                "lookups": total,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
                "entries": len(self._entries),
            }


class CostLedger:
    """Token and call counts per run, so cost is reportable rather than felt.

    Prices are not hardcoded. A wrong number printed as currency is worse than
    no number, and this project's model changes whenever a quota runs out — so
    the ledger records tokens, and pricing is supplied by whoever knows it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}

    def record(
        self,
        run_id: str,
        *,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached: bool = False,
    ) -> None:
        with self._lock:
            entry = self._runs.setdefault(
                run_id,
                {
                    "calls": 0,
                    "cached_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "tokens_saved": 0,
                    "models": {},
                },
            )
            entry["calls"] += 1
            if cached:
                entry["cached_calls"] += 1
                # A cache hit costs nothing; count what it avoided instead, so
                # the saving is visible rather than showing up as absence.
                entry["tokens_saved"] += input_tokens + output_tokens
            else:
                entry["input_tokens"] += input_tokens
                entry["output_tokens"] += output_tokens
            if model:
                entry["models"][model] = entry["models"].get(model, 0) + 1

    def for_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._runs.get(run_id)
            if not entry:
                return {
                    "calls": 0,
                    "cached_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "tokens_saved": 0,
                    "cache_hit_rate": 0.0,
                    "models": {},
                }
            snapshot = dict(entry)
            snapshot["models"] = dict(entry["models"])
        snapshot["total_tokens"] = snapshot["input_tokens"] + snapshot["output_tokens"]
        snapshot["cache_hit_rate"] = (
            round(snapshot["cached_calls"] / snapshot["calls"], 4) if snapshot["calls"] else 0.0
        )
        return snapshot

    def reset(self, run_id: str | None = None) -> None:
        with self._lock:
            if run_id is None:
                self._runs.clear()
            else:
                self._runs.pop(run_id, None)


def token_usage(response: Any) -> tuple[int, int]:
    """Best-effort (input, output) token counts from a LangChain response.

    Providers disagree about where they put this and some omit it entirely,
    so an unknown count is reported as zero rather than estimated — an
    invented token count would make the ledger worse than useless.
    """
    for attr in ("usage_metadata", "response_metadata"):
        meta = getattr(response, attr, None)
        if not isinstance(meta, dict):
            continue
        usage = meta.get("token_usage") or meta.get("usage") or meta
        if not isinstance(usage, dict):
            continue
        prompt = usage.get("input_tokens", usage.get("prompt_tokens"))
        completion = usage.get("output_tokens", usage.get("completion_tokens"))
        if prompt is not None or completion is not None:
            return int(prompt or 0), int(completion or 0)
    return 0, 0


# Process-wide singletons: the cache is most valuable across runs, since the
# same record pairs and the same schema recur every time a merge is repeated.
SEMANTIC_CACHE = SemanticCache()
COST_LEDGER = CostLedger()
