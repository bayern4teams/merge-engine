"""Deterministic comparison-vector fixtures for the P13 acceptance tests."""

from __future__ import annotations

import random

E, S, W, D, N = "exact", "strong", "weak", "disagree", "null"


def _draw(rng: random.Random, probs: dict[str, float]) -> str:
    x, acc = rng.random(), 0.0
    for lvl, p in probs.items():
        acc += p
        if x < acc:
            return lvl
    return D


def vectors(rng: random.Random, n: int, spec: dict[str, dict[str, float]]):
    """n vectors; per field a level drawn from spec[field]; value always None (no TF)."""
    return [{f: (_draw(rng, p), None) for f, p in spec.items()} for _ in range(n)]


# Blocking on `zip` makes look-alikes: even NON-matching blocked pairs agree on zip.
BLOCKED_MATCH = {
    "zip": {E: 0.92, S: 0.04, W: 0.02, D: 0.02},
    "name": {E: 0.85, S: 0.08, W: 0.05, D: 0.02},
}
BLOCKED_NONMATCH = {
    "zip": {E: 0.80, S: 0.05, W: 0.05, D: 0.10},
    "name": {E: 0.02, S: 0.03, W: 0.10, D: 0.85},
}
# Random (unblocked) cross-product pairs: agreement on either field is rare.
RANDOM_PAIRS = {
    "zip": {E: 0.04, S: 0.02, W: 0.04, D: 0.90},
    "name": {E: 0.004, S: 0.006, W: 0.03, D: 0.96},
}


def blocked_vectors(seed: int = 7, n_match: int = 120, n_non: int = 360):
    rng = random.Random(seed)
    v = vectors(rng, n_match, BLOCKED_MATCH) + vectors(rng, n_non, BLOCKED_NONMATCH)
    rng.shuffle(v)
    return v


def sampled_vectors(seed: int = 11, n: int = 3000):
    return vectors(random.Random(seed), n, RANDOM_PAIRS)


def small_snapshot_vectors():
    """Tiny fixed fixture for the no-new-argument regression snapshot."""
    v = []
    for i in range(40):
        v.append({"a": (E if i % 3 else D, "x" if i % 3 else None), "b": (S if i % 2 else E, None)})
    for i in range(20):
        v.append({"a": (D, None), "b": (D if i % 4 else N, None), "c": (W, None)})
    return v
