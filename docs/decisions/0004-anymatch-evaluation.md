# 0004. AnyMatch Evaluation & Strategic Adjudication Architecture

- **Status:** Decided (Deliberately NOT adopted for current pipeline; Ditto serialization adopted)
- **Date:** 2026-08-07
- **Authors:** Bayern MergeEngine Team (Branch `feat/matching-precision` - B4.3)

---

## Context

AnyMatch (*"AnyMatch: Entity Resolution with Small Language Models"*, arXiv:2405.04870) is a specialized fine-tuned entity-matching model reporting state-of-the-art performance (F1 87.5 vs GPT-4's 87.4) using ~3 orders of magnitude fewer parameters than general LLMs.

We evaluated whether to replace or augment the LLM borderline adjudicator (`_judge_pairs_with_llm` in `src/mcp_server/tools/resolve_entities.py`) with AnyMatch.

---

## Decision

**We deliberately do NOT adopt AnyMatch for the Bayern MergeEngine pipeline at this time.**

Instead, we adopt the underlying Ditto-style `COL/VAL` record serialization format (*VLDB 2020*) to format structured canonical evidence for the existing `LLMClient` pipeline.

---

## Technical Rationale

1. **Adjudication is not the system bottleneck**:
   LLM adjudication is strictly capped at `_MAX_LLM_PAIRS = 200` per entity type and only sees borderline pairs ($0.70 \le \text{confidence} < 0.95$). Performance and accuracy bottlenecks were caused by cross-origin schema divergence (fixed by C0a canonical projection), not model parameters.
2. **Benchmark mismatch**:
   AnyMatch benchmarks (Walmart–Amazon, Abt–Buy, DBLP–Scholar) measure performance on **already schema-aligned** datasets. In real multi-origin data migration, cross-origin schema divergence (`company_name` vs `org_title`) is the primary failure mode, which standard EM benchmarks do not measure.
3. **Infrastructure & dependency complexity**:
   Adopting AnyMatch requires introducing PyTorch, HuggingFace Transformers, model artifact distribution, and a dedicated GPU/CPU local inference server. This breaks the unified OpenAI/Anthropic API choke point managed by `LLMClient` and DSPy adapters.
4. **Preserving useful innovations**:
   The value of entity matching research lies in Ditto-style `COL/VAL` attribute serialization (`COL field_name VAL value_content`). Applying `COL/VAL` serialization to standard `LLMClient` calls yields structured attribute-level evidence without adding heavy local dependencies.

---

## Revisit Trigger

Re-evaluate AnyMatch or a local small LM adjudicator if:
1. The borderline candidate band exceeds **5,000 pairs per run** on production datasets, **AND**
2. LLM API cost or latency becomes a **measured bottleneck** in execution profiles.
