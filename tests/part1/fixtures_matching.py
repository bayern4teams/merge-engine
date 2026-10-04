"""Synthetic matching fixtures with PLANTED truth (Part 1, Phase 0.6).

Each fixture reproduces a failure SHAPE from TECHNICAL_REPORT_2026-09-26.md with
neutral invented names. Every random choice is seeded. Records carry the
engine's own id fields (``_origin_id`` doubles as the ``source``), so
``rid(rec)`` equals the engine's ``_record_id``.
"""

from __future__ import annotations

import random


def rid(rec: dict) -> str:
    return f"{rec['_origin_id']}:{rec['_origin_table']}:{rec['_origin_id_row']}"


def source_of(records: list[dict]) -> dict[str, str]:
    return {rid(r): r["_origin_id"] for r in records}


def _rec(source: str, row: str, **fields: str) -> dict:
    return {"_origin_id": source, "_origin_table": "t", "_origin_id_row": row, **fields}


def _tokens(rng: random.Random, n: int) -> list[str]:
    """n distinct pronounceable tokens; consonant set has no 's' or 'c'."""
    out: set[str] = set()
    while len(out) < n:
        out.add("".join(rng.choice("bdfgklmnprtvz") + rng.choice("aeiou") for _ in range(3)))
    return sorted(out)


# ── P2: one block larger than the old _MAX_COMPARISONS ────────────────────────
def p2_big_block(n_records: int = 800, n_dupes: int = 40, seed: int = 20260926) -> dict:
    """All names share the first token ``widget``, so one block holds every record
    (800 records = 319,600 pairs > 250,000). ``n_dupes`` planted duplicate pairs
    differ by one dropped letter, so they do NOT share a small exact-value block;
    only the giant block proposes them. Order is shuffled, so they are spread."""
    rng = random.Random(seed)
    toks = _tokens(rng, n_records - n_dupes)
    taken = set(toks)  # a typo must never equal another record's real name
    entries: list[tuple[str, str | None]] = []  # (name, dupe_group)
    for i, t in enumerate(toks):
        if i < n_dupes:
            entries.append((f"widget {t}", f"g{i}"))
            typo = next(v for v in (t[:4] + t[5:], t[:3] + t[4:], t[:2] + t[3:]) if v not in taken)
            taken.add(typo)
            entries.append((f"widget {typo}", f"g{i}"))  # drop one letter
        else:
            entries.append((f"widget {t}", None))
    rng.shuffle(entries)
    records, groups = [], {}
    for i, (name, g) in enumerate(entries):
        # half the planted pairs end up cross-source, half within, by row parity
        rec = _rec("src_a" if i % 2 == 0 else "src_b", str(i), name=name)
        records.append(rec)
        if g:
            groups.setdefault(g, []).append(rid(rec))
    return {"records": records, "truth": sorted(groups.values())}


# ── P6: three-way company set ─────────────────────────────────────────────────
def p6_cases() -> dict:
    """Each case is two records. expected = the report's three-way outcome.
    Match -> auto-merge; Possible Match -> review band; Possibly Related -> not
    merged and not sent to a human (typed edge)."""
    return {
        "a_same_email": {
            "truth": "Match",
            "expected": "auto-merged",
            "records": [
                _rec("src_a", "1", name="Orchid Works Ltd", email="info@orchidworks.example"),
                _rec("src_b", "1", name="ORCHID WORKS LTD.", email="info@orchidworks.example"),
            ],
        },
        "b_same_address_diff_tax_id": {
            "truth": "Possible Match",
            "expected": "review band",
            "records": [
                _rec(
                    "src_a",
                    "2",
                    name="Vesper Trading Ltd",
                    address="14 Mill Lane, Thornby",
                    tax_id="TX-1001",
                ),
                _rec(
                    "src_b",
                    "2",
                    name="Vesper Enterprises Inc",
                    address="14 Mill Lane, Thornby",
                    tax_id="TX-2002",
                ),
            ],
        },
        "c_same_address_phone_diff_company": {
            "truth": "Possibly Related",
            "expected": "excluded",
            "records": [
                _rec(
                    "src_a",
                    "3",
                    name="Kestrel Supply Ltd",
                    address="9 Quarry Road, Elmford",
                    phone="555-0142",
                ),
                _rec(
                    "src_b",
                    "3",
                    name="Quill Enterprises Inc",
                    address="9 Quarry Road, Elmford",
                    phone="555-0142",
                ),
            ],
        },
        # same as c, but the names co-block, so the pair is actually scored
        "c2_same_address_phone_cooblocked": {
            "truth": "Possibly Related",
            "expected": "excluded",
            "records": [
                _rec(
                    "src_a",
                    "4",
                    name="Kestrel Supply Ltd",
                    address="9 Quarry Road, Elmford",
                    phone="555-0142",
                ),
                _rec(
                    "src_b",
                    "4",
                    name="Kestrel Enterprises Inc",
                    address="9 Quarry Road, Elmford",
                    phone="555-0142",
                ),
            ],
        },
    }


def p6_fillers(n: int = 10) -> list[dict]:
    """Unrelated companies sharing one name token, so a combined run has >= 30
    candidate pairs and the probabilistic model actually fits. The first 4 are
    duplicated in the other source (same values), so the model sees real matches."""
    rng = random.Random(7)
    toks = _tokens(rng, n)
    base = [
        _rec(
            "src_a" if i % 2 else "src_b",
            f"f{i}",
            name=f"Nimbus {t} Ltd",
            email=f"{t}@{t}.example",
            address=f"{i + 20} {t} Street",
            tax_id=f"TX-9{i:03d}",
            phone=f"555-09{i:02d}",
        )
        for i, t in enumerate(toks)
    ]
    dupes = [
        _rec(
            "src_b" if r["_origin_id"] == "src_a" else "src_a",
            f"d{i}",
            **{k: v for k, v in r.items() if not k.startswith("_")},
        )
        for i, r in enumerate(base[:4])
    ]
    return base + dupes


# ── P20: giant-component bridge ───────────────────────────────────────────────
def _members(prefix: str, n: int) -> list[dict]:
    return [
        _rec("src_a" if i % 2 == 0 else "src_b", f"{prefix}{i}", name=f"{prefix}{i}")
        for i in range(n)
    ]


def _clique(ids: list[str], w: float) -> list[tuple[str, str, float]]:
    return [(a, b, w) for i, a in enumerate(ids) for b in ids[i + 1 :]]


def p20_cases() -> dict:
    """Scored pairs are handed straight to _build_clusters. Auto-match is 0.95,
    so every 'bridge' edge below is above auto-match."""
    A, B = _members("a", 5), _members("b", 5)
    a, b = [rid(r) for r in A], [rid(r) for r in B]
    truth = [a, b]
    cases = {
        # report shape: one weak-but-above-threshold edge between two strong clusters
        "bridge_weak": {
            "records": A + B,
            "truth": truth,
            "pairs": _clique(a, 0.98) + _clique(b, 0.98) + [(a[0], b[0], 0.96)],
        },
        # internal edges only just clear auto-match; the wrong edge is the strongest
        "bridge_stronger_than_internal": {
            "records": A + B,
            "truth": truth,
            "pairs": _clique(a, 0.96) + _clique(b, 0.96) + [(a[0], b[0], 0.99)],
        },
    }
    X = _members("x", 1)
    x = rid(X[0])
    cases["chain_via_singleton"] = {  # a0 - x - b0 chain through a third, separate entity
        "records": A + B + X,
        "truth": truth + [[x]],
        "pairs": _clique(a, 0.98) + _clique(b, 0.98) + [(a[0], x, 0.96), (x, b[0], 0.96)],
    }
    # NOT a report shape: a true cluster seen only as a chain (blocking never proposed
    # the non-adjacent pairs). Probes whether missing edges are punished as non-matches.
    cases["sparse_true_cluster_chain"] = {
        "records": A,
        "truth": [a],
        "pairs": [(a[i], a[i + 1], 0.97) for i in range(4)],
    }
    return cases
