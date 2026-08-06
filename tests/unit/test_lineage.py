from src.orchestrator.utils.lineage import build_lineage


def test_build_lineage_multi_origin_provenance():
    extracted_data = {
        "Company": [
            {
                "company_name": "Boeing",
                "_origin_id": "origin1",
                "_origin_table": "raw_boeing_db1",
                "_origin_id_row": 101,
            },
            {
                "company_name": "Boeing Inc",
                "_origin_id": "origin2",
                "_origin_table": "raw_boeing_db2",
                "_origin_id_row": 202,
            },
        ]
    }
    field_mappings = {
        "Company": {
            "company_name": "name",
        }
    }
    conflict_records = {
        "Company": {
            "company_name": [
                {
                    "field": "company_name",
                    "values": [
                        {"origin": "origin1", "value": "Boeing"},
                        {"origin": "origin2", "value": "Boeing Inc"},
                    ],
                    "selected_origin": "origin1",
                    "selected_value": "Boeing",
                    "winning_rule": "priority",
                }
            ]
        }
    }

    lineage = build_lineage(
        extracted_data=extracted_data,
        field_mappings=field_mappings,
        conflict_records=conflict_records,
    )

    assert lineage["status"] == "completed"
    entities = lineage["entities"]
    assert "Company" in entities
    col_info = entities["Company"]["columns"]["name"]

    assert col_info["source_field"] == "company_name"
    assert col_info["multi_origin"] is True
    assert len(col_info["origins"]) == 2
    assert col_info["origins"][0]["origin_id"] == "origin1"
    assert col_info["origins"][1]["origin_id"] == "origin2"

    # Conflicted field preserved
    assert len(col_info["conflicts"]) == 1
    assert col_info["conflicts"][0]["winning_rule"] == "priority"

    # Known limitations present
    assert "limitations" in lineage
    assert "source_ids_granularity" in lineage["limitations"]
    assert "durable_entity_map" in lineage["limitations"]
