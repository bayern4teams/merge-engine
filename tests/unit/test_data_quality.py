from src.mcp_server.tools.data_quality import run_data_quality_checks


def test_data_quality_clean_data():
    extracted_data = {
        "Company": [
            {"id": "c1", "name": "Boeing", "founding_year": 1916},
            {"id": "c2", "name": "Airbus", "founding_year": 1970},
        ]
    }
    matching_rules = {"Company": {"unique_id_fields": ["id"]}}

    res = run_data_quality_checks(extracted_data, matching_rules)
    report = res["quality_report"]

    assert res["has_quality_failure"] is False
    assert report["severity"] == "pass"
    assert report["entities"]["Company"]["severity"] == "pass"
    assert report["entities"]["Company"]["row_count"] == 2


def test_data_quality_zero_rows_fail():
    extracted_data = {"Company": []}
    res = run_data_quality_checks(extracted_data)

    assert res["has_quality_failure"] is True
    report = res["quality_report"]
    assert report["severity"] == "fail"
    assert report["entities"]["Company"]["severity"] == "fail"
    checks = report["entities"]["Company"]["checks"]
    assert any(c["check"] == "row_count_ge_1" and c["success"] is False for c in checks)


def test_data_quality_duplicate_unique_id_fail():
    extracted_data = {
        "Company": [
            {"id": "dup_1", "name": "Boeing"},
            {"id": "dup_1", "name": "Boeing Corp"},
        ]
    }
    matching_rules = {"Company": {"unique_id_fields": ["id"]}}

    res = run_data_quality_checks(extracted_data, matching_rules)
    assert res["has_quality_failure"] is True
    report = res["quality_report"]
    assert report["severity"] == "fail"
    checks = report["entities"]["Company"]["checks"]
    assert any("uniqueness:id" in c["check"] and c["success"] is False for c in checks)


def test_data_quality_null_name_warn():
    extracted_data = {
        "Company": [
            {"id": "c1", "name": None},
            {"id": "c2", "name": "Airbus"},
        ]
    }
    matching_rules = {"Company": {"unique_id_fields": ["id"]}}

    res = run_data_quality_checks(extracted_data, matching_rules)
    assert res["has_quality_failure"] is False
    report = res["quality_report"]
    assert report["severity"] == "warn"
    checks = report["entities"]["Company"]["checks"]
    assert any("not_null:name" in c["check"] and c["success"] is False for c in checks)


def test_data_quality_bad_year_warn():
    extracted_data = {
        "Company": [
            {"id": "c1", "name": "Boeing", "founding_year": 1200},  # out of sane bounds 1700-present
        ]
    }
    res = run_data_quality_checks(extracted_data)
    assert res["has_quality_failure"] is False
    report = res["quality_report"]
    assert report["severity"] == "warn"


def test_data_quality_degrades_on_exception(monkeypatch):
    """Confirm GX / check exceptions degrade to 'validation unavailable' without crashing."""
    def _bad_check(*args, **kwargs):
        raise RuntimeError("Forced internal error")

    monkeypatch.setattr("src.mcp_server.tools.data_quality._detect_name_column", _bad_check)
    extracted_data = {"Company": [{"id": "c1", "name": "Boeing"}]}

    res = run_data_quality_checks(extracted_data)
    assert res["has_quality_failure"] is False
    assert res["quality_report"]["status"] == "validation unavailable"
    assert res["quality_report"]["severity"] == "warn"
