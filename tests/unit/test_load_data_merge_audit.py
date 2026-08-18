"""Unit tests for Phase 7.2: persisting per-pair merge evidence.

Phase 7.1 attached `match_evidence` to every multi-member cluster but never
wrote it anywhere durable -- it lived only in the run's own state/spool,
gone once the run finished. `_persist_merge_audit` is the write path into
`merge_engine_merge_audit` (via the connector's `save_merge_audit`), called
inside the load's own transaction next to the existing entity-map persist.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.orchestrator.nodes.load_data import _persist_merge_audit


def _state_with_clusters(entity_type: str, clusters: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run_id": "run-123",
        "entity_clusters": {entity_type: clusters},
    }


class TestPersistMergeAudit:
    def test_writes_one_row_per_pair_with_fellegi_sunter_method(self) -> None:
        connector = MagicMock()
        connector.save_merge_audit = MagicMock(return_value=1)
        state = _state_with_clusters(
            "company",
            [
                {
                    "cluster_id": "cid-1",
                    "record_ids": ["o1:t:a", "o2:t:b"],
                    "match_evidence": {
                        "pairs": [
                            {
                                "a": "o1:t:a",
                                "b": "o2:t:b",
                                "confidence": 0.98,
                                "method": "fellegi_sunter",
                                "matched_fields": [{"field": "name", "weight": 3.1}],
                                "mismatched_fields": [],
                            }
                        ]
                    },
                }
            ],
        )
        _persist_merge_audit(state, {"type": "postgresql"}, {"company"}, connector=connector)

        connector.save_merge_audit.assert_called_once()
        call = connector.save_merge_audit.call_args
        assert call.args[0] == "company"
        assert call.args[1] == "run-123"
        rows = call.args[2]
        assert len(rows) == 1
        row = rows[0]
        assert row["cluster_id"] == "cid-1"
        assert row["record_a"] == "o1:t:a"
        assert row["record_b"] == "o2:t:b"
        assert row["confidence"] == 0.98
        assert row["method"] == "fellegi_sunter"
        assert row["decided_by"] == "auto"
        assert row["human_override"] is None
        assert row["evidence"]["matched_fields"] == [{"field": "name", "weight": 3.1}]

    def test_llm_method_maps_to_llm_decided_by(self) -> None:
        connector = MagicMock()
        connector.save_merge_audit = MagicMock(return_value=1)
        state = _state_with_clusters(
            "company",
            [
                {
                    "cluster_id": "cid-1",
                    "record_ids": ["a", "b"],
                    "match_evidence": {
                        "pairs": [
                            {
                                "a": "a",
                                "b": "b",
                                "confidence": 0.9,
                                "method": "llm",
                                "matched_fields": [],
                                "mismatched_fields": [],
                                "reason": "same entity, abbreviated name",
                            }
                        ]
                    },
                }
            ],
        )
        _persist_merge_audit(state, {"type": "postgresql"}, {"company"}, connector=connector)

        row = connector.save_merge_audit.call_args.args[2][0]
        assert row["decided_by"] == "llm"
        assert row["evidence"]["reason"] == "same entity, abbreviated name"

    def test_human_confirmed_cluster_overrides_decided_by(self) -> None:
        """cluster_edits.apply_cluster_decisions' "keep" path sets
        reviewed_by_human on the cluster without touching match_evidence --
        that explicit confirmation must outrank the original auto/llm
        decision in the audit trail."""
        connector = MagicMock()
        connector.save_merge_audit = MagicMock(return_value=1)
        state = _state_with_clusters(
            "company",
            [
                {
                    "cluster_id": "cid-1",
                    "record_ids": ["a", "b"],
                    "reviewed_by_human": True,
                    "match_evidence": {
                        "pairs": [
                            {
                                "a": "a",
                                "b": "b",
                                "confidence": 0.8,
                                "method": "fellegi_sunter",
                                "matched_fields": [],
                                "mismatched_fields": [],
                            }
                        ]
                    },
                }
            ],
        )
        _persist_merge_audit(state, {"type": "postgresql"}, {"company"}, connector=connector)

        row = connector.save_merge_audit.call_args.args[2][0]
        assert row["decided_by"] == "human"
        assert row["human_override"] == "merge"

    def test_human_split_pairs_are_persisted_with_split_override(self) -> None:
        """Phase 7.2's other half: a human split's evidence (captured by
        cluster_edits.apply_cluster_decisions before the cluster was torn
        apart, carried in state["human_split_pairs"]) must reach the audit
        table too, not just a human's "keep" confirmation."""
        connector = MagicMock()
        connector.save_merge_audit = MagicMock(return_value=1)
        state = {
            "run_id": "run-123",
            "entity_clusters": {"person": []},
            "human_split_pairs": [
                {
                    "entity_type": "person",
                    "cluster_id": "cid-1",
                    "record_a": "o1:t:a",
                    "record_b": "o2:t:b",
                    "confidence": 0.8,
                    "method": "fellegi_sunter",
                    "matched_fields": [{"field": "name", "weight": 3.1}],
                    "mismatched_fields": [{"field": "email", "weight": -2.0}],
                    "reason": None,
                }
            ],
        }
        _persist_merge_audit(state, {"type": "postgresql"}, {"person"}, connector=connector)

        connector.save_merge_audit.assert_called_once()
        call = connector.save_merge_audit.call_args
        assert call.args[0] == "person"
        assert call.args[1] == "run-123"
        row = call.args[2][0]
        assert row["cluster_id"] == "cid-1"
        assert row["record_a"] == "o1:t:a"
        assert row["record_b"] == "o2:t:b"
        assert row["decided_by"] == "human"
        assert row["human_override"] == "split"
        assert row["evidence"]["matched_fields"] == [{"field": "name", "weight": 3.1}]
        assert row["evidence"]["mismatched_fields"] == [{"field": "email", "weight": -2.0}]

    def test_human_split_pairs_for_a_different_entity_type_are_ignored(self) -> None:
        """A split pair for an entity this load run never touched must not
        be written -- entity_types mirrors the per-cluster loop's own
        filter."""
        connector = MagicMock()
        connector.save_merge_audit = MagicMock(return_value=0)
        state = {
            "run_id": "run-123",
            "entity_clusters": {},
            "human_split_pairs": [
                {
                    "entity_type": "company",
                    "cluster_id": "cid-1",
                    "record_a": "a",
                    "record_b": "b",
                }
            ],
        }
        _persist_merge_audit(state, {"type": "postgresql"}, {"person"}, connector=connector)
        connector.save_merge_audit.assert_not_called()

    def test_singleton_cluster_with_no_match_evidence_writes_nothing(self) -> None:
        connector = MagicMock()
        connector.save_merge_audit = MagicMock(return_value=0)
        state = _state_with_clusters("company", [{"cluster_id": "cid-1", "record_ids": ["a"]}])
        _persist_merge_audit(state, {"type": "postgresql"}, {"company"}, connector=connector)
        connector.save_merge_audit.assert_not_called()

    def test_pair_missing_a_or_b_is_skipped(self) -> None:
        connector = MagicMock()
        connector.save_merge_audit = MagicMock(return_value=0)
        state = _state_with_clusters(
            "company",
            [
                {
                    "cluster_id": "cid-1",
                    "record_ids": ["a", "b"],
                    "match_evidence": {"pairs": [{"a": "", "b": "b", "confidence": 0.9}]},
                }
            ],
        )
        _persist_merge_audit(state, {"type": "postgresql"}, {"company"}, connector=connector)
        connector.save_merge_audit.assert_not_called()

    def test_connector_without_save_merge_audit_is_a_silent_noop(self) -> None:
        connector = MagicMock(spec=["close"])
        state = _state_with_clusters(
            "company",
            [
                {
                    "cluster_id": "cid-1",
                    "record_ids": ["a", "b"],
                    "match_evidence": {"pairs": [{"a": "a", "b": "b", "confidence": 0.9}]},
                }
            ],
        )
        # Must not raise.
        _persist_merge_audit(state, {"type": "postgresql"}, {"company"}, connector=connector)

    def test_failure_is_swallowed_not_raised(self) -> None:
        """Unlike _persist_entity_map, a merge-audit failure must never roll
        the load back -- it is observability, not correctness."""
        connector = MagicMock()
        connector.save_merge_audit = MagicMock(side_effect=RuntimeError("db exploded"))
        state = _state_with_clusters(
            "company",
            [
                {
                    "cluster_id": "cid-1",
                    "record_ids": ["a", "b"],
                    "match_evidence": {"pairs": [{"a": "a", "b": "b", "confidence": 0.9}]},
                }
            ],
        )
        # Must not raise.
        _persist_merge_audit(state, {"type": "postgresql"}, {"company"}, connector=connector)

    def test_no_entity_types_is_a_noop(self) -> None:
        connector = MagicMock()
        _persist_merge_audit({}, {"type": "postgresql"}, set(), connector=connector)
        connector.save_merge_audit.assert_not_called()

    def test_non_dict_destination_is_a_noop(self) -> None:
        connector = MagicMock()
        _persist_merge_audit({}, MagicMock(), {"company"}, connector=connector)
        connector.save_merge_audit.assert_not_called()
