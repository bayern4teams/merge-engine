"""V3 profiling engine (Part B, P-3).

Two kinds of metric, deliberately computed two different ways:

- Scale-sensitive ones (null/distinct counts) are computed **database-side**
  via each connector's ``profile_column()`` (P-2) -- ``uniqExact``/``uniq``
  on ClickHouse, ``COUNT(DISTINCT ...)`` on PostgreSQL. Computing these in
  Python would mean pulling every row of a table that might hold tens of
  millions of them, which is exactly the anti-pattern P-2/P-3 exist to
  avoid.
- Per-value shape metrics (length distribution, top values, pattern
  distribution, type distribution) are computed **in Python over a bounded
  sample** pulled via ``connector.extract()`` -- a raw, non-deduplicated
  sample, since top-values needs frequency and ``sample_distinct_values``
  (Phase 4.1) already de-duplicates before this code ever sees a row.
  Classifying a few thousand strings' shape in Python is not the bottleneck
  a full-table scan would be; running it through the database instead would
  buy nothing here.

Every stored profile records ``mode`` (FULL/SAMPLED/APPROXIMATE) — PreSplink
§46's requirement that a caller never has to guess whether a number is
exact.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger("merge-engine.tools.profiling")

# Coarse shape buckets, checked in this order (first match wins) -- a value
# that's all digits is also "alphanumeric" under a looser definition, so
# order matters: the MOST SPECIFIC bucket a value satisfies is the one it's
# counted under.
_PATTERN_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "uuid",
        re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.I),
    ),
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("date_iso", re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}:\d{2})?")),
    ("numeric", re.compile(r"^-?\d+(\.\d+)?$")),
    ("alpha", re.compile(r"^[A-Za-z\s]+$")),
    ("alphanumeric", re.compile(r"^[A-Za-z0-9]+$")),
]


def _classify_pattern(value: str) -> str:
    for name, pattern in _PATTERN_RULES:
        if pattern.match(value):
            return name
    return "other"


def _shape_metrics(values: list[Any], top_n: int) -> dict[str, Any]:
    """Length distribution, top values, and pattern distribution over a
    bounded, already-fetched sample -- pure Python, no further queries."""
    non_null = [v for v in values if v is not None]
    string_values = [str(v) for v in non_null]

    length_buckets: Counter[str] = Counter()
    for s in string_values:
        n = len(s)
        if n == 0:
            bucket = "0"
        elif n <= 10:
            bucket = "1-10"
        elif n <= 50:
            bucket = "11-50"
        elif n <= 255:
            bucket = "51-255"
        else:
            bucket = "256+"
        length_buckets[bucket] += 1

    value_counts = Counter(string_values)
    top_values = [{"value": v, "count": c} for v, c in value_counts.most_common(top_n)]

    pattern_counts: Counter[str] = Counter()
    for s in string_values:
        pattern_counts[_classify_pattern(s)] += 1

    type_counts: Counter[str] = Counter(type(v).__name__ for v in non_null)

    return {
        "length_distribution": dict(length_buckets),
        "top_values": top_values,
        "pattern_distribution": dict(pattern_counts),
        "type_distribution": dict(type_counts),
    }


async def profile_column_full(
    connector: Any,
    table_name: str,
    column: str,
    *,
    mode: str = "SAMPLED",
    sample_size: int = 10_000,
    top_n: int = 10,
) -> dict[str, Any]:
    """The complete P-3 profile for one column: P-2's database-native
    aggregates plus Python-computed shape metrics over a bounded raw
    sample.

    ``mode`` governs both halves consistently: FULL/APPROXIMATE are passed
    straight through to ``connector.profile_column()``; the raw sample for
    shape metrics is always bounded by ``sample_size`` regardless of mode
    (even a FULL null-count pass does not justify pulling the whole table
    just to look at value shapes).
    """
    base = await connector.profile_column(table_name, column, mode=mode, sample_size=sample_size)

    extracted = await connector.extract(table_name, [column], batch_size=sample_size)
    raw_values = [row.get(column) for row in extracted.get("rows", [])]
    shape = _shape_metrics(raw_values, top_n)

    total = base.get("total_count", 0) or 0
    distinct = base.get("distinct_count", 0) or 0
    return {
        **base,
        "distinct_ratio": (distinct / total) if total else 0.0,
        "unique_ratio": (distinct / total) if total else 0.0,
        **shape,
    }


async def profile_and_store(
    store: Any,
    connector: Any,
    column_id: str,
    table_name: str,
    column: str,
    *,
    mode: str = "SAMPLED",
    sample_size: int = 10_000,
    top_n: int = 10,
    run_id: str | None = None,
) -> str:
    """Compute a full profile and persist it via ``MetadataStore.insert_profile``.

    The glue between P-2's connectors, this module's Python-side shape
    metrics, and P-1's store -- what P-4 (semantic type inference) and P-5
    (candidate keys) read back via ``get_latest_profile`` instead of
    re-profiling.
    """
    profile = await profile_column_full(
        connector, table_name, column, mode=mode, sample_size=sample_size, top_n=top_n
    )
    logger.debug(
        "Profiled %s.%s: mode=%s, null_ratio=%.3f, distinct_count=%d",
        table_name,
        column,
        profile["mode"],
        profile.get("null_ratio", 0.0),
        profile.get("distinct_count", 0),
    )
    profile_id: str = store.insert_profile(
        column_id,
        profile["mode"],
        run_id=run_id,
        null_count=profile.get("null_count"),
        null_ratio=profile.get("null_ratio"),
        distinct_count=profile.get("distinct_count"),
        distinct_ratio=profile.get("distinct_ratio"),
        unique_ratio=profile.get("unique_ratio"),
        min_value=profile.get("min_value"),
        max_value=profile.get("max_value"),
        length_distribution=profile.get("length_distribution"),
        top_values=profile.get("top_values"),
        pattern_distribution=profile.get("pattern_distribution"),
        type_distribution=profile.get("type_distribution"),
    )
    return profile_id
