"""
Tests for src/ui/components/origin_loader.py - Dynamic origin database loader UI component.
"""

import pytest
from src.ui.components.origin_loader import (
    reset_origin_counter,
    get_next_origin_id,
    collect_origin_values,
    get_origin_configs,
)


class TestOriginCounter:
    """Tests for origin counter functions."""

    def setup_method(self):
        """Reset counter before each test."""
        reset_origin_counter()

    def teardown_method(self):
        """Clean up after each test."""
        reset_origin_counter()

    def test_initial_counter_is_zero(self):
        """Initial origin ID should be origin_1."""
        reset_origin_counter()
        assert get_next_origin_id() == "origin_1"

    def test_sequential_ids(self):
        """Sequential calls should produce sequential IDs."""
        reset_origin_counter()
        id1 = get_next_origin_id()
        id2 = get_next_origin_id()
        id3 = get_next_origin_id()
        assert id1 == "origin_1"
        assert id2 == "origin_2"
        assert id3 == "origin_3"

    def test_reset_clears_counter(self):
        """Reset should restart the counter."""
        reset_origin_counter()
        id1 = get_next_origin_id()
        reset_origin_counter()
        id2 = get_next_origin_id()
        assert id1 == "origin_1"
        assert id2 == "origin_1"

    def test_counter_returns_strings(self):
        """Origin IDs should be strings."""
        reset_origin_counter()
        result = get_next_origin_id()
        assert isinstance(result, str)
        assert result.startswith("origin_")


class TestCollectOriginValues:
    """Tests for collect_origin_values() function."""

    def test_collect_valid_origins(self):
        """Should collect valid origin configurations."""
        origins = collect_origin_values(
            indices=[0, 1],
            types=["postgres", "mongodb"],
            hosts=["db1.example.com", "db2.example.com"],
            ports=[5432, 27017],
            users=["admin", "reader"],
            passwords=["secret", "pass123"],
            databases=["crm", "analytics"],
            priorities=[10, 20],
        )
        assert len(origins) == 2
        assert origins[0]["id"] == "origin_1"
        assert origins[0]["type"] == "postgres"
        assert origins[0]["connection"]["host"] == "db1.example.com"
        assert origins[0]["connection"]["port"] == 5432
        assert origins[0]["connection"]["database"] == "crm"
        assert origins[0]["priority"] == 10

        assert origins[1]["type"] == "mongodb"
        assert origins[1]["connection"]["port"] == 27017

    def test_skip_incomplete_entries(self):
        """Should skip entries with missing required fields."""
        origins = collect_origin_values(
            indices=[0],
            types=["postgres"],
            hosts=[""],  # Missing host
            ports=[5432],
            users=["admin"],
            passwords=["secret"],
            databases=["db"],
            priorities=[0],
        )
        assert len(origins) == 0

    def test_skip_entries_missing_password(self):
        """Should skip entries with missing password."""
        origins = collect_origin_values(
            indices=[0],
            types=["postgres"],
            hosts=["localhost"],
            ports=[5432],
            users=["admin"],
            passwords=[""],  # Missing password
            databases=["db"],
            priorities=[0],
        )
        assert len(origins) == 0

    def test_empty_indices(self):
        """Empty indices should produce empty list."""
        origins = collect_origin_values(
            indices=[],
            types=[],
            hosts=[],
            ports=[],
            users=[],
            passwords=[],
            databases=[],
            priorities=[],
        )
        assert origins == []

    def test_multiple_origins(self):
        """Should handle multiple origins correctly."""
        origins = collect_origin_values(
            indices=[0, 1, 2],
            types=["postgres", "postgres", "mongodb"],
            hosts=["host1", "host2", "host3"],
            ports=[5432, 5433, 27017],
            users=["user1", "user2", "user3"],
            passwords=["pass1", "pass2", "pass3"],
            databases=["db1", "db2", "db3"],
            priorities=[0, 10, 20],
        )
        assert len(origins) == 3
        assert all("connection" in o for o in origins)
        assert all("id" in o for o in origins)
        assert all("type" in o for o in origins)

    def test_default_port_when_zero(self):
        """Port 0 should default to 5432."""
        origins = collect_origin_values(
            indices=[0],
            types=["postgres"],
            hosts=["localhost"],
            ports=[0],
            users=["admin"],
            passwords=["secret"],
            databases=["db"],
            priorities=[0],
        )
        assert origins[0]["connection"]["port"] == 5432

    def test_default_priority_when_zero(self):
        """Priority 0 should default to 0 (not None)."""
        origins = collect_origin_values(
            indices=[0],
            types=["postgres"],
            hosts=["localhost"],
            ports=[5432],
            users=["admin"],
            passwords=["secret"],
            databases=["db"],
            priorities=[0],
        )
        assert origins[0]["priority"] == 0


class TestGetOriginConfigs:
    """Tests for get_origin_configs() function."""

    def setup_method(self):
        """Reset counter before each test."""
        reset_origin_counter()

    def teardown_method(self):
        """Clean up after each test."""
        reset_origin_counter()

    def test_returns_empty_when_no_widgets(self):
        """Should return empty list when no widgets exist."""
        reset_origin_counter()
        configs = get_origin_configs()
        assert configs == []