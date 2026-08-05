"""N.2: keyset pagination cursors, and the type traps that broke them.

The correctness stake is that a batch boundary landing inside a run of equal
watermark values must not skip or repeat rows — on a 16k-row table with a
heavily repeated `last_update`, that is thousands of rows either way.
"""

from __future__ import annotations

import json

import pytest

from src.mcp_server.connectors.keyset import (
    choose_keyset,
    decode_cursor,
    encode_cursor,
)
from src.mcp_server.connectors.postgres import _keyset_placeholder, _text_cast


class TestChooseKeyset:
    def test_watermark_leads_and_primary_key_breaks_ties(self) -> None:
        """The PK suffix is what makes a repeated watermark safe to seek on."""
        keyset = choose_keyset({"column": "last_update"}, ["rental_id"])

        assert keyset == ["last_update", "rental_id"]

    def test_primary_key_alone_is_enough(self) -> None:
        assert choose_keyset(None, ["id"]) == ["id"]

    def test_watermark_alone_is_accepted(self) -> None:
        assert choose_keyset({"column": "updated_at"}, []) == ["updated_at"]

    def test_a_pk_that_is_also_the_watermark_is_not_repeated(self) -> None:
        assert choose_keyset({"column": "id"}, ["id"]) == ["id"]

    def test_nothing_orderable_yields_no_keyset(self) -> None:
        """Signals the caller to fall back to offset paging rather than guess."""
        assert choose_keyset(None, None) == []
        assert choose_keyset({}, []) == []

    def test_blank_columns_are_ignored(self) -> None:
        assert choose_keyset(None, ["", None, "id"]) == ["id"]


class TestCursorRoundTrip:
    def test_keyset_values_survive_the_round_trip(self) -> None:
        cursor = encode_cursor(keyset_values=["2022-02-16 02:30:53+00:00", 4501])

        assert decode_cursor(cursor)["keyset"] == [
            "2022-02-16 02:30:53+00:00",
            4501,
        ]

    def test_row_count_rides_along_so_it_is_computed_once(self) -> None:
        """Re-counting every batch is a full scan per batch on a big table."""
        cursor = encode_cursor(keyset_values=[1], total_count=43_000_000)

        assert decode_cursor(cursor)["total_count"] == 43_000_000

    def test_offset_cursors_still_work(self) -> None:
        decoded = decode_cursor(encode_cursor(offset=2000, total_count=10))

        assert decoded["offset"] == 2000
        assert decoded["keyset"] is None

    def test_bare_integer_cursors_from_the_old_format_still_resume(self) -> None:
        """Runs checkpointed before keyset paging must not restart or crash."""
        decoded = decode_cursor("12000")

        assert decoded["offset"] == 12000
        assert decoded["keyset"] is None

    def test_absent_cursor_starts_at_the_beginning(self) -> None:
        assert decode_cursor(None) == {
            "keyset": None,
            "offset": 0,
            "total_count": None,
            "boundary_seen": 0,
        }

    def test_unreadable_cursor_restarts_rather_than_crashing(self) -> None:
        assert decode_cursor("{not json")["offset"] == 0

    def test_non_json_types_are_stringified_not_dropped(self) -> None:
        from datetime import datetime

        cursor = encode_cursor(keyset_values=[datetime(2022, 2, 16, 2, 30, 53)])

        assert decode_cursor(cursor)["keyset"] == ["2022-02-16 02:30:53"]

    def test_cursor_is_json_so_it_survives_checkpointing(self) -> None:
        json.loads(encode_cursor(keyset_values=[1, "a"], total_count=5))


class TestAsyncpgTypeBinding:
    """The trap that made timestamp watermarks fail outright.

    asyncpg reads each parameter's type from how the statement uses it, so
    `CAST($1 AS TIMESTAMPTZ)` declares $1 a timestamptz and asyncpg then
    refuses the string a JSON cursor actually holds. Pinning to text first
    makes PostgreSQL do the parsing.
    """

    def test_a_bare_cast_would_not_be_enough(self) -> None:
        assert _text_cast(":w", "TIMESTAMPTZ") == "CAST(CAST(:w AS TEXT) AS TIMESTAMPTZ)"

    def test_offset_aware_timestamps_cast_to_timestamptz(self) -> None:
        placeholder = _keyset_placeholder(0, "2022-02-16 02:30:53+00:00")

        assert "TIMESTAMPTZ" in placeholder
        assert "AS TEXT" in placeholder

    @pytest.mark.parametrize(
        "value",
        ["2022-02-16 02:30:53", "2022-02-16T02:30:53", "2022-02-16"],
    )
    def test_naive_timestamps_cast_to_timestamp(self, value: str) -> None:
        """A naive column compared to a timestamptz would shift by the
        session timezone — a silently wrong batch boundary, not an error."""
        placeholder = _keyset_placeholder(0, value)

        assert "AS TIMESTAMP)" in placeholder
        assert "TIMESTAMPTZ" not in placeholder

    def test_integers_are_bound_directly(self) -> None:
        assert _keyset_placeholder(1, 4501) == ":ks_1"

    def test_ordinary_strings_are_not_mistaken_for_timestamps(self) -> None:
        assert _keyset_placeholder(0, "ACME Corp") == ":ks_0"
        assert _keyset_placeholder(0, "2022") == ":ks_0"
