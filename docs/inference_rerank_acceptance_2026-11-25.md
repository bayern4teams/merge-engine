# Reranker acceptance — independent re-verification (2026-11-25)

In response to: ai-stack ops "AI-stack inference services — full status &
reranker incident report" (2026-11-25). All numbers below were re-measured
live from the ER dev box **after** their 09:37 UTC redeploy, with the same
probe code that failed on 2026-11-24. No numbers copied from their report.

## Verdict: ACCEPTED — all four §3.4 criteria pass

| Probe | 2026-11-24 (broken) | 2026-11-25 (ours, live) | Criterion |
|---|---|---|---|
| A: exact company-name match | 0.850, ranked 3rd | **0.9217 — top-1** | criterion 1 ✅ |
| A: controls (Apple / sky / sibling co) | 0.949 / 0.935 / 0.625 | 0.0006 / 0.0000 / 0.024 | spread 0.92 ✅ (≥0.3) |
| B: paraphrase vs irrelevant | 0.978 / 0.948 | **0.9989** / 0.0001 | ordering ✅ |
| C: 32-doc filler, true match at idx 7 | top-1 by luck | **0.9221 top-1**, filler ≤0.0001 | ✅ |
| Name pairs (Nestle S.A. vs Nestle SA / Novartis / Pfizer) | — | **0.9332** / 0.0193 / 0.0002 | ✅ |
| Order-invariance (3 shuffles, per-doc scores) | (was stable but wrong) | **max drift 0.0014, top-1 identical** | stable ✅ |
| Latency | 19 ms / 46 ms (32 docs) | 19 ms / 45 ms | no regression ✅ |

Their root cause (default pooler without `is_original_qwen3_reranker`, no
architecture routing, no judge chat-template) matches our 11-24 hypothesis;
the symptom set (compressed 0.x band, short-name failure, long-filler
"working") is fully explained by it.

## One correction to their claim

"**No** client-side change is required" is not fully true: our config and
client docstrings record the 11-24 finding ("ranking was WRONG on name
pairs, treat as unvalidated signal"). ER has updated those notes today
(incident record kept). Their API surface — ports, model id
`qwen3-reranker-4b`, `/v1/rerank` body, auth — is genuinely unchanged, so
nothing in code *breaks*; only stale warnings about trusting the scores
needed retiring.

## What this unlocks on our side (internal, sequenced)

1. P22/D-53: the reranker now earns a place in the bake-off; calibration
   (thresholds, score band) runs against ground truth — not before it.
2. Acceptance item 9 / quality asserts remain ground-truth-gated as agreed.

## Reproduce (no secrets inline; key is in ER `.env`)

See the ER reranker probe script (same code as the acceptance run): probes
A/B/C + shuffled stability, `POST /v1/rerank` on `10.10.9.45:8002` with
model `qwen3-reranker-4b`.
