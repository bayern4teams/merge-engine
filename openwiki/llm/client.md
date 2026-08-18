---
type: llm client
title: LLMClient
description: >
  Unified LLM client wrapping LangChain models (OpenAI, Azure, Anthropic) with
  structured JSON output, quota fallback chain, model chain advancement, and
  semantic cache integration.
tags: [llm, client, langchain, quota-fallback]
---

# LLMClient

## Purpose

Unified async client wrapping LangChain chat models. Every pipeline node that
needs LLM capability calls through this class.

## File

`src/llm/client.py`

## Constructor

```python
class LLMClient:
    def __init__(self, config: dict, session_name: str = "merge-engine"):
        self.provider = config.get("provider", "openai")  # "openai" | "azure" | "anthropic"
        self.model_name = config.get("model", "gpt-4o")
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url")
        self.temperature = config.get("temperature", 0.0)
        self.structured_output = config.get("structured_output", True)
        self.cache_enabled = config.get("cache_enabled", True)
        # Automatic quota fallback chain (Phase O.5):
        self.model_chain = [self.model_name, *(config.get("fallback_models") or [])]
        self._model_chain_idx = 0
```

## Model Chain / Quota Fallback

When a call hits a quota or billing error, `call()` advances through the
`model_chain` instead of failing:

```python
_QUOTA_ERROR_MARKERS = (
    "quota",
    "429",
    "403",
    "rate limit",
    "rate_limit",
    "insufficient_quota",
    "allocationquota",
    "billing",
)


def _is_quota_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _QUOTA_ERROR_MARKERS)
```

The chain is tried in order: primary model → fallback_models. A quota error
advances to the next model. Same-account quota errors can never resolve
mid-run, so retrying the same model would be futile.

## Structured Output

When `structured_output=True` and the provider supports it, the model returns
a JSON-structured response parsed against the requested schema:

```python
async def call_json(
    system_prompt: str,
    user_prompt: str,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call LLM with JSON response format, with cache lookup."""
```

## Semantic Cache

Integrates with `src.llm.cost.SEMANTIC_CACHE` to avoid paying for repeated
prompts:

1. Computes cache key from (model, temperature, system prompt, user prompt, response format)
2. On cache hit: returns cached result without calling the model
3. On cache miss: calls model, stores result, increments cost ledger

Token estimation for cache hits: `~4 characters per token` (approximate, used only for display).

## Tracing

Every `ainvoke` call carries the shared Langfuse callback handler from
`src.llm.tracing.trace_config`. Each call produces a generation span nested
under the owning graph-node span.

## Test Coverage

- `tests/unit/test_llm_client.py` — client initialization, provider dispatch, structured output
