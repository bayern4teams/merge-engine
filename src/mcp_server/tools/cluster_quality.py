"""Label-free cluster quality signals (report P20, and P16's quality report).

``name_variation_rate``: how much the display names of a set of records
differ from one another -- the coherence signal P20's cluster split reads and
P16's quality report prints. Data in, number out: no wordlist, no threshold.

``quality_signals`` (P16): homonymy rate, name-variation rate, Hill numbers of
the cluster sizes, B-cubed against the previous run's persisted clustering, and
the clusters whose names are incoherent -- a FLAG, never a cut (user decision c).
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Mapping
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NOT_WORD = re.compile(r"[^\w\s]+")


def comparable_name(name: str | None) -> str:
    """Casefolded, accent-folded, punctuation-free, single-spaced. ``\\w`` keeps
    every script's letters, so a non-Latin name never normalises to ''. Only a
    mark on an ASCII letter is an accent; on kana (``ガ``) it is the letter."""
    if not name:
        return ""
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", str(name)):
        if unicodedata.combining(ch) and out and out[-1].isascii():
            continue
        out.append(ch)
    folded = unicodedata.normalize("NFC", "".join(out)).casefold()
    return " ".join(_NOT_WORD.sub(" ", folded).split())


def name_variation_rate(names: list[str | None]) -> float:
    """Mean pairwise dissimilarity (1 - difflib ratio) of the usable names, in
    [0, 1]: 0.0 when they are all the same or fewer than two are usable, near 1
    when no two share any run of characters. Order-free (each pair is compared
    in sorted order, so the ratio's asymmetry cannot leak in).

    ponytail: character-level similarity; an alias with no lexical overlap
    (an acronym and its expansion) reads as unrelated (report P20 Ceiling).
    """
    usable = [n for n in (comparable_name(x) for x in names or []) if n]
    if len(usable) < 2:
        return 0.0
    pairs = list(combinations(usable, 2))
    total = sum(1 - SequenceMatcher(None, min(a, b), max(a, b)).ratio() for a, b in pairs)
    return float(total / len(pairs))


# ── P16: label-free quality signals ──────────────────────────────────────────
#
# ponytail: signals, not measurements (report P16 Ceiling): a rise says merging
# behaviour changed, not that it is wrong; the flag marks clusters worth a look
# and never splits one. The previous clustering is a JSON file, with no
# concurrency control, so two live runs on one path are last-writer-wins.
# Deferred alternative: the persisted time series with drift alerting (report
# 3.13; trigger: Part 3). A pairwise name rule is the cheap flag; correlation
# clustering (report P20 Deferred) replaces it only if the user's local run
# shows flagged clusters are real wrong merges.


def hill_numbers(sizes: list[int]) -> dict[int, float]:
    """Hill numbers of order 0, 1, 2 of the cluster sizes (in records): the
    number of clusters, exp(Shannon entropy), inverse Simpson. They fall as
    one cluster swallows the rest; an empty cluster is not an abundance."""
    kept = [n for n in sizes if n > 0]
    total = sum(kept)
    if not total:
        return {0: 0.0, 1: 0.0, 2: 0.0}
    p = [n / total for n in kept]
    return {
        0: float(len(p)),
        1: math.exp(-sum(x * math.log(x) for x in p)),
        2: 1 / sum(x * x for x in p),
    }


def bcubed(previous: list[list[str]], current: list[list[str]]) -> float | None:
    """B-cubed F1 of ``current`` against ``previous`` over the records in both
    (None when there are none): needs no labels, only the earlier clustering.
    Membership is compared, never cluster ids (they are minted afresh each run)."""
    prev = {r: i for i, c in enumerate(previous) for r in c}
    cur = {r: i for i, c in enumerate(current) for r in c}
    common = prev.keys() & cur.keys()
    if not common:
        return None
    joint = Counter((prev[r], cur[r]) for r in common)
    prev_size = Counter(prev[r] for r in common)
    cur_size = Counter(cur[r] for r in common)
    precision = sum(n * n / cur_size[c] for (_, c), n in joint.items()) / len(common)
    recall = sum(n * n / prev_size[p] for (p, _), n in joint.items()) / len(common)
    return 2 * precision * recall / (precision + recall)


def homonymy_rate(clusters: list[dict[str, Any]], names: Mapping[str, str | None]) -> float:
    """Share of the distinct comparable names found in two or more clusters:
    one name, many entities' worth of clusters (the under-merge signature)."""
    seen: Counter[str] = Counter()
    for c in clusters:
        seen.update({n for n in (comparable_name(names.get(r)) for r in c["record_ids"]) if n})
    return sum(1 for k in seen.values() if k > 1) / len(seen) if seen else 0.0


def _load_previous(path: Path, entity: str) -> tuple[dict[str, Any], list[list[str]] | None]:
    """(the whole history file, this entity's previous clustering or None). A
    missing, corrupt or foreign file is "no previous run", never an error."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning("quality history %s unreadable, B-cubed unavailable: %s", path, exc)
        return {}, None
    if not isinstance(raw, dict):
        return {}, None
    prev = raw.get(entity)
    ok = isinstance(prev, list) and all(
        isinstance(c, list) and all(isinstance(r, str) for r in c) for c in prev
    )
    return raw, prev if ok else None


def _save(path: Path, history: dict[str, Any]) -> None:
    """Atomic write (temp file + rename), as the P9 decision memory does."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(history, fh, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError as exc:
        logger.warning("quality history %s not written, next run has no B-cubed: %s", path, exc)


def _name_threshold(reps: list[str]) -> float:
    """Half the variation between different clusters (the mean dissimilarity of
    one name per cluster), read off this run: two names in one cluster that are
    as unrelated as that are not variants of one entity. Names are sampled at a
    stride of isqrt(clusters), so the cost stays linear in the clusters."""
    sample: list[str | None] = sorted(reps)[:: max(math.isqrt(len(reps)), 1)]
    return name_variation_rate(sample) / 2


def _incoherent(names: list[str | None], threshold: float) -> bool:
    """Do two of the cluster's distinct names differ by more than ``threshold``?"""
    usable = sorted({comparable_name(n) for n in names} - {""})
    return any(name_variation_rate(list(pair)) > threshold for pair in combinations(usable, 2))


def quality_signals(
    clusters: list[dict[str, Any]],
    names: Mapping[str, str | None],
    history_path: str | Path | None = None,
    entity: str = "",
) -> dict[str, Any]:
    """The label-free quality block of one entity's clustering (report P16).

    ``clusters``: dicts with ``cluster_id`` and ``record_ids`` (singletons
    included); ``names``: record id -> display name. With ``history_path`` the
    previous run's clustering for ``entity`` is read for B-cubed and this run's
    is written for the next; without one (or on run one) B-cubed is None with
    a "not available" note, never a number.

    Name coherence is a flag: a multi-record cluster is flagged when its
    names include two further apart than ``name_variation_threshold``, derived
    from this run's own names (``_name_threshold``). Fewer than two named clusters give
    nothing to compare against, so nothing is flagged.
    """
    groups = [(c.get("cluster_id"), [str(r) for r in c["record_ids"]]) for c in clusters]
    named = {str(k): v for k, v in names.items()}
    cluster_names = [[named.get(r) for r in ids] for _, ids in groups]
    multi = [
        (cid, ns) for (cid, ids), ns in zip(groups, cluster_names, strict=True) if len(ids) > 1
    ]
    rates = [name_variation_rate(ns) for _, ns in multi]
    reps = [
        min(u)
        for u in ([comparable_name(n) for n in ns if comparable_name(n)] for ns in cluster_names)
        if u
    ]
    threshold = _name_threshold(reps)
    flagged = [cid for cid, ns in multi if len(reps) > 1 and _incoherent(ns, threshold)]

    previous: list[list[str]] | None = None
    note: str | None = "B-cubed not available: no history file configured"
    history: dict[str, Any] = {}
    current = [ids for _, ids in groups]
    if history_path is not None:
        history, previous = _load_previous(Path(history_path), entity)
        note = "B-cubed not available: no previous run for this entity"
    score = bcubed(previous, current) if previous is not None else None
    if score is not None:
        note = None
    elif previous is not None:
        note = "B-cubed not available: no records in common with the previous run"
    if history_path is not None:
        _save(Path(history_path), {**history, entity: current})
    return {
        "homonymy_rate": homonymy_rate([{"record_ids": ids} for _, ids in groups], named),
        "name_variation_rate": sum(rates) / len(rates) if rates else 0.0,
        "hill_numbers": hill_numbers([len(ids) for _, ids in groups]),
        "bcubed": score,
        "bcubed_note": note,
        "flagged_clusters": flagged,
        "flagged_count": len(flagged),
        "name_variation_threshold": float(threshold),
    }
