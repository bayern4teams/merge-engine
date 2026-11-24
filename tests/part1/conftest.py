"""Fixtures for the P21 live tier under tests/part1 (pytest loads conftest
automatically; the live tests never import fixture names as symbols)."""

from __future__ import annotations

from tests.part1.p21_live_harness import dest, itest_schema, sql  # noqa: F401
