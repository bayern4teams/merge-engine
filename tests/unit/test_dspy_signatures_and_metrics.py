"""Unit tests for DSPy signatures, modules, and metrics (B1.2 and B1.3)."""

import json
from pathlib import Path

import pytest

from src.llm.field_mapping_dspy import FieldDecision, FieldMappingModule, FieldMappingOutput
from src.llm.metrics import field_mapping_metric, request_parsing_metric
from src.llm.request_parsing_dspy import RequestParsingModule, RequestParsingOutput
from tests.prompts.harness import _score

GOLDEN_DIR = Path(__file__).parent.parent / "prompts" / "golden"


class TestRequestParsingSignatureAndModule:
    """Test RequestParsingModule structure and output contract."""

    def test_output_schema_keys(self):
        output = RequestParsingOutput(
            entity_types=["company"],
            explicit_entity_types=["company"],
            subject="Nova",
            scope_type="company",
        )
        data = output.model_dump()
        assert "entity_types" in data
        assert "explicit_entity_types" in data
        assert "subject" in data
        assert "scope_type" in data

    def test_golden_cases_load(self):
        path = GOLDEN_DIR / "request_parsing.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["cases"]) == 14


class TestFieldMappingSignatureAndModule:
    """Test FieldMappingModule reshape logic and output contract."""

    def test_reshaping_list_decisions_to_bare_object(self):
        module = FieldMappingModule()
        out = FieldMappingOutput(
            decisions=[
                FieldDecision(source_field="org_title", action="map", dest_column="name"),
                FieldDecision(source_field="esg_score", action="skip", reason="noise"),
            ]
        )

        class MockPredictor:
            def __call__(self, **kwargs):
                class MockResult:
                    output = out

                return MockResult()

        module.predictor = MockPredictor()
        res = module.forward("company", "companies", [], [])
        assert res == {
            "org_title": {"action": "map", "dest_column": "name"},
            "esg_score": {"action": "skip", "reason": "noise"},
        }

    def test_golden_cases_load(self):
        path = GOLDEN_DIR / "field_mapping.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["cases"]) == 9


class TestMetricParityWithHarness:
    """Test that DSPy metric functions match harness _score outputs."""

    def test_request_parsing_metric_matches_harness(self):
        golden_file = GOLDEN_DIR / "request_parsing.json"
        cases = json.loads(golden_file.read_text(encoding="utf-8"))["cases"]

        for case in cases:
            mock_pred = dict(case["expected"])
            harness_ok, _ = _score("request_parsing", case, mock_pred)
            metric_score = request_parsing_metric(case, mock_pred)

            assert (metric_score == 1.0) == harness_ok

    def test_field_mapping_metric_matches_harness(self):
        golden_file = GOLDEN_DIR / "field_mapping.json"
        cases = json.loads(golden_file.read_text(encoding="utf-8"))["cases"]

        for case in cases:
            mock_pred = dict(case["expected"])
            harness_ok, _ = _score("field_mapping", case, mock_pred)
            metric_score = field_mapping_metric(case, mock_pred)

            assert (metric_score == 1.0) == harness_ok
