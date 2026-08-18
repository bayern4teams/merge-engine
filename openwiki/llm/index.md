# Files

- [LLMClient](client.md) - Unified LLM client wrapping LangChain models (OpenAI, Azure, Anthropic) with structured JSON output, quota fallback chain, model chain advancement, and semantic cache integration.

- [Cost Controls](cost.md) - LLM cost controls in merge-engine: semantic cache (bounded LRU, 5000 entries, thread-safe) and cost ledger (token accounting with per-model pricing). Cache key covers model, temperature, system prompt, user prompt, and format.

- [LLM Integration Overview](overview.md) - LLM layer architecture for merge-engine, covering the unified LLMClient, cost controls (semantic cache + token ledger), prompt template system, and Langfuse tracing integration used by all pipeline nodes.

- [Prompt Templates](prompts.md) - Prompt template system for merge-engine: six stages each with a (system, human) prompt pair loaded from files in src/llm/prompts/. Covers the templates, their purpose, and which orchestrator nodes consume them.

- [Langfuse Tracing](tracing.md) - Langfuse integration for merge-engine: shared callback handler on every LLM call, trace ID binding for HITL resume linkage, span nesting under graph nodes, and graceful degradation when Langfuse is unreachable.

