"""B.4 unit tests: the version gate and shared plumbing (S0 + common).

Covers the pure surface of ``splink_compat`` (the "verify the installed major,
refuse otherwise" gate that every B.4 phase runs through) and ``splink_common``
(records → DataFrame, labels table, unique-id column read-back) — no Linker, no
EM training, so this is the fast suite. The heavy linker/EM paths are covered
by the live end-to-end verification (see TODO.md B.4) and the existing
``test_splink_matcher.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_HAS_SPLINK = pytest.importorskip("splink") is not None  # type: ignore[func-ret-values]

from src.mcp_server.tools import (  # noqa: E402
    splink_common,
    splink_compat,
)

# ── splink_compat: the S0 version gate ────────────────────────────────────────


@pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
class TestVersionGate:
    def test_installed_is_available(self) -> None:
        assert splink_compat.splink_available() is True

    def test_installed_major_is_the_verified_one(self) -> None:
        minor = splink_compat.installed_minor_version()
        assert minor is not None
        assert minor[0] == splink_compat.REQUIRED_MAJOR

    def test_check_splink_passes_on_the_installed_major(self) -> None:
        # The installed 4.0.x must not raise — this is the gate's happy path.
        splink_compat.check_splink()

    def test_require_splink_reports_available(self) -> None:
        info = splink_compat.require_splink()
        assert info["available"] is True
        assert info["version"] == splink_compat.imported_version()
        assert info["required_major"] == splink_compat.REQUIRED_MAJOR

    def test_require_splink_degrades_when_absent(self, monkeypatch) -> None:
        # A missing optional dependency degrades (available: False), it does NOT raise.
        monkeypatch.setattr(splink_compat, "splink", None)
        monkeypatch.setattr(splink_compat, "_IMPORTED_VERSION", "unknown")
        assert splink_compat.splink_available() is False
        assert splink_compat.installed_minor_version() is None
        splink_compat.check_splink()  # absence is not a version mismatch
        info = splink_compat.require_splink()
        assert info["available"] is False

    def test_check_splink_refuses_a_wrong_major(self, monkeypatch) -> None:
        monkeypatch.setattr(splink_compat, "splink", object())
        monkeypatch.setattr(splink_compat, "installed_minor_version", lambda: (5, 0))
        with pytest.raises(splink_compat.SplinkVersionError) as exc:
            splink_compat.check_splink()
        assert exc.value.required_major == splink_compat.REQUIRED_MAJOR

    def test_version_error_names_both_versions(self) -> None:
        err = splink_compat.SplinkVersionError("5.0.0")
        assert "5.0.0" in str(err)
        assert "4" in str(err)  # required major is named


# ── splink_common: records → DataFrame / labels plumbing ──────────────────────


@pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
class TestRecordsToDataFrame:
    def test_adds_unique_id_and_drops_internal_keys(self) -> None:
        recs = [
            {
                "_origin_id": "pg",
                "_origin_table": "t",
                "_origin_id_row": "1",
                "name": "Ada",
                "age": 30,
            },
            {
                "_origin_id": "pg",
                "_origin_table": "t",
                "_origin_id_row": "2",
                "name": "Bob",
                "age": None,
            },
        ]
        df = splink_common.records_to_dataframe(recs)
        assert splink_common.DEFAULT_UNIQUE_ID_COLUMN in df.columns
        # underscore-prefixed lineage keys never reach a comparator
        assert "_origin_id" not in df.columns
        assert "_origin_table" not in df.columns
        assert "_origin_id_row" not in df.columns
        assert "name" in df.columns and "age" in df.columns
        # mixed types are stringified; a missing int becomes the float column's
        # NaN, which stringifies to "nan" (Splink comparators treat it as an
        # empty/null value at the level definitions).
        assert list(df["age"]) == ["30.0", "nan"]
        assert len(df) == 2

    def test_empty_records_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one record"):
            splink_common.records_to_dataframe([])

    def test_duplicate_unique_ids_raise(self) -> None:
        recs = [
            {"_origin_id": "pg", "_origin_table": "t", "_origin_id_row": "1", "name": "A"},
            {"_origin_id": "pg", "_origin_table": "t", "_origin_id_row": "1", "name": "A"},
        ]
        with pytest.raises(ValueError, match="duplicate unique id"):
            splink_common.records_to_dataframe(recs)

    def test_value_columns_restricts_carried_fields(self) -> None:
        recs = [
            {
                "_origin_id": "pg",
                "_origin_table": "t",
                "_origin_id_row": "1",
                "name": "Ada",
                "email": "a@x.com",
            }
        ]
        df = splink_common.records_to_dataframe(recs, value_columns=["name"])
        assert "name" in df.columns
        assert "email" not in df.columns
        assert df["name"].iloc[0] == "Ada"


@pytest.mark.skipif(not _HAS_SPLINK, reason="splink not installed")
class TestLabelsAndUid:
    def test_labels_dataframe_uses_the_40_schema(self) -> None:
        uid = "id"
        df = splink_common.labels_dataframe([("a", "b", 1.0), ("c", "d", 0)], unique_id_column=uid)
        assert list(df.columns) == [f"{uid}_l", f"{uid}_r", "clerical_match_score"]
        assert df[f"{uid}_l"].tolist() == ["a", "c"]
        assert df[f"{uid}_r"].tolist() == ["b", "d"]
        # 0 is coerced to 0.0 (float) — the column must be numeric
        assert all(isinstance(v, float) for v in df["clerical_match_score"])

    def test_labels_default_column_is_the_workbench_uid(self) -> None:
        df = splink_common.labels_dataframe([("a", "b", 1.0)])
        col = splink_common.DEFAULT_UNIQUE_ID_COLUMN
        assert f"{col}_l" in df.columns and f"{col}_r" in df.columns

    def test_unique_id_column_of_reads_the_linker_setting(self) -> None:
        linker = SimpleNamespace(
            _settings_obj=SimpleNamespace(as_dict=lambda: {"unique_id_column_name": "my_id"})
        )
        assert splink_common.unique_id_column_of(linker) == "my_id"

    def test_unique_id_column_of_falls_back_to_default(self) -> None:
        linker = SimpleNamespace(_settings_obj=SimpleNamespace(as_dict=lambda: {"other": 1}))
        assert splink_common.unique_id_column_of(linker) == splink_common.DEFAULT_UNIQUE_ID_COLUMN
