---
type: llm cost controls
title: Cost Controls
description: >
  LLM cost controls in merge-engine: semantic cache (bounded LRU, 5000 entries,
  thread-safe) and cost ledger (token accounting with per-model pricing).
  Cache key covers model, temperature, system prompt, user prompt, and format.
tags: [llm, cost, caching, token-accounting]
---

# Cost Controls

## Purpose

Two separate subsystems deliberately kept apart in `src/llm/cost.py`:

1. **Semantic Cache** — so the same question is not paid for twice
2. **Cost Ledger** — so "use the LLM more" stays a decision backed by a number

Shared module-level singletons: `SEMANTIC_CACHE` and `COST_LEDGER`.

## File

`src/llm/cost.py`

## Semantic Cache

Bounded, thread-safe LRU over normalized prompts.

### Key Computation

Cache keys are SHA-256 hashes of a JSON payload containing every input that
can affect the model's answer:

```python
def cache_key(
    *,
    model: str,
    temperature: Any,
    system_prompt: str,
    user_prompt: str,
    response_format: Any = None,
    json_mode: bool = False,
) -> str:
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
```

Normalization collapses whitespace so equivalent prompts share a key.

### Cache Properties

| Property | Value |
|----------|-------|
| Max entries | 5,000 |
| Thread safety | Yes (threading.Lock) |
| Eviction | LRU on full |
| Stats | `.hits` and `.misses` counters |

### Module-Level Singleton

```python
SEMANTIC_CACHE = SemanticCache()  # max_entries = 5000
```

## Cost Ledger

Token accounting tracking per-model usage and estimated cost:

```python
class CostLedger:
    def record(self, model: str, prompt_tokens: int, completion_tokens: int)
    def total_tokens(self) -> dict
    def estimate_cost(self, model: str) -> float
```

Pricing is configured per model in the cost data.

## Test Coverage

- `tests/unit/test_llm_cost.py` — cache key computation, LRU eviction, cost ledger accounting
