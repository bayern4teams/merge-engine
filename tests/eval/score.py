"""Measure entity-resolution quality against the labelled pairs.

Run it directly to print a report:

    python -m tests.eval.score

Reports precision/recall/F1 for the *auto-merge* decision — the one that
matters, because an auto-merge happens with no human review and destroys the
losing record. Pairs the scorer routes to review are counted separately: they
are not errors, they are the system correctly saying "I'm not sure".
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tests.eval.golden import Pair, build_golden_pairs


@dataclass
class Report:
    """Outcome counts for one scoring configuration."""

    name: str
    true_positive: int = 0  # same entity, auto-merged — correct
    false_positive: int = 0  # DIFFERENT entities auto-merged — data destroyed
    true_negative: int = 0  # different entities, not merged — correct
    false_negative: int = 0  # same entity, auto-excluded — a missed match
    review_same: int = 0  # same entity, sent to review — acceptable
    review_diff: int = 0  # different entities, sent to review — acceptable
    worst_false_positives: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        merged = self.true_positive + self.false_positive
        return self.true_positive / merged if merged else 1.0

    @property
    def recall(self) -> float:
        """Of all genuine matches, how many were auto-merged."""
        total_same = self.true_positive + self.false_negative + self.review_same
        return self.true_positive / total_same if total_same else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def review_rate(self) -> float:
        total = (
            self.true_positive
            + self.false_positive
            + self.true_negative
            + self.false_negative
            + self.review_same
            + self.review_diff
        )
        return (self.review_same + self.review_diff) / total if total else 0.0

    def render(self) -> str:
        lines = [
            f"== {self.name} " + "=" * max(0, 56 - len(self.name)),
            f"  precision (auto-merges that were right) : {self.precision:.4f}",
            f"  recall    (real matches auto-merged)    : {self.recall:.4f}",
            f"  F1                                      : {self.f1:.4f}",
            f"  sent to human/LLM review                : {self.review_rate:.1%}",
            "",
            f"  correct auto-merges      TP = {self.true_positive}",
            f"  WRONG auto-merges        FP = {self.false_positive}   "
            f"<-- each one destroys a record",
            f"  correct rejections       TN = {self.true_negative}",
            f"  missed matches           FN = {self.false_negative}",
            f"  routed to review         {self.review_same} same / {self.review_diff} different",
        ]
        if self.worst_false_positives:
            lines.append("")
            lines.append("  examples of wrong auto-merges:")
            for example in self.worst_false_positives[:5]:
                lines.append(f"    - {example}")
        return "\n".join(lines)


def evaluate(
    scorer: Callable[[dict[str, Any], dict[str, Any]], float],
    pairs: list[Pair] | None = None,
    auto_match: float = 0.95,
    auto_exclude: float = 0.70,
    name: str = "scorer",
) -> Report:
    """Score every labelled pair and tally the outcomes."""
    pairs = pairs if pairs is not None else build_golden_pairs()
    report = Report(name=name)

    for rec_a, rec_b, is_same, why in pairs:
        confidence = scorer(rec_a, rec_b)
        merged = confidence >= auto_match
        excluded = confidence <= auto_exclude

        if merged and is_same:
            report.true_positive += 1
        elif merged and not is_same:
            report.false_positive += 1
            report.worst_false_positives.append(
                f"{_describe(rec_a)} + {_describe(rec_b)} (conf {confidence:.3f}) - {why}"
            )
        elif excluded and is_same:
            report.false_negative += 1
        elif excluded and not is_same:
            report.true_negative += 1
        elif is_same:
            report.review_same += 1
        else:
            report.review_diff += 1

    return report


def _describe(rec: dict[str, Any]) -> str:
    name = " ".join(str(rec.get(k) or "") for k in ("first_name", "last_name")).strip()
    return f"{name} <{rec.get('email') or 'no email'}>"


@dataclass
class ClusterReport:
    """End-to-end clustering outcome on a set of known-distinct records.

    Pair-level precision can look perfect while clustering still destroys
    data: a pair routed to "review" is not merged *by the scorer*, but if
    clustering unions it anyway the record is gone just the same. This
    measures the thing the user actually receives.
    """

    name: str
    records: int = 0
    clusters: int = 0
    expected_clusters: int = 0
    largest_cluster: int = 0
    merged_away: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        return self.clusters == self.expected_clusters

    @property
    def over_merged(self) -> int:
        """Records lost beyond what the expected clustering accounts for."""
        return max(0, self.expected_clusters - self.clusters)

    def render(self) -> str:
        verdict = (
            "OK"
            if self.correct
            else (
                f"OVER-MERGED by {self.over_merged}"
                if self.clusters < self.expected_clusters
                else f"UNDER-MERGED by {self.clusters - self.expected_clusters}"
            )
        )
        lines = [
            f"== {self.name} " + "=" * max(0, 56 - len(self.name)),
            f"  input records                : {self.records}",
            f"  clusters produced            : {self.clusters}",
            f"  clusters expected            : {self.expected_clusters}   [{verdict}]",
            f"  records folded into others   : {self.merged_away}",
            f"  largest cluster              : {self.largest_cluster} records",
        ]
        if self.examples:
            lines.append("")
            label = (
                "  examples of multi-record clusters:"
                if self.correct
                else "  examples of WRONGLY merged clusters:"
            )
            lines.append(label)
            for e in self.examples[:5]:
                lines.append(f"    - {e}")
        return "\n".join(lines)


def evaluate_clustering(
    records: list[dict[str, Any]],
    expected_clusters: int,
    name: str = "clustering",
) -> ClusterReport:
    """Cluster known-distinct records and report how many were merged away."""
    from src.mcp_server.tools.resolve_entities import resolve_entities_impl

    result = asyncio.run(resolve_entities_impl("person", records, llm_config=None))
    clusters = result["clusters"]
    by_id = {f"{r['_origin_id']}:{r['_origin_table']}:{r['_origin_id_row']}": r for r in records}

    report = ClusterReport(
        name=name,
        records=len(records),
        clusters=len(clusters),
        expected_clusters=expected_clusters,
        merged_away=len(records) - len(clusters),
    )
    multi = sorted(
        (c for c in clusters if len(c["record_ids"]) > 1),
        key=lambda c: -len(c["record_ids"]),
    )
    if multi:
        report.largest_cluster = len(multi[0]["record_ids"])
    for cluster in multi[:5]:
        names = [_describe(by_id[rid]) for rid in cluster["record_ids"][:4] if rid in by_id]
        report.examples.append(f"{len(cluster['record_ids'])} records merged: " + " | ".join(names))
    return report


def main() -> int:
    from src.mcp_server.tools.resolve_entities import _compute_heuristic_confidence
    from tests.eval.golden import _record, load_staff

    pairs = build_golden_pairs()
    same = sum(1 for _, _, s, _ in pairs if s)
    print(
        f"\nGolden set: {len(pairs)} labelled pairs "
        f"({same} same entity, {len(pairs) - same} different)\n"
    )

    print(evaluate(_compute_heuristic_confidence, pairs, name="pairwise: heuristic only").render())
    print()

    # The probabilistic model needs to see the whole record set (for term
    # frequencies) and the candidate pairs (to estimate m/u), so fit it the
    # same way the pipeline does before scoring.
    from src.mcp_server.tools.resolve_entities import _fit_match_model, _score_pair

    records = [r for pair in pairs for r in (pair[0], pair[1])]
    candidates = [("a", "b", a, b) for a, b, _s, _w in pairs]
    # _fit_match_model now returns (model, vectors) — Part 1.5 A.2 reuses
    # the vectors it builds internally in the pipeline's own scoring loop;
    # this harness only needs the model.
    model, _vectors = _fit_match_model(records, candidates)
    print(
        evaluate(
            lambda a, b: _score_pair(a, b, model),
            pairs,
            name="pairwise: Fellegi-Sunter + term frequency",
        ).render()
    )
    print()

    # Every Pagila staff row is a distinct person (1500 rows, 1500 distinct
    # emails), so correct clustering leaves all of them intact.
    staff = load_staff()[:400]
    records = [_record(row) for row in staff]
    print(
        evaluate_clustering(
            records,
            expected_clusters=len(records),
            name="clustering, 400 distinct real people (must stay 400)",
        ).render()
    )
    print()

    # The opposite failure: refusing to merge anything is also broken. Each
    # person appears in two systems, so 200 records must collapse to 100.
    dupes: list[dict[str, Any]] = []
    for row in staff[:100]:
        dupes.append(_record(row, origin="pg"))
        variant = _record(row, origin="crm", suffix="b")
        variant["first_name"] = variant["first_name"].upper()
        dupes.append(variant)
    print(
        evaluate_clustering(
            dupes,
            expected_clusters=100,
            name="clustering, 100 people duplicated across 2 systems (must be 100)",
        ).render()
    )
    print()

    print(measure_blocking_recall(dupes))
    print()

    # The Pagila actor case: no email, no unique identifier at all, plus a
    # last_update stamp that is IDENTICAL across every row. Records like
    # this agree on a first name and on a field that identifies nobody —
    # which merged 25 pairs of different actors in a live run.
    thin = []
    for i, row in enumerate(staff[:300]):
        thin.append(
            {
                "_origin_id": "pg",
                "_origin_table": "actor",
                "_origin_id_row": str(i),
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "last_update": "2022-02-15 09:34:33+00",  # same for every row
            }
        )
    print(
        evaluate_clustering(
            thin,
            expected_clusters=len(thin),
            name="clustering, 300 people with NO unique id (must stay 300)",
        ).render()
    )
    print()
    return 0


def measure_blocking_recall(records: list[dict[str, Any]]) -> str:
    """Do the blocking rules actually surface the pairs that matter?

    Blocking is a recall filter: any true pair it fails to generate can never
    be scored, no matter how good the scorer is. Measured against the true
    pairs (two copies of the same person) rather than against all-pairs,
    since that is what blocking must not miss.
    """
    from src.mcp_server.tools.resolve_entities import (
        _generate_candidate_pairs,
        _record_id,
    )

    truth: set[tuple[str, str]] = set()
    by_source: dict[str, list[str]] = {}
    for rec in records:
        # Records built by main() share a staff_id-derived row id, with the
        # duplicate carrying a "b" suffix.
        base = str(rec["_origin_id_row"]).rstrip("b")
        by_source.setdefault(base, []).append(_record_id(rec))
    for ids in by_source.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                truth.add((a, b) if a < b else (b, a))

    generated = {
        (i1, i2) if i1 < i2 else (i2, i1) for i1, i2, _r1, _r2 in _generate_candidate_pairs(records)
    }
    found = len(truth & generated)
    total_possible = len(records) * (len(records) - 1) // 2
    recall = found / len(truth) if truth else 0.0

    return "\n".join(
        [
            "== blocking recall " + "=" * 39,
            f"  true pairs that exist        : {len(truth)}",
            f"  true pairs blocking surfaced : {found}",
            f"  blocking recall              : {recall:.4f}"
            f"   {'[OK]' if recall == 1.0 else '[MISSES TRUE PAIRS]'}",
            f"  candidate pairs generated    : {len(generated)} "
            f"of {total_possible} possible "
            f"({len(generated) / total_possible:.1%} of all-pairs)",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
