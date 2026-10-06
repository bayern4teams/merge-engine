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
from bisect import bisect_right
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
# and never splits one. A cluster is flagged only when its names fall into two or
# more groups that share no token and no substring AND the groups' representatives
# differ by more than the run-derived threshold. Accepted ceilings: siblings that
# share a family token (Tata Steel / Tata Motors), or fused companies that share a
# legal-form word, stay unflagged; a lone fused cluster with no other named cluster
# has no reference and is not flagged; one anchor group is compared with the others
# (linear), not every pair. The previous clustering is a JSON file keyed by run,
# with no concurrency control (two live runs on one path: last writer wins).
# Deferred alternative: the persisted time series with drift alerting (report
# 3.13; trigger: Part 3). A cheap name rule is the flag; correlation clustering
# (report P20 Deferred) replaces it only if the user's local run shows flagged
# clusters are real wrong merges.


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


def _clustering(value: Any) -> list[list[str]] | None:
    """``value`` if it is a list of lists of strings (a persisted clustering)."""
    ok = isinstance(value, list) and all(
        isinstance(c, list) and all(isinstance(r, str) for r in c) for c in value
    )
    return value if ok else None


def _load_history(path: Path) -> dict[str, Any]:
    """The whole history file; missing, corrupt or foreign reads as empty."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning("quality history %s unreadable, B-cubed unavailable: %s", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _baseline(
    stored: Any, run_id: str, current: list[list[str]]
) -> tuple[list[list[str]] | None, dict[str, Any] | None]:
    """(the clustering to compare against, the entry to persist or None).

    ``stored`` is ``{"run_id", "clusters", "previous": {"run_id", "clusters"} | None}``.
    The same run asked again (the gate preview, then the load report) compares
    against the stored ``previous`` and only refreshes ``clusters``; a new run
    compares against the stored ``clusters`` and shifts them into ``previous``. An
    empty clustering is never saved (an empty run must not wipe the baseline), and
    an empty ``run_id`` is no identity: every call is a new run.
    """
    entry = stored if isinstance(stored, dict) else {}
    clusters = _clustering(entry.get("clusters"))
    prev = entry.get("previous")
    keep = any(current)
    if clusters is not None and run_id and entry.get("run_id") == run_id:
        before = _clustering(prev.get("clusters")) if isinstance(prev, dict) else None
        return before, ({**entry, "clusters": current} if keep else None)
    shifted = None if clusters is None else {"run_id": entry.get("run_id"), "clusters": clusters}
    return clusters, (
        {"run_id": run_id, "clusters": current, "previous": shifted} if keep else None
    )


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


def _sample(names: list[str | None]) -> list[str | None]:
    """The names in a fixed order, thinned at a stride that keeps all-pairs work
    linear in their number (stride 1, i.e. all of them, while there are few)."""
    ordered = sorted(names, key=comparable_name)
    return ordered[:: max(math.isqrt(len(ordered)) // 2, 1)]


def _name_threshold(reps: list[str]) -> float:
    """Half the variation between different clusters (the mean dissimilarity of
    one name per cluster), read off this run: two groups of names in one cluster
    that are as unrelated as that are not variants of one entity."""
    return name_variation_rate(_sample([*reps])) / 2


def _name_groups(usable: list[str]) -> list[list[str]]:
    """Group sorted distinct comparable names that share a significant token or
    where one is contained in another (covers unspaced scripts and tickers); union-find, near-linear."""
    parent = list(range(len(usable)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    holders: dict[str, list[int]] = {}
    for i, name in enumerate(usable):
        for token in name.split():
            holders.setdefault(token, []).append(i)
    for token, idx in holders.items():
        # a token links only when it is at least half of one of the names holding it
        # (so a shared "ltd" or "group" does not tie two otherwise unrelated names)
        if any(2 * len(token) >= len(usable[i]) for i in idx):
            for i in idx:
                parent[find(i)] = find(idx[0])
    joined = "\n".join(usable)
    starts = [0]
    for name in usable:
        starts.append(starts[-1] + len(name) + 1)
    for i, name in enumerate(usable):  # names hold no newline: no match spans two names
        pos = joined.find(name)
        while pos >= 0:
            parent[find(i)] = find(bisect_right(starts, pos) - 1)
            pos = joined.find(name, pos + 1)
    out: dict[int, list[str]] = {}
    for i, name in enumerate(usable):
        out.setdefault(find(i), []).append(name)
    return list(out.values())


def _incoherent(names: list[str | None], threshold: float) -> bool:
    """Do the cluster's names fall into two or more unrelated groups, one of
    which differs from the largest by more than ``threshold``? (Stops at the first.)"""
    groups = _name_groups(sorted({comparable_name(n) for n in names} - {""}))
    if len(groups) < 2:
        return False
    anchor = min(groups, key=lambda g: (-len(g), g[0]))[0]
    return any(name_variation_rate([anchor, g[0]]) > threshold for g in groups if g[0] != anchor)


def quality_signals(
    clusters: list[dict[str, Any]],
    names: Mapping[str, str | None],
    history_path: str | Path | None = None,
    entity: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """The label-free quality block of one entity's clustering (report P16).

    ``clusters``: dicts with ``cluster_id`` and ``record_ids`` (singletons
    included); ``names``: record id -> display name. With ``history_path`` the
    previous run's clustering for ``entity`` is read for B-cubed and this run's
    is written for the next (``_baseline`` says how ``run_id`` keys it); without
    one (or on run one) B-cubed is None with a "not available" note, never a number.

    Name coherence is a flag: a multi-record cluster is flagged when its names
    split into unrelated groups (``_incoherent``) further apart than
    ``name_variation_threshold``, derived from this run's own names. Fewer than
    two named clusters give nothing to compare against, so nothing is flagged.
    """
    groups = [(c.get("cluster_id"), [str(r) for r in c["record_ids"]]) for c in clusters]
    named = {str(k): v for k, v in names.items()}
    cluster_names = [[named.get(r) for r in ids] for _, ids in groups]
    multi = [
        (cid, ns) for (cid, ids), ns in zip(groups, cluster_names, strict=True) if len(ids) > 1
    ]
    rates = [name_variation_rate(_sample(ns)) for _, ns in multi]
    reps = [
        min(u)
        for u in ([comparable_name(n) for n in ns if comparable_name(n)] for ns in cluster_names)
        if u
    ]
    threshold = _name_threshold(reps)
    flagged = [cid for cid, ns in multi if len(reps) > 1 and _incoherent(ns, threshold)]

    previous: list[list[str]] | None = None
    note: str | None = "B-cubed not available: no history file configured"
    current = [ids for _, ids in groups]
    if history_path is not None:
        path = Path(history_path)
        history = _load_history(path)
        previous, entry = _baseline(history.get(entity), run_id, current)
        if entry is not None:
            _save(path, {**history, entity: entry})
        note = "B-cubed not available: no previous run for this entity"
    score = bcubed(previous, current) if previous is not None else None
    if score is not None:
        note = None
    elif previous is not None:
        note = "B-cubed not available: no records in common with the previous run"
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
