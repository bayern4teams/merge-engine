"""Phase 3.3: get_initial_state's subject-first mode.

The UI's default company-name box sends `subject` directly rather than
relying on parse_request's LLM call to extract a scope from free text (see
tests/unit/test_parse_request_node.py::TestSubjectFirstMode for how
parse_request itself then treats this as authoritative).
"""

from __future__ import annotations

import pytest

from src.orchestrator.graph import get_initial_state


class TestSubjectFirstMode:
    def test_subject_activates_merge_scope(self) -> None:
        state = get_initial_state(
            "Merge all information about Airbus.",
            origins=[{"id": "o1"}],
            destination={"type": "postgresql"},
            subject="Airbus",
        )
        assert state["merge_scope"] == {
            "active": True,
            "subject": "Airbus",
            "scope_type": "company",
        }

    def test_subject_is_stripped(self) -> None:
        state = get_initial_state(
            "merge",
            origins=[{"id": "o1"}],
            destination={"type": "postgresql"},
            subject="  Airbus  ",
        )
        assert state["merge_scope"]["subject"] == "Airbus"

    def test_no_subject_leaves_scope_inactive(self) -> None:
        state = get_initial_state(
            "Merge all companies and people from both origins.",
            origins=[{"id": "o1"}],
            destination={"type": "postgresql"},
        )
        assert state["merge_scope"] == {"active": False, "subject": "", "scope_type": "none"}

    def test_blank_subject_leaves_scope_inactive(self) -> None:
        """An empty/whitespace-only subject (e.g. Advanced mode sending
        subject=None, or a stray empty string) must not spuriously activate
        scoping -- the free-text path must be unaffected."""
        state = get_initial_state(
            "merge",
            origins=[{"id": "o1"}],
            destination={"type": "postgresql"},
            subject="   ",
        )
        assert state["merge_scope"] == {"active": False, "subject": "", "scope_type": "none"}

    def test_subject_without_origins_or_config_still_requires_one(self) -> None:
        """subject is not a substitute for the function's existing required
        shape -- it only ever applies inside the UI branch (origins and
        destination both given). Confirms adding the parameter didn't
        accidentally open a new no-args code path."""
        with pytest.raises(ValueError, match="Must provide either"):
            get_initial_state("merge", subject="Airbus")
