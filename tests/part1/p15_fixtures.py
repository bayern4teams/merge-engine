"""P15 fixtures: planted pairs whose FINAL score the test controls.

The band logic (what the report asks for) consumes a pair's final score; it must not depend on how
that score was produced.  So the tests plant the score of named pairs (a wrapper around
``_score_pair`` in the test file) and leave every other pair at its natural score.  Records carry the
engine's own id fields, so ``rid(rec)`` is the engine's record id.

Names are invented and share no token with ``fixtures_matching.p6_fillers`` ("Nimbus <tok> Ltd"), which
supply >= 30 candidate pairs so the fitted-model path engages.
"""

from __future__ import annotations

from tests.part1 import fixtures_matching as fx

rid = fx.rid

# key -> (name in src_a, name in src_b).  Each pair shares exactly one rare token, so blocking proposes it.
NAMES = {
    "P": ("Birch Holdings", "Birch Holdings Alpha"),  # the report's "~0.8" band pair
    "Q": ("Maple Trading", "Maple Trading Beta"),  # a second band pair
    "M": ("Orchid Works", "Orchid Works Limited"),  # clear Match
    "X": ("Cobalt Freight", "Cobalt Mining"),  # clear Exclude
    "R": ("Quartz Gear", "Quartz Gear Gamma"),  # 0.96: Match under the defaults
    "E": ("Argon Supply", "Argon Systems"),  # 0.6996: just under the band
    "B": ("Helix Parts", "Helix Components"),  # 0.70 exactly: the band's inclusive lower bound
    "C": ("Indigo Labs", "Indigo Laboratories"),  # 0.9496: just under Match
    "D": ("Jasper Metals", "Jasper Metals Delta"),  # 0.95 exactly: Match
}


def planted(keys):
    """(records, ids, names) for the planted pairs in ``keys``; ids[key] = (rid_a, rid_b)."""
    records, ids = [], {}
    for k in keys:
        a, b = NAMES[k]
        ra = fx._rec("src_a", f"{k}1", name=a)
        rb = fx._rec("src_b", f"{k}1", name=b)
        records += [ra, rb]
        ids[k] = (rid(ra), rid(rb))
    return records, ids


def with_fillers(records):
    """Add the shared-token fillers so the combined run has >= 30 candidate pairs (model fits)."""
    return records + fx.p6_fillers()


def many_pairs(n):
    """n pairs, each sharing one unique rare token and nothing else; (records, [(rid_a, rid_b)], names)."""
    toks = fx._tokens(__import__("random").Random(15), 2 * n)
    records, ids, names = [], [], {}
    for i in range(n):
        a, b = toks[i], f"{toks[i]} {toks[n + i]}"
        ra = fx._rec("src_a", f"m{i}", name=a)
        rb = fx._rec("src_b", f"m{i}", name=b)
        records += [ra, rb]
        ids.append((rid(ra), rid(rb)))
        names[frozenset({a, b})] = None
    return records, ids, names
