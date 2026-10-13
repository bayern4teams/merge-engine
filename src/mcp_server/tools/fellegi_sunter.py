"""Probabilistic record scoring (Fellegi-Sunter) with term-frequency weighting.

The previous scorer averaged string similarities and compared the result to
two hand-picked constants. That has no way to express the single most useful
fact in record linkage: **agreeing on a rare value is much stronger evidence
than agreeing on a common one.** Two people both named "Rory" tells you
almost nothing when 248 first names in the table are shared; two people with
the same rare surname tells you a great deal.

Fellegi-Sunter models this directly. For each field we estimate:

    m = P(the field agrees | the records ARE the same entity)
    u = P(the field agrees | the records are NOT the same entity)

The evidence a field contributes is the log ratio ``log2(m/u)`` when it
agrees, and ``log2((1-m)/(1-u))`` when it disagrees. Summing these across
fields gives a total match weight, which converts back to a probability.

``u`` is where term frequency enters: the chance two *random* records share a
value IS that value's frequency, so a value held by 1 record in 1500 has a
far smaller ``u`` — and therefore far more weight — than one held by 40.

``m`` is estimated **unsupervised** from the candidate pairs themselves (a
simplified EM pass), with priors that keep a cold start sane; no labelled
training data is required. ``u`` is, when the caller supplies comparison
vectors of randomly sampled record pairs, taken from those instead (P13): the
candidate set is blocked down to look-alikes, so a ``u`` estimated there is far
too high. See the comment above ``_sampled_u`` before touching either path.

Reference: Fellegi & Sunter (1969); the term-frequency treatment follows the
approach used by Splink.
"""

from __future__ import annotations

import logging
import math
import random
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")

logger = logging.getLogger("merge-engine.tools.fellegi_sunter")

# Comparison outcomes, strongest first. Each gets its own m/u pair so a
# fuzzy match is not treated as equal evidence to an exact one.
LEVEL_EXACT = "exact"
LEVEL_STRONG = "strong"  # minor typo / formatting difference
LEVEL_WEAK = "weak"  # loosely similar
LEVEL_DISAGREE = "disagree"
LEVEL_NULL = "null"  # one side missing — no evidence either way

_LEVELS = (LEVEL_EXACT, LEVEL_STRONG, LEVEL_WEAK, LEVEL_DISAGREE)

# Priors used before any data is seen, and as the floor during estimation.
# Deliberately conservative: an unseen field should not dominate the score.
_PRIOR_M = {
    LEVEL_EXACT: 0.85,
    LEVEL_STRONG: 0.09,
    LEVEL_WEAK: 0.04,
    LEVEL_DISAGREE: 0.02,
}
_PRIOR_U = {
    LEVEL_EXACT: 0.02,
    LEVEL_STRONG: 0.03,
    LEVEL_WEAK: 0.10,
    LEVEL_DISAGREE: 0.85,
}

# Keeps log ratios finite when a level is never observed.
_EPS = 1e-6

# How strongly the observed data pulls the priors. EM over unlabelled pairs
# is noisy on small inputs, so the priors keep a real say.
_PRIOR_WEIGHT = 20.0

# Assumed share of candidate pairs that are true matches before estimation.
_PRIOR_MATCH_RATE = 0.10

# Random record pairs drawn for the sampled ``u`` (report P13: "a few thousand").
# Not a corpus fit: the standard error of a proportion estimated from k pairs is
# at most 0.5/sqrt(k), so k=3000 bounds the ABSOLUTE error on any field's u by
# ~0.009 whatever the data. That is only an absolute bound: the RELATIVE error is
# large for a rare level (u~0.002 -> ~40%, u~0.0005 -> ~80%), which is the
# ceiling named below.
SAMPLE_PAIRS = 3000


@dataclass
class FieldModel:
    """Learned m/u probabilities for one field."""

    name: str
    m: dict[str, float] = field(default_factory=lambda: dict(_PRIOR_M))
    u: dict[str, float] = field(default_factory=lambda: dict(_PRIOR_U))
    # value -> frequency among all records, for term-frequency adjustment
    term_frequency: dict[str, float] = field(default_factory=dict)
    is_unique_id: bool = False

    def weight(self, level: str, value: str | None = None) -> float:
        """Bits of evidence this comparison contributes (may be negative)."""
        if level == LEVEL_NULL:
            return 0.0

        m = max(self.m.get(level, _EPS), _EPS)
        u = max(self.u.get(level, _EPS), _EPS)

        if level == LEVEL_EXACT and value is not None:
            # Term-frequency adjustment: for an exact agreement, the chance of
            # a coincidental match IS this value's frequency. A name shared by
            # 20% of rows carries far less weight than a unique one.
            freq = self.term_frequency.get(value)
            if freq is not None and freq > 0:
                u = max(freq, _EPS)

        return math.log2(m / u)


def build_token_document_frequencies(
    records: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Token document frequencies per field: ``({field: {token: df}}, {field: N})``.

    P14: ``build_term_frequencies`` is IDF at the WHOLE-VALUE grain, so a
    token shared by most names (a legal-form word, say) is never down-weighted.
    Here a document is one record's value of the field and the corpus is every
    record given (both origins together); a token's ``df`` is how many values
    contain it. Under Fellegi-Sunter's own assumptions the per-token log-odds
    increment is ``-log2(df/N)`` [C03] -- measured, not estimated. The
    value-level table stays beside this one for atomic fields, where value
    grain IS token grain.

    ponytail: the table is a per-run artifact (Part 1); persisting a global,
    per-run-snapshotted table (report D-23) is Part 3 storage.
    """
    df: dict[str, Counter] = {}
    n: dict[str, int] = {}
    for rec in records:
        for key, value in rec.items():
            if key.startswith("_") or value is None:
                continue
            tokens = set(_TOKEN_RE.findall(str(value).lower()))
            if not tokens:
                continue
            df.setdefault(key, Counter()).update(tokens)
            n[key] = n.get(key, 0) + 1
    return {k: dict(c) for k, c in df.items()}, n


def idf_soft_similarity(
    tokens1: set[str],
    tokens2: set[str],
    df: dict[str, int],
    n: int,
    token_sim: Callable[[str, str], float],
) -> float | None:
    """IDF-weighted soft token similarity of two names, in [0, 1].

    Each token scores the best ``token_sim`` it finds on the other side
    (character-level, so "jonh" still earns most of "john"), and the scores
    are averaged with IDF weights ``log2(N/df)``; the two directions are then
    averaged (a symmetric Monge-Elkan) -- unless one token set contains the
    other, where the weaker direction is the score: averaging gave X vs
    "Addendum to X" a free full match one way and halved the rare extra
    token's cost (D5). A token shared by most names carries
    almost no weight, so "Acme X Ltd" vs "Beta X Ltd" no longer looks alike
    because of "X" and "Ltd" -- the inversion the report names (P14).

    ``None`` when either side carries no information (every token occurs in
    every document), so the caller falls back rather than reading "no
    evidence" as "disagree". A token missing from the table counts as seen
    once.
    ponytail: report D-14 asks for TF-IDF; plain token cosine was measured to
    turn every typo into DISAGREE (person recall 0.85 -> 0.26), so the token
    match is soft. No SoftTFIDF theta: the caller's own character similarity
    is used as is. Deferred: a continuous sum(-log2(df/N)) text weight.
    ponytail: D5 ceiling -- a superset with ONE rare extra token still lands
    in the weak level; if such near-duplicates are common among the
    candidates, EM learns that level (and the year off by one) as match
    evidence. Upgrade path: score calibration against labels (D12).
    """
    if n <= 0 or not tokens1 or not tokens2:
        return None

    def w(t: str) -> float:
        return math.log2(n / min(max(df.get(t, 1), 1), n))

    def directed(src: set[str], dst: set[str]) -> float | None:
        total = math.fsum(w(t) for t in src)
        if total == 0:
            return None
        return math.fsum(w(t) * max(token_sim(t, u) for u in dst) for t in src) / total

    d1, d2 = directed(tokens1, tokens2), directed(tokens2, tokens1)
    if d1 is None or d2 is None:
        return None
    if tokens1 <= tokens2 or tokens2 <= tokens1:
        # D5: one name wholly inside the other scores a free 1.0 one way, so
        # only the longer name's side says whether the extra words matter.
        return min(d1, d2)
    return (d1 + d2) / 2


class MatchModel:
    """Per-field m/u probabilities plus the log-odds scoring rule."""

    def __init__(self, fields: dict[str, FieldModel], match_prior: float):
        self.fields = fields
        self.match_prior = min(max(match_prior, 1e-4), 0.9)
        # P14: ({field: {token: df}}, {field: N}) the comparison vectors were
        # built with; scoring must build its vectors with the same table.
        self.token_df: tuple[dict[str, dict[str, int]], dict[str, int]] | None = None

    @property
    def prior_weight(self) -> float:
        """Starting evidence from the base rate of matches among candidates."""
        return math.log2(self.match_prior / (1.0 - self.match_prior))

    def score(self, comparisons: dict[str, tuple[str, str | None]]) -> float:
        """Total match weight in bits for one pair's comparison vector."""
        total = self.prior_weight
        for name, (level, value) in comparisons.items():
            model = self.fields.get(name)
            if model is not None:
                total += model.weight(level, value)
        return total

    def probability(self, comparisons: dict[str, tuple[str, str | None]]) -> float:
        """Match weight expressed as a probability in [0, 1]."""
        return _sigmoid_bits(self.score(comparisons))

    def explain(self, comparisons: dict[str, tuple[str, str | None]]) -> list[str]:
        """Per-field contributions, largest first — for the review gate."""
        rows: list[tuple[float, str]] = []
        for name, (level, value) in comparisons.items():
            model = self.fields.get(name)
            if model is None or level == LEVEL_NULL:
                continue
            w = model.weight(level, value)
            rows.append((w, f"{name}: {level} ({w:+.2f} bits)"))
        rows.sort(key=lambda r: -abs(r[0]))
        return [text for _w, text in rows]


def _sigmoid_bits(weight: float) -> float:
    """Convert a log2-odds match weight to a probability."""
    if weight > 60:
        return 1.0
    if weight < -60:
        return 0.0
    odds = 2.0**weight
    return odds / (1.0 + odds)


def build_term_frequencies(records: list[dict[str, Any]], normalise) -> dict[str, dict[str, float]]:
    """Value frequencies per field across the whole record set.

    This is what makes a match on a common value count for less. Computed
    once per entity type from every record, not per pair.
    """
    counters: dict[str, Counter] = {}
    totals: dict[str, int] = {}
    for rec in records:
        for key, value in rec.items():
            if key.startswith("_") or value is None:
                continue
            norm = normalise(value)
            if not norm:
                continue
            counters.setdefault(key, Counter())[norm] += 1
            totals[key] = totals.get(key, 0) + 1

    frequencies: dict[str, dict[str, float]] = {}
    for key, counter in counters.items():
        total = max(totals.get(key, 1), 1)
        # A value seen once in n records has frequency 1/n — the rarest
        # possible evidence this dataset can offer.
        frequencies[key] = {v: c / total for v, c in counter.items()}
    return frequencies


def sample_random_pairs(n: int, k: int, seed: int) -> list[tuple[int, int]]:
    """Up to ``k`` distinct record-index pairs ``(i, j)``, ``i < j``, drawn
    uniformly from the full cross product of ``n`` records — NOT from blocks.

    Seeded and deterministic: the same ``(n, k, seed)`` gives the same pairs.
    Returns every pair when ``k`` covers the whole cross product.
    """
    total = n * (n - 1) // 2
    if total <= 0 or k <= 0:
        return []
    rng = random.Random(seed)
    if k * 2 >= total:  # dense: enumerating is cheaper than rejection
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        return pairs if k >= total else rng.sample(pairs, k)
    seen: set[tuple[int, int]] = set()
    drawn: list[tuple[int, int]] = []
    while len(drawn) < k:  # k < total/2, so rejections are rare
        i, j = sorted(rng.sample(range(n), 2))
        if (i, j) not in seen:
            seen.add((i, j))
            drawn.append((i, j))
    return drawn


# P13: there are two ``u`` estimation paths and they coexist ON PURPOSE. Do not
# unify them -- unifying is what reintroduces the bug. (1) Field-level
# agreement: ``u`` comes from the comparison vectors of RANDOMLY SAMPLED record
# pairs (the unblocked cross product), because EM over the blocked candidates
# sees only look-alikes and returns a ``u`` far too high. (2) Token/value-level
# exact agreement: ``FieldModel.weight`` uses ``df/N`` from
# ``build_term_frequencies`` directly -- measured, no EM and no sampling. ``m``
# stays EM on the blocked candidates and is fitted BEFORE ``u`` is replaced:
# pinning ``u`` inside the EM would make blocked non-matches that agree on the
# blocking field look like matches and dilute ``m``.
# ponytail: ceiling -- a rare value rarely agrees in a random sample, so the
# estimate floors (prior pseudo-count, never 0). Deferred: calibrating the score
# (Platt/isotonic, Belin & Rubin 1997); trigger: the review band's volume
# justifies it (report P13, section 2.17).
# Prior consistency (lead decision, deviates from the report's literal wording):
# sampled ``u`` is the random-pair population's, so ``match_prior`` must be that
# population's too -- the share of RANDOM pairs that match, EM match mass over
# the candidates / population_pairs. Pairing sampled ``u`` with the blocked-
# candidate prior counts the blocking agreement twice and over-scores
# non-matches, so ``sampled_vectors`` without ``population_pairs`` is refused.
def _sampled_u(
    sampled_vectors: list[dict[str, tuple[str, str | None]]],
) -> dict[str, dict[str, float]]:
    """Per-field level probabilities among sampled random pairs.

    Prior pseudo-counts (as in the EM M-step) floor an unseen level above zero.
    A field with no non-null sampled comparison is absent: it has no evidence.
    """
    counts: dict[str, dict[str, float]] = {}
    for vec in sampled_vectors:
        for name, (level, _value) in vec.items():
            if level == LEVEL_NULL:
                continue
            row = counts.setdefault(name, {lvl: _PRIOR_U[lvl] * _PRIOR_WEIGHT for lvl in _LEVELS})
            row[level] += 1.0
    return {
        name: {lvl: c / sum(row.values()) for lvl, c in row.items()} for name, row in counts.items()
    }


def estimate_model(
    comparison_vectors: list[dict[str, tuple[str, str | None]]],
    term_frequencies: dict[str, dict[str, float]],
    unique_id_fields: set[str] | None = None,
    iterations: int = 8,
    sampled_vectors: list[dict[str, tuple[str, str | None]]] | None = None,
    population_pairs: int | None = None,
) -> MatchModel:
    """Estimate m/u per field from unlabelled candidate pairs, via EM.

    Starts from the priors, repeatedly (E) scores every pair with the current
    model and (M) re-estimates each level's m/u weighted by those scores.
    Priors are mixed in throughout so a handful of pairs can't produce
    absurd probabilities.

    ``sampled_vectors`` (optional): comparison vectors of randomly sampled
    record pairs (see ``sample_random_pairs``). When given, each field's ``u``
    is taken from them instead of from the blocked candidates; ``m`` is the
    unchanged EM result, and ``match_prior`` becomes the random-pair match rate
    (EM match mass / ``population_pairs``, the size C(n, 2) of the unblocked
    cross product), so ``u`` and the prior describe the same population.
    ``sampled_vectors`` without ``population_pairs`` raises ``ValueError``.
    Both omitted: output is exactly as before.
    """
    if sampled_vectors and not population_pairs:
        raise ValueError(
            "sampled_vectors needs population_pairs: u and match_prior must share a population"
        )
    unique_id_fields = unique_id_fields or set()
    names = {n for vec in comparison_vectors for n in vec}
    if sampled_vectors:
        names |= {n for vec in sampled_vectors for n in vec}
    fields = {
        name: FieldModel(
            name=name,
            term_frequency=term_frequencies.get(name, {}),
            is_unique_id=name in unique_id_fields,
        )
        for name in names
    }
    model = MatchModel(fields, _PRIOR_MATCH_RATE)

    if not comparison_vectors:
        return _pin_sampled_u(model, comparison_vectors, sampled_vectors, population_pairs)

    for _ in range(iterations):
        # E-step: responsibility of the "match" class for each pair.
        responsibilities = [model.probability(vec) for vec in comparison_vectors]
        match_mass = sum(responsibilities)
        non_match_mass = len(responsibilities) - match_mass

        # M-step: re-estimate each level's probabilities.
        #
        # Part 1.5 A.7: this used to be `for field: for pair: vec.get(field,
        # NULL)` — re-scanning every comparison vector once per DISTINCT
        # FIELD NAME across the whole run, regardless of whether that vector
        # even carries the field. `_project_to_canonical` (Bug C0a) carries
        # unrecognised raw fields through verbatim, so a wide entity type
        # accumulates far more distinct field names than a narrow one, and
        # this loop is O(iterations * fields * pairs) — the field-count
        # factor is pure waste, since a vector missing a field contributes
        # nothing (`continue`s immediately). Measured live: this was 94% of
        # resolve_entities' wall time for a wide entity type (`experience`,
        # 907 of 966s) vs. 77% for a narrow one (`technology`, 112 of 146s).
        # A single pass that only visits each vector's own (present) keys
        # does the identical accumulation in O(iterations * sum of
        # per-vector field counts) — the field-count factor drops out.
        m_counts_by_field = {
            name: {lvl: _PRIOR_M[lvl] * _PRIOR_WEIGHT for lvl in _LEVELS} for name in fields
        }
        u_counts_by_field = {
            name: {lvl: _PRIOR_U[lvl] * _PRIOR_WEIGHT for lvl in _LEVELS} for name in fields
        }
        for vec, resp in zip(comparison_vectors, responsibilities, strict=True):
            for name, (level, _value) in vec.items():
                if level == LEVEL_NULL:
                    continue  # carries no evidence, so it trains nothing
                m_counts_by_field[name][level] += resp
                u_counts_by_field[name][level] += 1.0 - resp

        for name, fmodel in fields.items():
            m_counts = m_counts_by_field[name]
            u_counts = u_counts_by_field[name]
            m_total = sum(m_counts.values()) or 1.0
            u_total = sum(u_counts.values()) or 1.0
            fmodel.m = {lvl: m_counts[lvl] / m_total for lvl in _LEVELS}
            fmodel.u = {lvl: u_counts[lvl] / u_total for lvl in _LEVELS}

        prior = (match_mass + _PRIOR_MATCH_RATE * _PRIOR_WEIGHT) / (
            match_mass + non_match_mass + _PRIOR_WEIGHT
        )
        model = MatchModel(fields, prior)

    logger.debug(
        "Estimated match model over %d pairs: match_prior=%.3f, fields=%s",
        len(comparison_vectors),
        model.match_prior,
        sorted(fields),
    )
    return _pin_sampled_u(model, comparison_vectors, sampled_vectors, population_pairs)


def _pin_sampled_u(
    model: MatchModel,
    comparison_vectors: list[dict[str, tuple[str, str | None]]],
    sampled_vectors: list[dict[str, tuple[str, str | None]]] | None,
    population_pairs: int | None,
) -> MatchModel:
    """Swap in the random-pair ``u`` and the random-pair match prior together."""
    if not (sampled_vectors and population_pairs):
        return model
    # Mass is read BEFORE u is replaced, so it is the EM's own match count.
    mass = sum(model.probability(vec) for vec in comparison_vectors)
    for name, u in _sampled_u(sampled_vectors).items():
        model.fields[name].u = u
    # At least one match in the population keeps the prior above 0; matches lie
    # inside the candidates, so a random pair cannot match more often than a
    # candidate does. Set directly: the constructor's clamp is for candidate-
    # scale rates and would inflate a prior of order 1/C(n, 2).
    model.match_prior = min(max(mass, 1.0) / population_pairs, model.match_prior)
    return model
