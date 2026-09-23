"""Unit tests for the Great Expectations format-level checks (ADR 0008,
fix-v3 B.1.4). Skipped entirely when great_expectations isn't installed —
the degradation path itself is covered separately, without importorskip,
by mocking the import to fail.
"""

from __future__ import annotations

import pytest

pytest.importorskip("great_expectations", reason="dq optional extra not installed")

from src.mcp_server.tools.data_quality import _run_format_expectations, run_data_quality_checks


class TestRunFormatExpectations:
    def test_bad_email_and_unparseable_date_both_fail(self):
        sample = [
            {"email": "a@b.com", "signup_date": "2020-01-01"},
            {"email": "not-an-email", "signup_date": "not-a-date"},
        ]
        entries, skipped = _run_format_expectations(sample, ["email", "signup_date"])
        assert skipped is False
        by_check = {e["check"]: e for e in entries}
        assert any(k.startswith("gx_format:email") for k in by_check)
        assert any(k.startswith("gx_format:signup_date") for k in by_check)
        email_entry = next(e for k, e in by_check.items() if k.startswith("gx_format:email"))
        date_entry = next(e for k, e in by_check.items() if k.startswith("gx_format:signup_date"))
        assert email_entry["severity"] == "fail"
        assert email_entry["success"] is False
        assert date_entry["severity"] == "fail"
        assert date_entry["success"] is False

    def test_clean_sample_passes(self):
        sample = [
            {"email": "a@b.com", "signup_date": "2020-01-01"},
            {"email": "c@d.com", "signup_date": "2021-05-05"},
        ]
        entries, skipped = _run_format_expectations(sample, ["email", "signup_date"])
        assert skipped is False
        assert entries
        assert all(e["success"] for e in entries)
        assert all(e["severity"] == "fail" for e in entries)

    def test_no_email_or_date_shaped_columns_returns_no_entries(self):
        sample = [{"id": "1", "widgets": 3}]
        entries, skipped = _run_format_expectations(sample, ["id", "widgets"])
        assert entries == []
        assert skipped is False

    def test_the_historic_revenue_bug_is_caught(self):
        # §7's exact failure mode: a numeric-looking column ("amount")
        # holding a human-formatted string GX exists to catch. Uses a
        # `*_date` style bad row too, so this stays end-to-end through
        # run_data_quality_checks' fold-in, not just the helper.
        sample = {
            "CompanyFinancials": [
                {"id": "f1", "revenue": "100000", "reported_date": "2020-01-01"},
                {"id": "f2", "revenue": "$1 Million", "reported_date": "2021-06-01"},
            ]
        }
        res = run_data_quality_checks(sample, row_counts={"CompanyFinancials": 2})
        report = res["quality_report"]
        checks = report["entities"]["CompanyFinancials"]["checks"]
        revenue_checks = [c for c in checks if c["check"].startswith("gx_format:revenue")]
        assert revenue_checks, f"no gx_format:revenue check in {checks!r}"
        assert revenue_checks[0]["severity"] == "fail"
        assert revenue_checks[0]["success"] is False


class TestRunDataQualityChecksFoldsInGx:
    def test_gx_failure_is_folded_into_entity_severity(self):
        sampled = {"Company": [{"id": "c1", "name": "Boeing", "email": "not-an-email"}]}
        res = run_data_quality_checks(sampled, row_counts={"Company": 1})
        report = res["quality_report"]
        assert report["entities"]["Company"]["severity"] == "fail"
        assert res["has_quality_failure"] is True
        checks = report["entities"]["Company"]["checks"]
        assert any(c["check"].startswith("gx_format:email") for c in checks)

    def test_no_gx_available_degrades_with_flag_not_silently(self, monkeypatch):
        import sys

        import src.mcp_server.tools.data_quality as dq

        # Simulate GX not being importable, regardless of what's actually
        # installed in this environment.
        monkeypatch.setitem(sys.modules, "great_expectations", None)
        entries, skipped = dq._run_format_expectations([{"email": "a@b.com"}], ["email"])
        assert skipped is True
        assert entries == []

        sampled = {"Company": [{"id": "c1", "email": "a@b.com"}]}
        res = dq.run_data_quality_checks(sampled, row_counts={"Company": 1})
        assert res["quality_report"]["gx_checks_skipped"] is True
