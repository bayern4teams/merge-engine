"""DSPy module and signature for field mapping stage.

Outputs list[FieldDecision] internally, reshaped to a bare dynamically-keyed object
{source_field: {action, dest_column, ...}} at the module boundary.
"""

from __future__ import annotations

import json
from typing import Any

import dspy
from pydantic import BaseModel, Field


class FieldDecision(BaseModel):
    source_field: str = Field(description="Name of the source field being mapped")
    action: str = Field(description="Action: 'map' or 'skip'")
    dest_column: str | None = Field(
        default=None,
        description="Target destination column name when action is 'map'",
    )
    reason: str | None = Field(
        default=None,
        description="Optional reason for skipping",
    )


class FieldMappingOutput(BaseModel):
    decisions: list[FieldDecision] = Field(
        default_factory=list,
        description="List of field mapping decisions for each source field",
    )


class FieldMappingSignature(dspy.Signature):
    """Field mapping stage signature.

    Map unmapped source fields to authoritative destination table schema columns.
    """

    entity_type: str = dspy.InputField(desc="Target entity type name (e.g. company, person)")
    dest_table: str = dspy.InputField(desc="Target destination table name")
    destination_schema: str = dspy.InputField(desc="Destination table schema JSON string")
    source_fields: str = dspy.InputField(desc="Source fields to map JSON string")
    output: FieldMappingOutput = dspy.OutputField(
        desc="List of FieldDecision items for all source fields"
    )


class FieldMappingModule(dspy.Module):
    """DSPy module for field mapping stage."""

    def __init__(self) -> None:
        super().__init__()
        self.predictor = dspy.Predict(FieldMappingSignature)

    def forward(
        self,
        entity_type: str,
        dest_table: str,
        destination_schema: list[dict[str, Any]] | str,
        fields: list[dict[str, Any]] | str,
    ) -> dict[str, dict[str, Any]]:
        dest_schema_str = (
            json.dumps(destination_schema)
            if not isinstance(destination_schema, str)
            else destination_schema
        )
        fields_str = json.dumps(fields) if not isinstance(fields, str) else fields

        result = self.predictor(
            entity_type=entity_type,
            dest_table=dest_table,
            destination_schema=dest_schema_str,
            source_fields=fields_str,
        )

        out = result.output
        decisions: list[FieldDecision] = []
        if isinstance(out, FieldMappingOutput):
            decisions = out.decisions
        elif isinstance(out, dict) and "decisions" in out:
            decisions = [FieldDecision(**d) for d in out["decisions"]]
        elif isinstance(out, list):
            decisions = [FieldDecision(**d) if isinstance(d, dict) else d for d in out]

        mapping_result: dict[str, dict[str, Any]] = {}
        for d in decisions:
            val: dict[str, Any] = {"action": d.action}
            if d.action == "map" and d.dest_column:
                val["dest_column"] = d.dest_column
            elif d.reason:
                val["reason"] = d.reason
            mapping_result[d.source_field] = val

        return mapping_result
