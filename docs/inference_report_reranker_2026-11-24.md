# Inference report — embedding & rerank endpoints on 10.10.9.45

> **RESOLVED 2026-11-25:** the reranker defect below was fixed by ai-stack
> ops on 09:37 UTC (Qwen3 yes/no-logit scoring + judge template applied —
> our hypothesis was correct). Independently re-verified by the ER team the
> same morning: probes A/B/C + name-pair set all pass, order-invariant
> (max drift 0.0014), spread ~0.92, latency unchanged (~19–45 ms). The
> endpoint is ACCEPTED; the sections below stand as the incident record.
> See `docs/inference_rerank_acceptance_2026-11-25.md` for the pass record.

**From:** merge-engine (ER team) · **Date:** 2026-11-24 · **Action requested on:** `:8002` (reranker)
All numbers below were measured live from the ER dev box today; every claim is reproducible with the commands at the end. Auth: the bearer key already shared with the ER team (kept out of this document on purpose).

## TL;DR

| Endpoint | Verdict |
|---|---|
| `:8001` embeddings (`qwen3-embedding-4b`) | ✅ **Accepted as-is.** Fast (~19 ms), deterministic, 2560-dim, sensible cosine geometry. |
| `:8002` reranker (`qwen3-reranker-4b`) | ⚠️ **Needs review.** Transport works and it's fast, but scores are compressed into a ~0.5–0.98 band and **ranking is wrong on short entity-name probes** — the exact workload we hired it for. |

## 1. What we actually serve vs. what was announced

The deployment was announced to us as model-ids `qwen38-embed` / `qwen38-rerank`. `/v1/models` reports:

- `:8001` → `qwen3-embedding-4b` (`max_model_len` 8192, vLLM)
- `:8002` → `qwen3-reranker-4b` (`max_model_len` 8192, vLLM)

We configured the served ids on our side. **Please confirm these ids are stable** (they are baked into our config, and stored vectors will be keyed to the embedding model id).

`:8002` does **not** expose `/v1/chat/completions` (404 Not Found — note this is the FastAPI catch-all answer, so we can't tell whether the route is absent or merely unmounted). We read that as "served in a pooling/scoring task", which is correct for a reranker; confirming the exact vLLM launch flags is the core ask of this report.

## 2. Embeddings `:8001` — measurements (no action needed)

- Latency, single short text ×5: median **19 ms** (min 19 / max 22)
- **Deterministic**: identical vectors across repeated calls
- **2560 dimensions**, `dimensions` field present in response
- Geometry sane on entity names (cosine):
  - "Samsung Electronics Co" vs "Samsung Corp" → **0.986**
  - "Samsung Electronics Co" vs "Apple Inc" → **0.790**

One question: the base checkpoint is Qwen3-Embedding-4B which natively emits **2560-dim** — matches, so no Matryoshka truncation appears to be configured. Please confirm no `--hf-overrides` changed pooling, since we'll store vectors under `dimensions: 2560` and a mid-corpus change would be silently corrupting.

## 3. Reranker `:8002` — the problem

### 3.1 What works

- `POST /v1/rerank` (Cohere-style body) responds correctly; `index` mapping correct; `top_n` respected.
- Fast: 2 docs → median **19 ms**; 32 docs → **46 ms**.
- Deterministic across repeats, and scores are **order-invariant** (shuffling document order does not change per-document scores — good sign, it's not a position artifact).

### 3.2 What fails: the score itself

Probe set A (short company names — our actual workload):

```
query = "Samsung Electronics Co Ltd"
documents = ["Samsung Electronics Co., Ltd.",   ← exact match
             "Samsung Life Insurance",
             "Apple Inc."]
```

Measured relevance_score, stable across runs and document orders:

| Document | score |
|---|---|
| Apple Inc. | **0.945** |
| Samsung Electronics Co., Ltd. (exact match) | 0.853 |
| The sky is blue (added control) | 0.938 |

**The exact match ranks last**, and an irrelevant sentence scores within noise of it. The score band is compressed (essentially everything lands in 0.5–0.98, spread ≈ 0.1 on a 2-doc probe), so the signal is near-undecidable even if the ordering were right.

Probe set B (paraphrase pair, longer text):

```
query = "The capital of France is Paris."
"Paris is the capital of France." 0.978  >  "Samsung makes phones." 0.948
```

Ordering right, but again a 0.03 spread on obviously-irrelevant control text.

Probe set C (32 docs of filler `Company N Ltd` + 1 true match at index 7): the true match **did** win. So it's not globally broken — short name-vs-name scoring (exactly the entity-resolution case) is where it degrades, and compressed absolute scores make any threshold on top of it meaningless.

### 3.3 Our hypothesis (please verify or refute)

Qwen3-Reranker is a **generative** reranker: the official recipe scores by comparing the yes/no next-token logits over a specific instruction template, *not* a cross-encoder classification head. The symptom pattern — compressed 0.x band, near-flat for irrelevant docs, wrong on short name pairs — is what it looks like when vLLM's score API uses a **default pooler** (e.g. classifier/last-token) instead of Qwen3's yes/no-logit normalization.

If the deployment is `vllm serve Qwen/Qwen3-Reranker-4B --task score ...` (or v0.31's equivalent), please check:

1. the **pooling/scoring config** actually applied (Qwen3 rerankers need the model-specific scoring, per Qwen's deployment docs for vLLM — often `--hf-overrides` with `PoolerConfig` / token-similarity settings, and the correct instruction template);
2. whether the served checkpoint is the base model vs. the one with the scoring setup Qwen ships;
3. the **chat/instruct template** used: if the template isn't applied, short queries lose all their signal, which matches the name-pair failure specifically.

### 3.4 Acceptance test we'll re-run when you re-deploy

We will re-run probes A/B/C plus a small labeled name-pair set and require: (1) exact/near-exact name matches score top on probe A, (2) score spread on obviously-irrelevant controls ≥ ~0.3 so a threshold can exist, (3) stability under repeat/reorder (already OK). Final acceptance is our D-53 bake-off against ground truth, but probes A/B are the smoke test.

## 4. Reproduce

```bash
KEY=*** in ER .env>
# served model ids
curl -s http://10.10.9.45:8001/v1/models -H "Authorization: Bearer $KEY"
curl -s http://10.10.9.45:8002/v1/models -H "Authorization: Bearer $KEY"

# probe A (the failing one)
curl -s http://10.10.9.45:8002/v1/rerank -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" -d '{
  "model":"qwen3-reranker-4b",
  "query":"Samsung Electronics Co Ltd",
  "documents":["Samsung Electronics Co., Ltd.","Samsung Life Insurance","Apple Inc."],
  "return_documents":false}'

# embeddings sanity
curl -s http://10.10.9.45:8001/v1/embeddings -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" -d '{
  "model":"qwen3-embedding-4b",
  "input":["Samsung Electronics Co","Samsung Corp","Apple Inc"]}'
```

**Bottom line:** embeddings ship; reranker needs its scoring path inspected before the ER side can trust a single number out of it.
