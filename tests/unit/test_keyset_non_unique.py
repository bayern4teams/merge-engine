"""Keyset pagination when the ordering key is not unique.

The bug these cover was found by running the pipeline against the real 43M-row
cell_towers table rather than by reading the code:

  * ``retry_on_transient`` wrapped every connector's ``extract`` without
    ``functools.wraps``, so a runtime signature check concluded the connectors
    did not accept ``pk_columns`` and never passed it. Keyset pagination
    silently degraded to OFFSET on every origin.
  * With a non-unique key, a strict ``>`` drops every row sharing the batch
    boundary. Measured on a 300,000-row table keyed by three non-unique
    columns: 100,000 rows (33%) lost, run reported success.
"""

from __future__ import annotations

import inspect

import pytest

from src.mcp_server.connectors.keyset import (
    advance_boundary,
    assert_key_unique,
    decode_cursor,
    drop_consumed,
    encode_cursor,
    keyset_is_unique,
)
from src.orchestrator.utils.retry import DataQualityError, retry_on_transient


class TestDecoratorPreservesSignature:
    """The wiring bug: a decorator that hid the parameters it forwards."""

    def test_async_wrapper_keeps_the_wrapped_signature(self) -> None:
        @retry_on_transient(max_attempts=2)
        async def extract(self, table_name, pk_columns=None):  # noqa: ANN001
            return pk_columns

        params = inspect.signature(extract).parameters
        assert "pk_columns" in params, (
            "callers decide whether to pass pk_columns by inspecting the "
            "signature; hiding it silently disables keyset pagination"
        )

    def test_sync_wrapper_keeps_the_wrapped_signature(self) -> None:
        @retry_on_transient(max_attempts=2)
        def fetch(self, table_name, pk_columns=None):  # noqa: ANN001
            return pk_columns

        assert "pk_columns" in inspect.signature(fetch).parameters

    def test_wrapper_keeps_the_wrapped_name(self) -> None:
        @retry_on_transient(max_attempts=2)
        async def extract(self):  # noqa: ANN001
            return None

        assert extract.__name__ == "extract"

    @pytest.mark.asyncio
    async def test_decorated_function_still_runs(self) -> None:
        @retry_on_transient(max_attempts=2)
        async def extract(pk_columns=None):  # noqa: ANN001
            return pk_columns

        assert await extract(pk_columns=["id"]) == ["id"]


class TestUniquenessRule:
    def test_a_key_covering_the_primary_key_is_unique(self) -> None:
        assert keyset_is_unique(["updated_at", "id"], ["id"]) is True

    def test_a_composite_primary_key_must_be_fully_covered(self) -> None:
        assert keyset_is_unique(["a", "b"], ["a", "b"]) is True
        assert keyset_is_unique(["a"], ["a", "b"]) is False

    def test_a_watermark_alone_is_not_unique(self) -> None:
        assert keyset_is_unique(["last_update"], None) is False

    def test_no_keyset_is_not_unique(self) -> None:
        assert keyset_is_unique([], ["id"]) is False


class TestBoundaryAccounting:
    """The ">=" plus skip-count scheme that replaces the lossy strict ">"."""

    KEY = ["k"]

    def test_boundary_rows_already_returned_are_dropped(self) -> None:
        rows = [{"k": 5, "v": "a"}, {"k": 5, "v": "b"}, {"k": 6, "v": "c"}]
        assert drop_consumed(rows, self.KEY, [5], 2) == [{"k": 6, "v": "c"}]

    def test_only_rows_at_the_boundary_key_are_dropped(self) -> None:
        # A stale skip count must never eat rows past the boundary.
        rows = [{"k": 5, "v": "a"}, {"k": 6, "v": "b"}, {"k": 7, "v": "c"}]
        assert drop_consumed(rows, self.KEY, [5], 5) == [
            {"k": 6, "v": "b"}, {"k": 7, "v": "c"},
        ]

    def test_nothing_is_dropped_without_a_boundary(self) -> None:
        rows = [{"k": 1}, {"k": 2}]
        assert drop_consumed(rows, self.KEY, None, 0) == rows

    def test_skip_count_accumulates_within_one_key(self) -> None:
        # A batch that starts and ends on the same key must carry its count
        # forward, or the next batch re-emits rows it already returned.
        emitted = [{"k": 9}, {"k": 9}, {"k": 9}]
        key, seen = advance_boundary(emitted, self.KEY, [9], 4)
        assert key == [9]
        assert seen == 7

    def test_skip_count_resets_on_a_new_key(self) -> None:
        emitted = [{"k": 9}, {"k": 10}, {"k": 10}]
        key, seen = advance_boundary(emitted, self.KEY, [9], 4)
        assert key == [10]
        assert seen == 2

    def test_boundary_survives_a_cursor_round_trip(self) -> None:
        cursor = encode_cursor(keyset_values=[7], boundary_seen=3)
        position = decode_cursor(cursor)
        assert position["keyset"] == [7]
        assert position["boundary_seen"] == 3

    def test_paging_a_single_repeated_key_makes_progress(self) -> None:
        """A key value larger than one batch must still terminate."""
        table = [{"k": 1, "id": i} for i in range(10)]
        batch, key, seen, emitted = 4, None, 0, []
        for _ in range(10):
            window = [r for r in table if key is None or r["k"] >= key[0]]
            window = window[: batch + seen]
            rows = drop_consumed(window, self.KEY, key, seen)[:batch]
            if not rows:
                break
            emitted.extend(rows)
            key, seen = advance_boundary(rows, self.KEY, key, seen)
        assert len(emitted) == 10
        assert len({r["id"] for r in emitted}) == 10


class TestUniqueKeyGuard:
    """A key wrongly reported as a primary key must fail, not lose rows."""

    def test_duplicate_on_a_declared_key_raises(self) -> None:
        rows = [{"k": 1}, {"k": 1}]
        with pytest.raises(DataQualityError, match="non-unique key"):
            assert_key_unique(rows, ["k"], "public.rental")

    def test_the_error_names_the_table_and_the_key(self) -> None:
        with pytest.raises(DataQualityError) as excinfo:
            assert_key_unique([{"k": 1}, {"k": 1}], ["k"], "public.rental")
        message = str(excinfo.value)
        assert "public.rental" in message
        assert "'k'" in message

    def test_a_genuinely_unique_key_passes(self) -> None:
        assert_key_unique([{"k": 1}, {"k": 2}], ["k"], "t") is None

    def test_a_composite_key_compares_all_columns(self) -> None:
        rows = [{"a": 1, "b": 1}, {"a": 1, "b": 2}]
        assert_key_unique(rows, ["a", "b"], "t") is None
        with pytest.raises(DataQualityError):
            assert_key_unique(rows + [{"a": 1, "b": 2}], ["a", "b"], "t")

    def test_escalates_rather_than_failing_the_run_outright(self) -> None:
        """Disposition matters: the user gets retry/skip/abort, not a dead run."""
        with pytest.raises(DataQualityError) as excinfo:
            assert_key_unique([{"k": 1}, {"k": 1}], ["k"], "t")
        assert excinfo.value.disposition == "escalate"
