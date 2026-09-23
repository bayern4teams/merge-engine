"""Unit tests for _relational_agreement and its common-parent guard
(audit §13 #3, RELATER-style, fix-v3 B.5.2). The negative fixture here
(Airbus SE + Airbus Helicopters) is the audit's own §00a2 failure shape,
encoded as a test BEFORE the feature existed, per the plan.
"""

from __future__ import annotations

from src.mcp_server.tools.resolve_entities import (
    _relational_agreement,
    _shared_neighbors_beyond_common_parent,
    build_relationship_index,
)


class TestSharedNeighborsBeyondCommonParent:
    def test_airbus_blob_negative_fixture_no_boost_from_shared_parent_alone(self) -> None:
        # Airbus SE and Airbus Helicopters both assert a relation to
        # "Airbus Group" (their common parent) and nothing else -- exactly
        # the audit's own f62c560d failure shape. Sharing ONLY the parent
        # must not count as agreement.
        rows = [
            {
                "companyid": "airbus-se",
                "relatedcompanyid": "airbus-group",
                "relationtype": "SUBSIDIARY_OF",
            },
            {
                "companyid": "airbus-helicopters",
                "relatedcompanyid": "airbus-group",
                "relationtype": "SUBSIDIARY_OF",
            },
        ]
        index = build_relationship_index(rows)
        rec1 = {"companyID": "airbus-se", "name": "Airbus SE"}
        rec2 = {"companyID": "airbus-helicopters", "name": "Airbus Helicopters"}
        shared = _shared_neighbors_beyond_common_parent(index, rec1, rec2)
        assert shared == set()
        assert _relational_agreement(rec1, rec2, index) == 0.0

    def test_two_or_more_shared_neighbors_beyond_the_parent_do_count(self) -> None:
        rows = [
            {"companyid": "a", "relatedcompanyid": "parent"},
            {"companyid": "b", "relatedcompanyid": "parent"},
            {"companyid": "a", "relatedcompanyid": "joint-venture-x"},
            {"companyid": "b", "relatedcompanyid": "joint-venture-x"},
        ]
        index = build_relationship_index(rows)
        rec1 = {"companyID": "a"}
        rec2 = {"companyID": "b"}
        shared = _shared_neighbors_beyond_common_parent(index, rec1, rec2)
        assert shared == {"parent", "joint-venture-x"}

    def test_no_shared_neighbors_at_all(self) -> None:
        index = build_relationship_index(
            [
                {"companyid": "a", "relatedcompanyid": "x"},
                {"companyid": "b", "relatedcompanyid": "y"},
            ]
        )
        assert (
            _shared_neighbors_beyond_common_parent(index, {"companyID": "a"}, {"companyID": "b"})
            == set()
        )

    def test_a_record_with_no_known_id_gets_no_boost(self) -> None:
        index = build_relationship_index([{"companyid": "a", "relatedcompanyid": "b"}])
        assert _relational_agreement({"name": "no id here"}, {"companyID": "a"}, index) == 0.0


class TestRelationalAgreementIsBoundedAndAdditive:
    def test_boost_is_bounded_at_point_three(self) -> None:
        rows = [{"companyid": "a", "relatedcompanyid": f"n{i}"} for i in range(10)] + [
            {"companyid": "b", "relatedcompanyid": f"n{i}"} for i in range(10)
        ]
        index = build_relationship_index(rows)
        boost = _relational_agreement({"companyID": "a"}, {"companyID": "b"}, index)
        assert boost == 0.3

    def test_boost_alone_can_never_cross_auto_match(self) -> None:
        from src.mcp_server.tools.resolve_entities import _AUTO_MATCH

        rows = [{"companyid": "a", "relatedcompanyid": f"n{i}"} for i in range(20)] + [
            {"companyid": "b", "relatedcompanyid": f"n{i}"} for i in range(20)
        ]
        index = build_relationship_index(rows)
        boost = _relational_agreement({"companyID": "a"}, {"companyID": "b"}, index)
        assert boost < _AUTO_MATCH

    def test_exactly_two_shared_neighbors_gives_the_expected_bounded_value(self) -> None:
        rows = [
            {"companyid": "a", "relatedcompanyid": "n1"},
            {"companyid": "b", "relatedcompanyid": "n1"},
            {"companyid": "a", "relatedcompanyid": "n2"},
            {"companyid": "b", "relatedcompanyid": "n2"},
        ]
        index = build_relationship_index(rows)
        boost = _relational_agreement({"companyID": "a"}, {"companyID": "b"}, index)
        assert boost == 0.3  # min(0.15 * 2, 0.3)
