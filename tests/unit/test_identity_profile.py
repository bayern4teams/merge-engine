"""Unit tests for tools/identity_profile.py (Task B4.1)."""

from __future__ import annotations

from src.mcp_server.tools.identity_profile import (
    align_cross_origin_fields,
    classify_column_role,
    profile_table_columns,
)


def test_classify_column_role_identity_key() -> None:
    # High uniqueness + website hint -> identity_key
    role = classify_column_role(
        "website", uniqueness_ratio=1.0, cross_overlap_ratio=0.8, null_rate=0.0
    )
    assert role == "identity_key"

    role_ticker = classify_column_role(
        "ticker", uniqueness_ratio=0.95, cross_overlap_ratio=0.5, null_rate=0.1
    )
    assert role_ticker == "identity_key"


def test_classify_column_role_surrogate_key() -> None:
    # High uniqueness + 0.0 cross overlap -> surrogate_key
    role = classify_column_role(
        "companyID", uniqueness_ratio=1.0, cross_overlap_ratio=0.0, null_rate=0.0
    )
    assert role == "surrogate_key"


def test_profile_table_columns_real_data() -> None:
    origin1_rows = [
        {
            "company_name": "Boeing",
            "website": "https://www.boeing.com",
            "ticker": "BA",
            "companyID": "COMP-001",
        },
        {
            "company_name": "Airbus",
            "website": "https://www.airbus.com",
            "ticker": "AIR",
            "companyID": "COMP-002",
        },
        {
            "company_name": "Lockheed",
            "website": "https://www.lockheedmartin.com",
            "ticker": "LMT",
            "companyID": "COMP-003",
        },
    ]
    origin2_rows = [
        {
            "name": "Boeing Corp",
            "homepage": "www.boeing.com",
            "stock_symbol": "BA",
            "companyID": "COMP-999",
        },
        {
            "name": "Airbus Group",
            "homepage": "airbus.com",
            "stock_symbol": "AIR",
            "companyID": "COMP-888",
        },
    ]

    profiles = profile_table_columns(
        origin1_rows, origin_id="origin1", table_name="Company", cross_rows=origin2_rows
    )

    assert "website" in profiles
    assert profiles["website"]["role"] == "identity_key"
    assert profiles["companyID"]["role"] == "surrogate_key"
    assert profiles["website"]["uniqueness_ratio"] == 1.0
    assert profiles["website"]["cross_overlap_ratio"] > 0.0


def test_align_cross_origin_fields() -> None:
    profile_a = {
        "company_name": {"role": "strong_signal"},
        "email": {"role": "strong_signal"},
        "companyID": {"role": "surrogate_key"},
    }
    profile_b = {
        "name": {"role": "strong_signal"},
        "email": {"role": "strong_signal"},
        "companyID": {"role": "surrogate_key"},
    }

    alignment = align_cross_origin_fields(profile_a, profile_b)
    assert alignment.get("company_name") == "name"
    assert alignment.get("email") == "email"
    assert "companyID" not in alignment
