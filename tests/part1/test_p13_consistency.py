"""P13 consistency: sampled u must be paired with the random-pair match prior.

Pairing random-pair ``u`` with the blocked-candidate prior counts blocking agreement
twice and over-scores non-matches. On the same blocked candidates the new model must
be no worse than the old one: precision@0.5 >=, false positives <=, over 3 seeds.
"""

from __future__ import annotations

import random

import pytest

from src.mcp_server.tools import fellegi_sunter as fs
from src.mcp_server.tools.resolve_entities import _comparison_level, _normalize

FIELDS = ("name", "zip", "phone")


def _make(seed: int, n_entities: int = 350):
    rng = random.Random(seed)
    firsts = [f"First{i}" for i in range(30)]
    lasts = [f"Last{i}" for i in range(8)]
    zips = [f"{10000 + i * 37}" for i in range(25)]
    weights = [1 / (i + 1) for i in range(len(zips))]
    recs = []
    for e in range(n_entities):
        name = f"{rng.choice(firsts)} {rng.choice(lasts)} Tag{rng.randrange(40)}"
        rec = {"eid": e, "name": name, "zip": rng.choices(zips, weights)[0]}
        rec["phone"] = f"555{rng.randrange(10**7):07d}"
        recs.append(rec)
        if rng.random() < 0.3:  # a duplicate with typo / moved zip / missing phone
            dup = dict(rec)
            if rng.random() < 0.5:
                i = rng.randrange(len(name))
                dup["name"] = name[:i] + name[i + 1 :]
            if rng.random() < 0.15:
                dup["zip"] = rng.choices(zips, weights)[0]
            if rng.random() < 0.2:
                dup["phone"] = None
            recs.append(dup)
    return recs


def _vec(a, b):
    return {f: _comparison_level(a.get(f), b.get(f), _normalize, field_name=f) for f in FIELDS}


def _precision_fp(model, vecs, truth):
    hit = [t for v, t in zip(vecs, truth, strict=True) if model.probability(v) >= 0.5]
    return (sum(hit) / len(hit) if hit else 1.0), hit.count(False)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_sampled_u_with_random_pair_prior_is_no_worse_than_blocked_em(seed):
    recs = _make(seed)
    n = len(recs)
    blocks: dict[tuple, list[int]] = {}
    for i, r in enumerate(recs):  # block on the first two name tokens
        blocks.setdefault(tuple(r["name"].split()[:2]), []).append(i)
    pairs = [(i, j) for b in blocks.values() for k, i in enumerate(b) for j in b[k + 1 :]]
    truth = [recs[i]["eid"] == recs[j]["eid"] for i, j in pairs]
    vecs = [_vec(recs[i], recs[j]) for i, j in pairs]
    tfs = fs.build_term_frequencies(
        [{k: v for k, v in r.items() if k != "eid"} for r in recs], _normalize
    )
    sampled = [_vec(recs[i], recs[j]) for i, j in fs.sample_random_pairs(n, fs.SAMPLE_PAIRS, seed)]

    old = fs.estimate_model(vecs, tfs, set())
    new = fs.estimate_model(
        vecs, tfs, set(), sampled_vectors=sampled, population_pairs=n * (n - 1) // 2
    )
    p_old, fp_old = _precision_fp(old, vecs, truth)
    p_new, fp_new = _precision_fp(new, vecs, truth)
    assert p_new >= p_old, f"precision@0.5 {p_new:.3f} < blocked-EM {p_old:.3f}"
    assert fp_new <= fp_old, f"false positives {fp_new} > blocked-EM {fp_old}"
    assert 0.0 < new.match_prior < old.match_prior  # the random-pair rate, not the candidate rate


def test_sampled_u_without_population_pairs_is_refused():
    with pytest.raises(ValueError, match="population_pairs"):
        fs.estimate_model([], {}, sampled_vectors=[{"a": ("exact", None)}])
