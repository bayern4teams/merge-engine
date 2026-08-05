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

Both m and u are estimated **unsupervised** from the candidate pairs
themselves (a simplified EM pass), with priors that keep a cold start sane.
No labelled training data is required.

Reference: Fellegi & Sunter (1969); the term-frequency treatment follows the
approach used by Splink.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

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


class MatchModel:
    """Per-field m/u probabilities plus the log-odds scoring rule."""

    def __init__(self, fields: dict[str, FieldModel], match_prior: float):
        self.fields = fields
        self.match_prior = min(max(match_prior, 1e-4), 0.9)

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


def estimate_model(
    comparison_vectors: list[dict[str, tuple[str, str | None]]],
    term_frequencies: dict[str, dict[str, float]],
    unique_id_fields: set[str] | None = None,
    iterations: int = 8,
) -> MatchModel:
    """Estimate m/u per field from unlabelled candidate pairs, via EM.

    Starts from the priors, repeatedly (E) scores every pair with the current
    model and (M) re-estimates each level's m/u weighted by those scores.
    Priors are mixed in throughout so a handful of pairs can't produce
    absurd probabilities.
    """
    unique_id_fields = unique_id_fields or set()
    names = {n for vec in comparison_vectors for n in vec}
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
        return model

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
    return model
