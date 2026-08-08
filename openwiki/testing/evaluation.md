---
type: evaluation harness
title: Evaluation & Golden Data
description: >
  Evaluation framework for merge-engine: golden datasets, scoring system,
  prompt harness, and phase-specific evaluation suites.
tags: [testing, evaluation, golden-data, scoring, prompts]
---

# Evaluation & Golden Data

## Purpose

Evaluation framework for verifying merge-engine correctness against golden
reference data and scoring prompt quality.

## File

`tests/eval/` — evaluation harness

## Evaluation Suite

| File | Purpose |
|------|---------|
| `golden.py` (8KB) | Golden test data definitions |
| `score.py` (14KB) | Matcher precision/recall scoring |
| `scope_plans.json` (6KB) | Eval scope plans |

## Prompt Harness

`tests/prompts/` — prompt quality evaluation

| File | Purpose |
|------|---------|
| `harness.py` (15KB) | Prompt golden-case scorer |
| `scorecard_latest.txt` (2KB) | Latest prompt evaluation scorecard |
| `golden/` | Golden prompt test data directory |

## Phase-Specific Evaluation

| Test File | Phase | Purpose |
|-----------|-------|---------|
| `test_phase_k_gates.py` | K | Gate rejection correctness |
| `test_phase_l_matching.py` | L | Matching precision/recall |
| `test_phase_m_resilience.py` | M | Error handling resilience |
| `test_matching_rules_and_policy.py` | O | Per-entity matching rules |

## Test Coverage

- Golden dataset comparison (`tests/eval/golden.py`)
- Prompt quality evaluation (`tests/prompts/harness.py`)
- Phase-specific evaluation suites for K, L, M, and O phases
