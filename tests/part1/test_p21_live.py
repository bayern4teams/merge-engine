"""P21 live acceptance tier (`.handoff/P21.acceptance.md`), env-gated.

Skips wholesale unless MERGE_ENGINE_LIVE_DB=1 (CI has no Postgres/Mongo).
Every "done" claim here is a SQL count or an HTTP status — the API's own
answers are never the evidence (CLAUDE.md: live, independent). Items 8/9 are
measured and REPORTED, never asserted (owner rule: no quality assertion).
"""

from __future__ import annotations

from typing import Any

from tests.part1.p21_live_harness import ER_TABLES, live_only

pytestmark = [live_only]


# .handoff/P21.acceptance.md E1: every ensure_* is idempotent — run twice,
# identical catalog, no exception.
def test_e1_ensure_er_side_tables_idempotent(dest: Any, sql: Any, itest_schema: str) -> None:
    dest.ensure_er_side_tables()
    dest.ensure_er_side_tables()  # second call must be a no-op
    sql.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = %s ORDER BY table_name
        """,
        (itest_schema,),
    )
    found = {r[0] for r in sql.fetchall()}
    assert set(ER_TABLES) <= found, f"missing: {set(ER_TABLES) - found}"
    # purge path runs and is a no-op on fresh tables
    assert dest.purge_er_idempotency(days=30) == 0
