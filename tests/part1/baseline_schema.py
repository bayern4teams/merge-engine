"""BEFORE numbers for P1 / P3 / P17 on the CURRENT code (Phase 0.6, P0-6-schema).

    export TMPDIR=$PWD/.tmp
    .venv311/bin/python -m tests.part1.baseline_schema   # rewrites before_schema.json

No DB, no LLM: map_fields_impl runs with llm_config=None; join_graph's connector
factory and analyze_dependencies' LLMClient are replaced by in-memory stubs (the
LLM stub records calls and returns nothing); the destination is a recording stub.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

from src.mcp_server.tools import join_graph as jg
from src.mcp_server.tools import map_fields as mf
from tests.part1 import fixtures_schema as fx

# nodes/__init__ re-exports same-named functions, shadowing the modules: go via sys.modules.
ad = importlib.import_module("src.orchestrator.nodes.analyze_dependencies")
ld = importlib.import_module("src.orchestrator.nodes.load_data")

OUT = Path(__file__).with_name("before_schema.json")


# ── P1 ────────────────────────────────────────────────────────────────────
def run_p1() -> dict:
    out: dict = {}
    dest = fx.p1_destination_schema()
    dest_cols = [c["name"] for c in dest["columns"] if c["name"] != "id"]
    for set_name, truth in fx.P1_SETS.items():
        res = asyncio.run(
            mf.map_fields_impl(
                "org", fx.p1_unified_schema(list(truth)), dest, llm_config=None, connector=None
            )
        )
        mapped = res["field_mapping"]
        unmapped = {u["field"]: u for u in res["unmapped_fields"]}
        fields, tp, wrong, missed, false_pos = {}, 0, 0, 0, 0
        for src, want in truth.items():
            got = mapped.get(src, {}).get("dest_column")
            best = max(((mf._heuristic_match(src, d), d) for d in dest_cols), key=lambda x: x[0])
            if got is None:
                status = "ok_unmapped" if want is None else "MISSED"
                missed += want is not None
            elif want is None:
                status, false_pos = "FALSE_MAP", false_pos + 1
            elif got == want:
                status, tp = "ok", tp + 1
            else:
                status, wrong = "WRONG_TARGET", wrong + 1
            fields[src] = {
                "tokens": mf._tokenize_field(src),
                "truth": want,
                "mapped_to": got,
                "mapped_confidence": mapped.get(src, {}).get("confidence"),
                "best_heuristic": [best[1], round(best[0], 4)],
                "unmapped_reason": unmapped.get(src, {}).get("reason"),
                "unmapped_suggested": unmapped.get(src, {}).get("suggested_column"),
                "status": status,
            }
        out[set_name] = {
            "n_fields": len(truth),
            "n_should_map": sum(v is not None for v in truth.values()),
            "correct": tp,
            "missed": missed,
            "wrong_target": wrong,
            "false_map": false_pos,
            "mapped_total": len(mapped),
            "unmapped_total": len(res["unmapped_fields"]),
            "fields": fields,
        }
    return out


# ── P3 ────────────────────────────────────────────────────────────────────
class _StubOrigin:
    """In-memory stand-in for an origin connector (sample_distinct_values only)."""

    def __init__(self, rows: dict[str, dict[str, list]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, str, int]] = []

    async def sample_distinct_values(self, table, column, limit=500, filter_values=None):
        vals = list(dict.fromkeys(self.rows[table][column]))
        if filter_values is not None:
            keep = {jg.normalize_val(v) for v in filter_values}
            vals = [v for v in vals if jg.normalize_val(v) in keep]
        self.queries.append((table, column, len(vals)))
        return vals[:limit]

    async def close(self) -> None:
        return None


class _StubLLM:
    """Records every call; returns nothing (so the node falls to its matrix fallback)."""

    calls: list[str] = []

    def __init__(self, cfg) -> None:
        pass

    async def call_json(self, system_prompt="", user_prompt="", **kw):
        _StubLLM.calls.append("call_json")
        return {}


def _edge_key(e) -> tuple[str, str, str, str]:
    return (e.child_table, e.child_column, e.parent_table, e.parent_column)


def run_p3() -> dict:
    stub = _StubOrigin(fx.p3_rows())
    schemas = {fx.P3_ORIGIN: {"tables": fx.P3_TABLES}}
    origins = [{"id": fx.P3_ORIGIN, "type": "stub", "connection": {}}]

    cands = jg._find_candidate_edges(fx.P3_TABLES)
    cand_keys = {_edge_key(c) for c in cands}

    real_cc = jg.create_connector
    jg.create_connector = lambda *_a, **_k: stub  # type: ignore[assignment]
    try:
        graph = asyncio.run(jg.build_join_graph(schemas, origins))
    finally:
        jg.create_connector = real_cc
    edges = {}
    for t in graph._adjacency.values():
        for e in t:
            edges[_edge_key(e)] = (e.confidence, e.overlap_ratio)
    tree, covered, confirmed = ad._join_graph_to_dependency_tree(
        graph, fx.P3_TARGET_ENTITIES, fx.P3_ENTITY_TABLES
    )

    # Whole node, LLM stubbed out (records calls, returns {}).
    real_llm, real_cc2 = ad.LLMClient, jg.create_connector
    ad.LLMClient = _StubLLM  # type: ignore[misc,assignment]
    jg.create_connector = lambda *_a, **_k: _StubOrigin(fx.p3_rows())  # type: ignore[assignment]
    _StubLLM.calls = []
    try:
        node = asyncio.run(
            ad.analyze_dependencies(
                {
                    "target_entities": fx.P3_TARGET_ENTITIES,
                    "schemas": schemas,
                    "origins": origins,
                    "entity_tables": fx.P3_ENTITY_TABLES,
                    "llm_config": {"stub": True},
                }
            )
        )
    finally:
        ad.LLMClient, jg.create_connector = real_llm, real_cc2  # type: ignore[misc]
    node_edges = sorted(
        (d["child_table"], d["child_column"], d["parent_table"], d["parent_column"])
        for d in node.get("column_dependencies", [])
    )

    def keys(s):
        return sorted(list(k) for k in s)

    true_found = {k for k in fx.P3_TRUE_EDGES if k in edges}
    false_acc = {k for k in fx.P3_FALSE_EDGES if k in edges}
    return {
        "candidate_edges_proposed": keys(cand_keys),
        "true_edges_proposed": keys(fx.P3_TRUE_EDGES & cand_keys),
        "true_edges_total": len(fx.P3_TRUE_EDGES),
        "true_edges_in_graph": keys(true_found),
        "decoy_accepted": keys(false_acc),
        "graph_edges": {"|".join(k): list(v) for k, v in sorted(edges.items())},
        "join_graph_to_dependency_tree": {
            "tree": tree,
            "tree_is_empty_dict": tree == {},
            "n_nonempty_dependencies": sum(bool(v) for v in tree.values()),
            "fully_covered": covered,
            "n_confirmed_edges": len(confirmed),
        },
        "planted_true_tree": fx.P3_TRUE_TREE,
        "tree_matches_truth": tree == fx.P3_TRUE_TREE,
        "analyze_dependencies_node": {
            "origin": node.get("dependency_analysis_origin"),
            "dependency_tree": node.get("dependency_tree"),
            "llm_calls_stub": len(_StubLLM.calls),
            "column_dependencies": [list(e) for e in node_edges],
            "warning": node.get("dependency_analysis_warning"),
        },
    }


# ── P17 ───────────────────────────────────────────────────────────────────
class _RecordingDest:
    """Destination stub: one table `topic_link(id)`; records every write and every SQL/DDL string.

    load_batch mirrors PostgreSQLDestinationConnector.load_batch (columns not in the table are
    dropped before INSERT), so `rows_persisted` is what a real table would end up holding.
    """

    def __init__(self) -> None:
        self.columns = ["id"]
        self.sql: list[str] = []
        self.calls: list[str] = []
        self.received: list[dict] = []
        self.persisted: list[dict] = []

    def get_table_schema(self, table):
        self.calls.append(f"get_table_schema({table})")
        return {
            "primary_key": "id",
            "columns": [{"name": c, "type": "text", "nullable": False} for c in self.columns],
        }

    def ensure_table(self, table):
        self.calls.append(f"ensure_table({table})")
        self.sql.append(f'CREATE TABLE IF NOT EXISTS "public"."{table}" (id TEXT PRIMARY KEY)')
        return True

    def load_batch(self, table_name, records, mapping=None, upsert_key="id"):
        self.calls.append(f"load_batch({table_name}, n={len(records)})")
        self.received.extend(records)
        cols = [c for c in records[0] if c in self.columns]
        self.sql.append(
            f'INSERT INTO "public"."{table_name}" ({", ".join(cols)}) '
            f'ON CONFLICT ("{upsert_key}") DO NOTHING'
        )
        self.persisted.extend({c: r.get(c) for c in cols} for r in records)
        return {"inserted": len(records), "updated": 0}

    def add_column(self, *a, **k):  # any ALTER path would land here
        self.calls.append("add_column")
        self.sql.append("ALTER TABLE (add_column)")

    def extend_schema_batch(self, *a, **k):
        self.calls.append("extend_schema_batch")
        self.sql.append("ALTER TABLE (extend_schema_batch)")
        return {"added": 0, "failed": 0}


def run_p17() -> dict:
    dest_schema = fx.p17_destination_schema()
    mapped = asyncio.run(
        mf.map_fields_impl(
            "topic_link",
            fx.p1_unified_schema(fx.P17_SOURCE_FIELDS),
            dest_schema,
            llm_config=None,
        )
    )
    dest = _RecordingDest()
    load_results: dict = {}
    failed: list = []
    errors: list = []
    asyncio.run(
        ld._load_all_entities(
            entity_order=["topic_link"],
            unified_entities={"topic_link": fx.p17_records()},
            field_mappings={"topic_link": mapped["field_mapping"]},
            destination={"tables": {}},
            available_dest_tables=[fx.P17_DEST_TABLE],
            connector=dest,
            load_results=load_results,
            failed_entities=failed,
            errors=errors,
            state=None,
        )
    )
    return {
        "dest_columns": dest.columns,
        "source_link_rows": fx.P17_N_ROWS,
        "linkage_fields_needed": fx.P17_SOURCE_FIELDS,
        "map_fields": {
            "field_mapping": mapped["field_mapping"],
            "unmapped": [
                {"field": u["field"], "reason": u["reason"]} for u in mapped["unmapped_fields"]
            ],
            "schema_changes": mapped["schema_changes"],
        },
        "load_results": load_results,
        "rows_written_to_table": len(dest.persisted),
        "persisted_row_shapes": sorted({tuple(sorted(r)) for r in dest.persisted}),
        "persisted_nonid_values": sum(
            1 for r in dest.persisted for k, v in r.items() if k != "id" and v is not None
        ),
        "source_linkage_values_lost": fx.P17_N_ROWS * len(fx.P17_SOURCE_FIELDS),
        "failed_entities": failed,
        "errors_reported": errors,
        "defect_reported": bool(errors or failed),
        "sql_issued": sorted(set(dest.sql)),
        "n_alter_issued": sum(s.startswith("ALTER") for s in dest.sql),
        "n_create_issued": sum(s.startswith("CREATE") for s in dest.sql),
        "calls": sorted(set(c.split("n=")[0] for c in dest.calls)),
    }


def main() -> None:
    result = {"p1": run_p1(), "p3": run_p3(), "p17": run_p17()}
    text = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    OUT.write_text(text)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
