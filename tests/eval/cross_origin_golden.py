"""Cross-origin candidate pairs for Boeing, Airbus, and Lockheed (Task B4.0).

Unlike Pagila (synthetic corruptions on single-origin staff records), these pairs represent
real cross-origin data across origin1 and origin2, including divergent field names,
domain/ticker identity keys, surrogate keys, and relational evidence.
"""

from __future__ import annotations

from typing import Any

Pair = tuple[dict[str, Any], dict[str, Any], bool, str]
"""(record_a, record_b, is_same_entity, why_this_pair_is_interesting)"""


def build_cross_origin_golden_pairs() -> list[Pair]:
    """Return the labelled cross-origin company and person pairs."""
    pairs: list[Pair] = []

    # ── 1. Boeing Companies (True Match) ───────────────────────────
    boeing_a = {
        "_origin_id": "origin1",
        "_origin_table": "Company",
        "_origin_id_row": "orig1_boeing_01",
        "company_name": "The Boeing Company",
        "website": "https://www.boeing.com",
        "ticker": "BA",
        "legal_name": "The Boeing Company",
        "country": "United States",
    }
    boeing_b = {
        "_origin_id": "origin2",
        "_origin_table": "company_profile",
        "_origin_id_row": "orig2_boeing_99",
        "name": "Boeing Corp",
        "homepage": "www.boeing.com",
        "stock_symbol": "BA",
        "registration_number": "US-DEL-1002938",
        "country_code": "US",
    }
    pairs.append(
        (boeing_a, boeing_b, True, "Boeing company cross-origin match (exact domain & ticker)")
    )

    # ── 2. Boeing Persons (True Match) ────────────────────────────
    boeing_exec_a = {
        "_origin_id": "origin1",
        "_origin_table": "Person",
        "_origin_id_row": "orig1_pers_calhoun",
        "full_name": "Dave Calhoun",
        "email": "dave.calhoun@boeing.com",
        "company_name": "The Boeing Company",
        "title": "Chief Executive Officer",
    }
    boeing_exec_b = {
        "_origin_id": "origin2",
        "_origin_table": "contacts",
        "_origin_id_row": "orig2_cont_calhoun",
        "first_name": "David",
        "last_name": "Calhoun",
        "email": "d.calhoun@boeing.com",
        "org_name": "Boeing Corp",
        "role": "CEO",
    }
    pairs.append(
        (
            boeing_exec_a,
            boeing_exec_b,
            True,
            "Boeing CEO cross-origin match (Dave vs David Calhoun, matching employer domain)",
        )
    )

    # ── 3. Airbus Companies (True Match) ───────────────────────────
    airbus_a = {
        "_origin_id": "origin1",
        "_origin_table": "Company",
        "_origin_id_row": "orig1_airbus_01",
        "company_name": "Airbus SE",
        "website": "https://www.airbus.com",
        "ticker": "AIR",
        "country": "France",
    }
    airbus_b = {
        "_origin_id": "origin2",
        "_origin_table": "company_profile",
        "_origin_id_row": "orig2_airbus_88",
        "name": "Airbus Group",
        "homepage": "airbus.com",
        "stock_symbol": "AIR",
        "registration_number": "FR-99201",
    }
    pairs.append(
        (airbus_a, airbus_b, True, "Airbus company cross-origin match (Airbus SE vs Airbus Group)")
    )

    # ── 4. Airbus Persons (True Match) ────────────────────────────
    airbus_exec_a = {
        "_origin_id": "origin1",
        "_origin_table": "Person",
        "_origin_id_row": "orig1_pers_faury",
        "full_name": "Guillaume Faury",
        "email": "guillaume.faury@airbus.com",
        "company_name": "Airbus SE",
    }
    airbus_exec_b = {
        "_origin_id": "origin2",
        "_origin_table": "contacts",
        "_origin_id_row": "orig2_cont_faury",
        "first_name": "G.",
        "last_name": "Faury",
        "email": "gfaury@airbus.com",
        "org_name": "Airbus Group",
    }
    pairs.append(
        (
            airbus_exec_a,
            airbus_exec_b,
            True,
            "Airbus CEO cross-origin match (Guillaume vs G. Faury)",
        )
    )

    # ── 5. Lockheed Companies (True Match) ────────────────────────
    lockheed_a = {
        "_origin_id": "origin1",
        "_origin_table": "Company",
        "_origin_id_row": "orig1_lockheed_01",
        "company_name": "Lockheed Martin Corporation",
        "website": "https://www.lockheedmartin.com",
        "ticker": "LMT",
    }
    lockheed_b = {
        "_origin_id": "origin2",
        "_origin_table": "company_profile",
        "_origin_id_row": "orig2_lockheed_77",
        "name": "Lockheed Martin Corp",
        "homepage": "lockheedmartin.com",
        "stock_symbol": "LMT",
    }
    pairs.append((lockheed_a, lockheed_b, True, "Lockheed company cross-origin match"))

    # ── 6. Cross-Company Non-Matches ──────────────────────────────
    pairs.append((boeing_a, airbus_b, False, "Different aerospace companies (Boeing vs Airbus)"))
    pairs.append((boeing_a, lockheed_b, False, "Different defense companies (Boeing vs Lockheed)"))
    pairs.append((airbus_a, lockheed_b, False, "Different defense companies (Airbus vs Lockheed)"))

    # ── 7. Cross-Person Non-Matches ───────────────────────────────
    pairs.append(
        (boeing_exec_a, airbus_exec_b, False, "Different executives (Boeing CEO vs Airbus CEO)")
    )

    # ── 8. Surrogate Key Trap (Non-Match) ──────────────────────────
    surrogate_a = {
        "_origin_id": "origin1",
        "_origin_table": "Company",
        "_origin_id_row": "1001",
        "company_name": "Boeing Direct Services",
        "companyID": "COMP-1001",
    }
    surrogate_b = {
        "_origin_id": "origin2",
        "_origin_table": "company_profile",
        "_origin_id_row": "5002",
        "name": "Airbus Avionics Subcontractor",
        "companyID": "COMP-1001",  # Same local surrogate key, different entities!
    }
    pairs.append(
        (surrogate_a, surrogate_b, False, "Surrogate key collision across different origins")
    )

    return pairs
