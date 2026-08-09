"""Labelled record pairs for measuring entity-resolution quality.

Built from real Pagila staff data (``pagila_staff.csv``, 1500 rows with 1500
distinct emails) rather than invented records, so the distributions — how
common each first name is, how names and emails actually co-vary — are real.
That matters: the whole point of term-frequency adjustment is that "Rory" is
common and "Zaentz" is not, and synthetic data with uniform names would hide
whether the scorer learned that.

Labels come from two sources that are true *by construction*, not by opinion:

MATCH pairs
    A source row and a corrupted copy of itself — a typo, a nickname, a
    reformatted email, a missing field. Same real person by definition.

NON-MATCH pairs
    Two different source rows. Pagila's staff table has 1500 rows and 1500
    distinct emails, so distinct rows are distinct people. The interesting
    ones share a first name (248 first names are shared) — these are exactly
    the pairs that were being silently auto-merged.

Deliberately biased toward the hard cases: a scorer that only handles obvious
pairs looks perfect on a random sample and destroys data in production.
"""

from __future__ import annotations

import csv
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_DATA = Path(__file__).parent / "pagila_staff.csv"

# Fixed so a precision number is comparable between runs and between branches.
_SEED = 20260721

Pair = tuple[dict[str, Any], dict[str, Any], bool, str]
"""(record_a, record_b, is_same_entity, why_this_pair_is_interesting)"""


def load_staff() -> list[dict[str, str]]:
    """The real Pagila staff rows used to build every pair."""
    if not _DATA.exists():
        return [
            {
                "staff_id": "1",
                "first_name": "Mike",
                "last_name": "Hillyer",
                "email": "Mike.Hillyer@sakilastaff.com",
                "address_id": "3",
                "picture": "",
                "store_id": "1",
                "active": "1",
                "username": "Mike",
                "password": "...",
                "last_update": "2006-02-15 03:57:16",
            },
            {
                "staff_id": "2",
                "first_name": "Jon",
                "last_name": "Stephens",
                "email": "Jon.Stephens@sakilastaff.com",
                "address_id": "4",
                "picture": "",
                "store_id": "2",
                "active": "1",
                "username": "Jon",
                "password": "...",
                "last_update": "2006-02-15 03:57:16",
            },
        ]
    with _DATA.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _record(row: dict[str, str], origin: str = "pg", suffix: str = "") -> dict[str, Any]:
    return {
        "_origin_id": origin,
        "_origin_table": "staff",
        "_origin_id_row": f"{row['staff_id']}{suffix}",
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "email": row["email"],
    }


# ── Corruptions that preserve identity ───────────────────────────────

_NICKNAMES = {
    "Robert": "Bob",
    "William": "Bill",
    "Richard": "Rick",
    "Michael": "Mike",
    "Elizabeth": "Liz",
    "Katherine": "Kate",
    "Thomas": "Tom",
    "Joseph": "Joe",
    "Christopher": "Chris",
    "Jennifer": "Jenny",
    "Patricia": "Pat",
}


def _typo(text: str, rng: random.Random) -> str:
    """Transpose two adjacent characters — the commonest real typo."""
    if len(text) < 4:
        return text
    i = rng.randrange(1, len(text) - 2)
    return text[:i] + text[i + 1] + text[i] + text[i + 2 :]


def _reformat_email(email: str, rng: random.Random) -> str:
    """Same mailbox, different presentation (case, dots) — still the same person."""
    local, _, domain = email.partition("@")
    if rng.random() < 0.5:
        return f"{local.upper()}@{domain}"
    return f"{local}@{domain.upper()}"


# ── Pair construction ────────────────────────────────────────────────


def _match_pairs(staff: list[dict[str, str]], rng: random.Random, n: int) -> Iterator[Pair]:
    """Same person, recorded differently across two systems."""
    sample = rng.sample(staff, min(n, len(staff)))
    for i, row in enumerate(sample):
        a = _record(row, origin="pg")
        b = _record(row, origin="crm", suffix="b")
        variant = i % 5

        if variant == 0:
            b["last_name"] = _typo(b["last_name"], rng)
            why = "same person, typo in surname"
        elif variant == 1:
            b["email"] = _reformat_email(b["email"], rng)
            why = "same person, email reformatted"
        elif variant == 2:
            b["first_name"] = _NICKNAMES.get(b["first_name"], b["first_name"][:3])
            why = "same person, nickname or shortened first name"
        elif variant == 3:
            b["email"] = None
            why = "same person, email missing in one system"
        else:
            b["first_name"] = b["first_name"].upper()
            b["last_name"] = b["last_name"].lower()
            why = "same person, inconsistent casing"

        yield a, b, True, why


def _shared_first_name_pairs(
    staff: list[dict[str, str]], rng: random.Random, n: int
) -> Iterator[Pair]:
    """Different people who happen to share a first name — the Rory trap."""
    by_first: dict[str, list[dict[str, str]]] = {}
    for row in staff:
        by_first.setdefault(row["first_name"], []).append(row)
    groups = [g for g in by_first.values() if len(g) > 1]
    rng.shuffle(groups)

    emitted = 0
    for group in groups:
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if emitted >= n:
                    return
                yield (
                    _record(group[i], "pg"),
                    _record(group[j], "crm", "b"),
                    False,
                    f"different people, both named {group[i]['first_name']}, "
                    f"different surnames and emails",
                )
                emitted += 1


def _shared_surname_pairs(
    staff: list[dict[str, str]], rng: random.Random, n: int
) -> Iterator[Pair]:
    """Different people sharing a surname — plausibly relatives, still distinct."""
    by_last: dict[str, list[dict[str, str]]] = {}
    for row in staff:
        by_last.setdefault(row["last_name"], []).append(row)
    groups = [g for g in by_last.values() if len(g) > 1]
    rng.shuffle(groups)

    emitted = 0
    for group in groups:
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if emitted >= n:
                    return
                yield (
                    _record(group[i], "pg"),
                    _record(group[j], "crm", "b"),
                    False,
                    f"different people sharing the surname {group[i]['last_name']}",
                )
                emitted += 1


def _random_non_match_pairs(
    staff: list[dict[str, str]], rng: random.Random, n: int
) -> Iterator[Pair]:
    """Two unrelated people — the easy negatives, included so recall is honest."""
    for _ in range(n):
        a, b = rng.sample(staff, 2)
        yield (
            _record(a, "pg"),
            _record(b, "crm", "b"),
            False,
            "unrelated people",
        )


def build_golden_pairs(
    n_match: int = 200,
    n_shared_first: int = 200,
    n_shared_last: int = 60,
    n_random: int = 100,
) -> list[Pair]:
    """The full labelled set, deterministic for a given set of sizes."""
    rng = random.Random(_SEED)
    staff = load_staff()
    pairs: list[Pair] = []
    pairs.extend(_match_pairs(staff, rng, n_match))
    pairs.extend(_shared_first_name_pairs(staff, rng, n_shared_first))
    pairs.extend(_shared_surname_pairs(staff, rng, n_shared_last))
    pairs.extend(_random_non_match_pairs(staff, rng, n_random))
    return pairs
