"""N.1: the per-run DuckDB spool that keeps rows out of graph state.

The property being protected is that state size stops tracking data size.
Every row used to sit in `MergeState.extracted_data` and be serialised into
a checkpoint on every step and every HITL pause, so a large merge could not
start — and a paused one could not be resumed.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

import src.orchestrator.spool as spool_mod
from src.orchestrator.utils.retry import DataQualityError
from src.orchestrator.spool import (
    RunSpool,
    cleanup_old_spools,
    entity_origin_counts,
    entity_records_by_id,
    entity_row_count,
    entity_rows,
    is_handle,
    make_handle,
)


@pytest.fixture(autouse=True)
def _isolated_spool_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    yield


def _row(i: int, origin: str = "pg", table: str = "staff") -> dict[str, Any]:
    return {
        "_origin_id": origin,
        "_origin_table": table,
        "_origin_id_row": str(i),
        "name": f"person {i}",
        "email": f"p{i}@example.com",
    }


class TestRoundTrip:
    def test_rows_come_back_in_extraction_order(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(i) for i in range(5)])

        got = spool.read_all("person")

        assert [r["_origin_id_row"] for r in got] == ["0", "1", "2", "3", "4"]

    def test_appends_accumulate_across_batches(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(0), _row(1)])
        spool.append("person", [_row(2)])

        assert spool.row_count("person") == 3
        assert [r["_origin_id_row"] for r in spool.read_all("person")] == ["0", "1", "2"]

    def test_values_survive_intact(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [{"a": 1, "b": None, "c": [1, 2], "d": {"e": "f"}}])

        assert spool.read_all("person")[0] == {
            "a": 1, "b": None, "c": [1, 2], "d": {"e": "f"},
        }

    def test_reset_clears_a_previous_attempt(self) -> None:
        """A retry must not append to what the failed attempt left behind."""
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(0), _row(1)])
        spool.reset_entity("person")
        spool.append("person", [_row(9)])

        assert spool.row_count("person") == 1

    def test_entities_are_independent(self) -> None:
        spool = RunSpool("run1")
        for entity in ("person", "company"):
            spool.reset_entity(entity)
        spool.append("person", [_row(0)])
        spool.append("company", [_row(0), _row(1)])

        assert spool.row_count("person") == 1
        assert spool.row_count("company") == 2
        assert sorted(spool.entities()) == ["company", "person"]

    def test_runs_are_independent(self) -> None:
        a, b = RunSpool("runA"), RunSpool("runB")
        a.reset_entity("person"); a.append("person", [_row(0)])
        b.reset_entity("person"); b.append("person", [_row(0), _row(1)])

        assert a.row_count("person") == 1
        assert b.row_count("person") == 2

    def test_missing_entity_reads_empty_rather_than_raising(self) -> None:
        assert RunSpool("nope").read_all("person") == []
        assert RunSpool("nope").row_count("person") == 0
        assert RunSpool("nope").entities() == []

    def test_streaming_yields_every_row_without_a_full_list(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(i) for i in range(250)])

        assert sum(1 for _ in spool.iter_rows("person", chunk=32)) == 250


class TestPartialTruncation:
    """The Phase M rule, enforced on the spool.

    A table that could not be read in full must not be merged as though it
    were, so the rows it did produce are removed rather than left looking
    complete.
    """

    def test_truncate_removes_only_the_failed_table_s_rows(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(i, table="staff") for i in range(3)])
        spool.append("person", [_row(i, table="actor") for i in range(4)])

        discarded = spool.truncate_after("person", keep=3)

        assert discarded == 4
        assert spool.row_count("person") == 3
        assert all(r["_origin_table"] == "staff" for r in spool.read_all("person"))

    def test_truncating_to_zero_empties_the_entity(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(i) for i in range(3)])

        assert spool.truncate_after("person", keep=0) == 3
        assert spool.read_all("person") == []

    def test_appending_after_truncation_continues_cleanly(self) -> None:
        """Sequence numbers must not collide with the discarded rows."""
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(i) for i in range(5)])
        spool.truncate_after("person", keep=2)
        spool.append("person", [_row(99)])

        got = spool.read_all("person")
        assert [r["_origin_id_row"] for r in got] == ["0", "1", "99"]


class TestTargetedReads:
    def test_read_by_ids_returns_only_what_was_asked_for(self) -> None:
        """Review gates show a handful of records out of possibly millions."""
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(i) for i in range(100)])

        found = spool.read_by_ids("person", ["pg:staff:7", "pg:staff:42"])

        assert set(found) == {"pg:staff:7", "pg:staff:42"}
        assert found["pg:staff:7"]["name"] == "person 7"

    def test_unknown_ids_are_simply_absent(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(0)])

        assert spool.read_by_ids("person", ["pg:staff:404"]) == {}

    def test_per_origin_counts_are_computed_in_sql(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(i, origin="pg") for i in range(3)])
        spool.append("person", [_row(i, origin="mongo") for i in range(2)])

        assert spool.per_origin_counts("person") == {"pg": 3, "mongo": 2}


class TestStateAccessors:
    """Consumers must work with a handle or an inline list, transparently."""

    def test_handles_are_recognised(self) -> None:
        assert is_handle(make_handle("r", "person", 5)) is True
        assert is_handle([{"a": 1}]) is False
        assert is_handle({"a": 1}) is False
        assert is_handle(None) is False

    def test_handle_carries_the_count_without_reading_rows(self) -> None:
        state = {"extracted_data": {"person": make_handle("r", "person", 43_000_000)}}

        assert entity_row_count(state, "person") == 43_000_000

    def test_reads_through_a_handle(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(0), _row(1)])
        state = {"extracted_data": {"person": make_handle("run1", "person", 2)}}

        assert len(entity_rows(state, "person")) == 2

    def test_inline_lists_still_work(self) -> None:
        """Callers that hand rows in directly must not silently read empty."""
        state = {"extracted_data": {"person": [_row(0), _row(1)]}}

        assert len(entity_rows(state, "person")) == 2
        assert entity_row_count(state, "person") == 2
        assert entity_origin_counts(state, "person") == {"pg": 2}

    def test_targeted_read_works_for_both_shapes(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(0), _row(1)])

        spooled = {"extracted_data": {"person": make_handle("run1", "person", 2)}}
        inline = {"extracted_data": {"person": [_row(0), _row(1)]}}

        for state in (spooled, inline):
            found = entity_records_by_id(state, "person", ["pg:staff:1"])
            assert list(found) == ["pg:staff:1"]

    def test_absent_entity_is_empty_not_an_error(self) -> None:
        assert entity_rows({}, "person") == []
        assert entity_row_count({"extracted_data": {}}, "person") == 0


class TestHandleSize:
    def test_a_handle_is_tiny_regardless_of_row_count(self) -> None:
        """This is the whole point: checkpoint size stops tracking data size."""
        handle = make_handle("run1", "person", 43_276_150)

        assert len(json.dumps(handle)) < 120


class TestCleanup:
    def test_only_unknown_and_old_runs_are_swept(self) -> None:
        """Being unknown is not enough — the spool must also be idle.

        A freshly written spool belongs to a run in flight, whether or not
        this process knows about it. `min_age_seconds=0` here opts into the
        old membership-only behaviour to exercise the keep-set itself.
        """
        for run in ("keep_me", "sweep_me"):
            s = RunSpool(run)
            s.reset_entity("person")
            s.append("person", [_row(0)])

        # Default age guard: both are seconds old, so neither goes.
        assert cleanup_old_spools(keep_run_ids={"keep_me"}) == 0
        assert RunSpool("sweep_me").exists()

        removed = cleanup_old_spools(keep_run_ids={"keep_me"}, min_age_seconds=0)

        assert removed == 1
        assert RunSpool("keep_me").exists()
        assert not RunSpool("sweep_me").exists()

    def test_a_paused_run_s_spool_survives(self) -> None:
        """A run parked at a gate still needs its rows when the human returns."""
        paused = RunSpool("paused_at_gate")
        paused.reset_entity("person")
        paused.append("person", [_row(0)])

        cleanup_old_spools(keep_run_ids={"paused_at_gate", "brand_new_run"})

        assert paused.read_all("person")[0]["name"] == "person 0"

    def test_cleanup_with_no_spool_directory_is_harmless(self) -> None:
        assert cleanup_old_spools(keep_run_ids=set()) == 0

    def test_discard_removes_one_run(self) -> None:
        spool = RunSpool("run1")
        spool.reset_entity("person")
        spool.append("person", [_row(0)])

        spool.discard()

        assert not spool.exists()


class TestCleanupNeverDeletesLiveSpools:
    """The 43M-row run lost 24,200,000 rows to this.

    Cleanup used to delete every spool whose run_id was absent from the
    caller's keep-set. That set comes from `_active_workflows`, which lives in
    memory — so a concurrent run, or any run parked at a HITL gate after an
    app restart, looked like garbage. Age is now the primary guard.
    """

    def test_a_spool_being_written_right_now_survives(self, tmp_path, monkeypatch):
        monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path)
        live = tmp_path / "someone_elses_run.duckdb"
        live.write_bytes(b"rows")

        # Another merge starts and knows nothing about this run.
        removed = spool_mod.cleanup_old_spools(keep_run_ids={"my_run"})

        assert removed == 0
        assert live.exists(), "a spool touched seconds ago is still in use"

    def test_a_genuinely_old_spool_is_swept(self, tmp_path, monkeypatch):
        monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path)
        stale = tmp_path / "finished_last_week.duckdb"
        stale.write_bytes(b"rows")
        old = time.time() - (7 * 24 * 60 * 60)
        os.utime(stale, (old, old))

        assert spool_mod.cleanup_old_spools(keep_run_ids=set()) == 1
        assert not stale.exists()

    def test_an_old_spool_still_in_the_keep_set_survives(self, tmp_path, monkeypatch):
        monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path)
        parked = tmp_path / "wf_parked.duckdb"
        parked.write_bytes(b"rows")
        old = time.time() - (7 * 24 * 60 * 60)
        os.utime(parked, (old, old))

        removed = spool_mod.cleanup_old_spools(keep_run_ids={"wf_parked"})
        assert removed == 0
        assert parked.exists()


class TestAppendVerifiesItsWrite:
    def test_a_spool_replaced_mid_run_raises(self, tmp_path, monkeypatch):
        """Silent loss becomes a loud failure."""
        monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path)
        spool = spool_mod.RunSpool("wf_x")
        spool.reset_entity("person")
        spool.append("person", [{"a": 1}, {"a": 2}])

        # Simulate what cleanup used to do: delete the file underneath the
        # run. The next append would previously have started from an empty
        # table and reported success.
        real_connect = spool._connect

        def sabotage():
            con = real_connect()
            con.execute("DELETE FROM rows_person")
            return con

        monkeypatch.setattr(spool, "_connect", sabotage)
        with pytest.raises(DataQualityError, match="cannot be trusted"):
            spool.append("person", [{"a": 3}])
