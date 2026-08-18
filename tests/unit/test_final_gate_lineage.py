"""Unit tests for Phase 7.3: merge lineage surfaced at the final approval gate.

Before this, a cluster's Phase 7.1 `match_evidence` was visible only in
internal state -- the matches gate shows just the sub-0.85 (ambiguous)
clusters, and only there. `WorkflowManager._clusters_by_entity` builds the
full per-entity, multi-member-cluster list `human_final_approval`'s
interrupt payload now carries, so the reviewer signing off on the load sees
every merge's evidence, not only the ones the system was unsure about.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.workflow_manager import WorkflowManager


@pytest.fixture
def manager():
    with patch("src.orchestrator.workflow_manager.build_graph") as mock_build:
        mock_build.return_value = MagicMock()
        yield WorkflowManager()


class TestClustersByEntity:
    def test_multi_member_cluster_is_included_with_evidence(self, manager) -> None:
        values = {
            "entity_clusters": {
                "company": [
                    {
                        "cluster_id": "cid-1",
                        "record_ids": ["o1:t:a", "o2:t:b"],
                        "match_confidence": 0.97,
                        "required_human_review": False,
                        "match_evidence": {
                            "pairs": [
                                {
                                    "a": "o1:t:a",
                                    "b": "o2:t:b",
                                    "confidence": 0.97,
                                    "method": "fellegi_sunter",
                                    "matched_fields": [{"field": "name", "weight": 3.1}],
                                    "mismatched_fields": [],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        result = WorkflowManager._clusters_by_entity(values)
        assert list(result.keys()) == ["company"]
        cluster = result["company"][0]
        assert cluster["cluster_id"] == "cid-1"
        assert cluster["record_ids"] == ["o1:t:a", "o2:t:b"]
        assert cluster["match_confidence"] == 0.97
        assert cluster["match_evidence"]["pairs"][0]["method"] == "fellegi_sunter"

    def test_singleton_cluster_is_excluded(self, manager) -> None:
        values = {
            "entity_clusters": {
                "company": [{"cluster_id": "cid-1", "record_ids": ["a"], "match_confidence": 1.0}]
            }
        }
        assert WorkflowManager._clusters_by_entity(values) == {}

    def test_entity_with_only_singletons_is_omitted_entirely(self, manager) -> None:
        values = {
            "entity_clusters": {
                "company": [{"cluster_id": "c1", "record_ids": ["a"]}],
                "person": [
                    {
                        "cluster_id": "c2",
                        "record_ids": ["x", "y"],
                        "match_confidence": 0.9,
                    }
                ],
            }
        }
        result = WorkflowManager._clusters_by_entity(values)
        assert list(result.keys()) == ["person"]

    def test_no_entity_clusters_returns_empty_dict(self, manager) -> None:
        assert WorkflowManager._clusters_by_entity({}) == {}

    def test_cluster_without_match_evidence_still_included(self, manager) -> None:
        """A cluster the heuristic/auto path merged before Phase 7.1 evidence
        capture existed (or a heuristic-only pair with no fitted model) has
        no match_evidence -- it must still appear (with match_evidence:
        None), not be silently dropped, since its confidence/members are
        still worth showing."""
        values = {
            "entity_clusters": {
                "company": [
                    {"cluster_id": "c1", "record_ids": ["a", "b"], "match_confidence": 0.96}
                ]
            }
        }
        result = WorkflowManager._clusters_by_entity(values)
        assert result["company"][0]["match_evidence"] is None

    def test_only_the_expected_keys_are_forwarded(self, manager) -> None:
        """The payload must not leak whatever internal bookkeeping a cluster
        happens to carry (e.g. a future field added to the cluster dict for
        internal use only)."""
        values = {
            "entity_clusters": {
                "company": [
                    {
                        "cluster_id": "c1",
                        "record_ids": ["a", "b"],
                        "match_confidence": 0.9,
                        "required_human_review": True,
                        "some_internal_bookkeeping_field": "should not leak",
                    }
                ]
            }
        }
        result = WorkflowManager._clusters_by_entity(values)
        assert set(result["company"][0].keys()) == {
            "cluster_id",
            "record_ids",
            "match_confidence",
            "required_human_review",
            "match_evidence",
        }


class TestFinalApprovalPayloadIncludesLineage:
    def test_build_interrupt_data_carries_clusters_by_entity(self, manager) -> None:
        values = {
            "unified_entities": {},
            "pending_schema_changes": [],
            "errors": [],
            "field_mappings": {},
            "unmapped_fields": {},
            "resolution_stats": {},
            "entity_clusters": {
                "company": [
                    {
                        "cluster_id": "c1",
                        "record_ids": ["a", "b"],
                        "match_confidence": 0.97,
                        "match_evidence": {"pairs": [{"a": "a", "b": "b", "confidence": 0.97}]},
                    }
                ]
            },
        }
        payload = manager._build_interrupt_data("human_final_approval", values)
        clusters_by_entity = payload["data"]["clusters_by_entity"]
        assert clusters_by_entity["company"][0]["cluster_id"] == "c1"
