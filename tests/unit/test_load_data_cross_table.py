"""Unit tests for Phase 6.3: writing child rows for cross-table mappings.

Before this, `load_data_impl` treated an entity's field mapping as a single
flat dict destined entirely for that entity's own table. Phase 6.2 widened
`map_fields_impl` to tag a mapping entry with a `dest_table` other than the
entity's own (a sibling reached via the destination's real FK graph, e.g.
`Company.totalInvestment -> finance.amount`); these tests cover the
consumer side -- grouping a mapping by destination table and writing one
child row per parent record into each sibling table, inside the same
transaction the caller already holds open.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.mcp_server.tools.load_data import (
    _group_mapping_by_dest_table,
    _write_child_rows,
    load_data_impl,
)


class TestGroupMappingByDestTable:
    def test_plain_mapping_with_no_dest_table_collapses_to_primary(self) -> None:
        mapping = {
            "org_title": {"dest_column": "name", "confidence": 1.0},
            "sector": {"dest_column": "industry", "confidence": 0.9},
        }
        grouped = _group_mapping_by_dest_table(mapping, "company")
        assert grouped == {"company": mapping}

    def test_cross_table_entries_split_into_their_own_group(self) -> None:
        mapping = {
            "org_title": {"dest_column": "name", "dest_table": "company", "confidence": 1.0},
            "totalInvestment": {
                "dest_column": "amount",
                "dest_table": "finance",
                "confidence": 0.9,
            },
            "contactEmail": {
                "dest_column": "email",
                "dest_table": "contact_email",
                "confidence": 0.85,
            },
        }
        grouped = _group_mapping_by_dest_table(mapping, "company")
        assert set(grouped) == {"company", "finance", "contact_email"}
        assert grouped["company"] == {"org_title": mapping["org_title"]}
        assert grouped["finance"] == {"totalInvestment": mapping["totalInvestment"]}
        assert grouped["contact_email"] == {"contactEmail": mapping["contactEmail"]}

    def test_empty_mapping_yields_only_the_primary_key_empty(self) -> None:
        assert _group_mapping_by_dest_table(None, "company") == {"company": {}}
        assert _group_mapping_by_dest_table({}, "company") == {"company": {}}

    def test_dest_table_equal_to_primary_stays_in_primary_group(self) -> None:
        mapping = {"org_title": {"dest_column": "name", "dest_table": "company"}}
        grouped = _group_mapping_by_dest_table(mapping, "company")
        assert grouped == {"company": mapping}


def _connector_with_fk(child_table: str, fk_column: str, parent_table: str) -> MagicMock:
    connector = MagicMock()
    connector.get_foreign_keys = MagicMock(
        side_effect=lambda t: {fk_column: parent_table} if t == child_table else {}
    )
    connector.ensure_table = MagicMock(return_value=False)
    connector.load_batch = MagicMock(return_value={"inserted": 1, "updated": 0})
    return connector


class TestWriteChildRows:
    def test_writes_one_child_row_per_parent_with_the_fk_set(self) -> None:
        connector = _connector_with_fk("finance", "company_id", "company")
        source_records = [{"totalInvestment": 4_200_000, "_cluster_id": "c1"}]
        transformed_parents = [{"id": "dest-id-1"}]
        mapping_by_table = {
            "finance": {"totalInvestment": {"dest_column": "amount", "dest_table": "finance"}}
        }

        results = _write_child_rows(
            connector=connector,
            primary_table="company",
            source_records=source_records,
            transformed_parent_records=transformed_parents,
            mapping_by_table=mapping_by_table,
        )

        assert results == {"finance": {"inserted": 1, "updated": 0}}
        connector.load_batch.assert_called_once()
        call = connector.load_batch.call_args
        assert call.kwargs["table_name"] == "finance"
        child_records = call.kwargs["records"]
        assert len(child_records) == 1
        assert child_records[0]["amount"] == 4_200_000
        assert child_records[0]["company_id"] == "dest-id-1"
        assert child_records[0]["id"]  # a fresh id was generated for the child row

    def test_all_none_child_record_is_not_written(self) -> None:
        connector = _connector_with_fk("finance", "company_id", "company")
        source_records = [{"_cluster_id": "c1"}]  # never carried totalInvestment
        transformed_parents = [{"id": "dest-id-1"}]
        mapping_by_table = {
            "finance": {"totalInvestment": {"dest_column": "amount", "dest_table": "finance"}}
        }

        results = _write_child_rows(
            connector=connector,
            primary_table="company",
            source_records=source_records,
            transformed_parent_records=transformed_parents,
            mapping_by_table=mapping_by_table,
        )

        assert results == {}
        connector.load_batch.assert_not_called()

    def test_missing_reverse_fk_skips_the_table_without_raising(self) -> None:
        connector = MagicMock()
        # finance has no FK back to company at all (schema drift, or the
        # family relationship runs the OTHER direction) -- must not guess.
        connector.get_foreign_keys = MagicMock(return_value={})
        source_records = [{"totalInvestment": 100}]
        transformed_parents = [{"id": "dest-id-1"}]
        mapping_by_table = {
            "finance": {"totalInvestment": {"dest_column": "amount", "dest_table": "finance"}}
        }

        results = _write_child_rows(
            connector=connector,
            primary_table="company",
            source_records=source_records,
            transformed_parent_records=transformed_parents,
            mapping_by_table=mapping_by_table,
        )

        assert results == {}
        connector.load_batch.assert_not_called()

    def test_connector_without_get_foreign_keys_skips_everything(self) -> None:
        connector = MagicMock(spec=["load_batch"])
        results = _write_child_rows(
            connector=connector,
            primary_table="company",
            source_records=[{"totalInvestment": 100}],
            transformed_parent_records=[{"id": "dest-id-1"}],
            mapping_by_table={
                "finance": {"totalInvestment": {"dest_column": "amount", "dest_table": "finance"}}
            },
        )
        assert results == {}

    def test_parent_record_without_id_is_skipped(self) -> None:
        connector = _connector_with_fk("finance", "company_id", "company")
        source_records = [{"totalInvestment": 100}, {"totalInvestment": 200}]
        transformed_parents = [{"id": None}, {"id": "dest-id-2"}]
        mapping_by_table = {
            "finance": {"totalInvestment": {"dest_column": "amount", "dest_table": "finance"}}
        }

        _write_child_rows(
            connector=connector,
            primary_table="company",
            source_records=source_records,
            transformed_parent_records=transformed_parents,
            mapping_by_table=mapping_by_table,
        )

        child_records = connector.load_batch.call_args.kwargs["records"]
        assert len(child_records) == 1
        assert child_records[0]["company_id"] == "dest-id-2"

    def test_multiple_sibling_tables_each_get_their_own_child_rows(self) -> None:
        connector = MagicMock()

        def get_fks(table: str) -> dict[str, str]:
            return {
                "finance": {"company_id": "company"},
                "contact_email": {"company_id": "company"},
            }.get(table, {})

        connector.get_foreign_keys = MagicMock(side_effect=get_fks)
        connector.ensure_table = MagicMock(return_value=False)
        connector.load_batch = MagicMock(return_value={"inserted": 1, "updated": 0})

        source_records = [{"totalInvestment": 100, "contactEmail": "a@b.com"}]
        transformed_parents = [{"id": "dest-id-1"}]
        mapping_by_table = {
            "finance": {"totalInvestment": {"dest_column": "amount", "dest_table": "finance"}},
            "contact_email": {
                "contactEmail": {"dest_column": "email", "dest_table": "contact_email"}
            },
        }

        results = _write_child_rows(
            connector=connector,
            primary_table="company",
            source_records=source_records,
            transformed_parent_records=transformed_parents,
            mapping_by_table=mapping_by_table,
        )

        assert set(results) == {"finance", "contact_email"}
        assert connector.load_batch.call_count == 2


@pytest.mark.asyncio
class TestLoadDataImplCrossTableIntegration:
    async def test_load_data_impl_writes_parent_and_child_rows_in_one_call(self) -> None:
        connector = MagicMock()
        connector.ensure_table = MagicMock(return_value=False)

        def get_fks(table: str) -> dict[str, str]:
            return {"finance": {"company_id": "company"}}.get(table, {})

        connector.get_foreign_keys = MagicMock(side_effect=get_fks)

        def load_batch(table_name: str, records: list[dict[str, Any]], **kwargs: Any) -> dict:
            if table_name == "company":
                for r in records:
                    r.setdefault("id", "generated")
            return {"inserted": len(records), "updated": 0}

        connector.load_batch = MagicMock(side_effect=load_batch)

        records = [
            {
                "id": "company-1",
                "org_title": "Acme Corp",
                "totalInvestment": 4_200_000,
                "_cluster_id": "company-1",
            }
        ]
        mapping = {
            "org_title": {"dest_column": "name", "dest_table": "company", "confidence": 1.0},
            "totalInvestment": {
                "dest_column": "amount",
                "dest_table": "finance",
                "confidence": 0.9,
            },
        }

        result = await load_data_impl(
            entity_type="company",
            records=records,
            mapping=mapping,
            table_name="company",
            connector=connector,
        )

        assert result["success"] is True
        assert result["child_tables"] == {"finance": {"inserted": 1, "updated": 0}}
        # Two load_batch calls: one for the parent table, one for the child.
        tables_written = [c.kwargs["table_name"] for c in connector.load_batch.call_args_list]
        assert tables_written == ["company", "finance"]
        # The parent write never saw the finance-tagged field.
        parent_call = connector.load_batch.call_args_list[0]
        assert "amount" not in parent_call.kwargs["records"][0]

    async def test_single_table_mapping_yields_empty_child_tables(self) -> None:
        connector = MagicMock()
        connector.ensure_table = MagicMock(return_value=False)
        connector.load_batch = MagicMock(return_value={"inserted": 1, "updated": 0})

        records = [{"id": "company-1", "org_title": "Acme Corp"}]
        mapping = {"org_title": {"dest_column": "name", "confidence": 1.0}}

        result = await load_data_impl(
            entity_type="company",
            records=records,
            mapping=mapping,
            table_name="company",
            connector=connector,
        )

        assert result["child_tables"] == {}
        connector.get_foreign_keys.assert_not_called()
