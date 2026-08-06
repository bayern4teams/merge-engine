"""DSPy module and signature for request parsing.

Parses natural language merge requests into entity types, explicit entity types,
subject organization name, and scope type.
"""

from __future__ import annotations

from typing import Any

import dspy
from pydantic import BaseModel, Field


class RequestParsingOutput(BaseModel):
    entity_types: list[str] = Field(
        default_factory=list,
        description="Extracted target entity types from allowed vocabulary",
    )
    explicit_entity_types: list[str] = Field(
        default_factory=list,
        description="Subset of entity_types explicitly requested or named",
    )
    subject: str = Field(
        default="",
        description="Target organization name or empty string if none",
    )
    scope_type: str = Field(
        default="none",
        description="Must be 'company' if single target organization named, else 'none'",
    )


class RequestParsingSignature(dspy.Signature):
    """Parse natural language merge request into target entity types and scope.

    Use ONLY allowed entity types: person, company, product, country, address, account,
    order, finance, industry, location, experience, education, skill, technology,
    technology_category, contact, company_relation, contract.
    """

    user_request: str = dspy.InputField(desc="User natural language request string")
    output: RequestParsingOutput = dspy.OutputField(
        desc="Structured output containing entity_types, explicit_entity_types, subject, scope_type"
    )


class RequestParsingModule(dspy.Module):
    """DSPy module for request parsing stage."""

    def __init__(self) -> None:
        super().__init__()
        self.predictor = dspy.Predict(RequestParsingSignature)

    def forward(self, user_request: str) -> dict[str, Any]:
        result = self.predictor(user_request=user_request)
        out = result.output
        if isinstance(out, RequestParsingOutput):
            res_dict = out.model_dump()
        elif isinstance(out, dict):
            res_dict = out
        else:
            res_dict = {
                "entity_types": getattr(out, "entity_types", []),
                "explicit_entity_types": getattr(out, "explicit_entity_types", []),
                "subject": getattr(out, "subject", ""),
                "scope_type": getattr(out, "scope_type", "none"),
            }
        return res_dict
