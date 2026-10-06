"""Label-free cluster quality signals (report P20, reused by P16).

``name_variation_rate``: how much the display names of a set of records
differ from one another -- the coherence signal P20's cluster split reads and
P16's quality report prints. Data in, number out: no wordlist, no threshold.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from itertools import combinations

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
