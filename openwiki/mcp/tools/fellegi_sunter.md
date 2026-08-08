---
type: utility
title: Fellegi-Sunter Weight Estimation
description: Probabilistic record linkage weight estimation based on the Fellegi-Sunter model. Estimates match/non-match probabilities for each field comparison to inform blocking thresholds.
tags: [fellegi-sunter, record-linkage, probabilistic, matching]
---

# Fellegi-Sunter Weight Estimation

## Purpose

Implements the Fellegi-Sunter probabilistic record linkage model for estimating match/non-match probabilities for each field comparison. Used by `resolve_entities` to determine which candidate pairs are likely matches.

## File

`src/mcp_server/tools/fellegi_sunter.py` (~11KB)

## Model

The Fellegi-Sunter model assigns a weight to each field comparison:

```
w = log10(m_u / l_u)
```

Where:
- `m_u` = probability of agreement on field u given the pair is a match
- `l_u` = probability of agreement on field u given the pair is NOT a match

Higher positive weights indicate stronger evidence of a match.

## Term-Frequency Adjustment

A match on a rare surname counts for more than a match on a common first name. The implementation adjusts weights by term frequency — comparing against the distribution of values in the source tables.

## Integration

Called by `resolve_entities` (both the orchestrator node and the MCP tool) during the heuristic scoring phase, before LLM scoring.

## Test Coverage

- `tests/unit/test_hybrid_llm_paths.py` — hybrid scoring with Fellegi-Sunter weights
- `tests/integration/test_resolve_entities.py` — end-to-end resolution
