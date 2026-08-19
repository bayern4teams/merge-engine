"""Column-name normalization shared across V3 discovery tools (Part B).

Originally written once, in `semantic_type_inference.py` (P-4), then found
live to be needed identically by `candidate_keys.py` (P-5) and
`relationship_scoring.py` (P-6): a plain snake_case-only identifier-shape
regex (`_id$`) silently misses camelCase columns like `companyID`, since
`companyid` (lowercased, no other change) has no underscore before `id` --
verified against real ClickHouse `origin2.Company.companyID` data, where
`score_identifier_likelihood` scored 0.1 instead of the 0.7-1.0 a plainly
ID-shaped column should get. Extracted here (`src/utils`, below every
`src/mcp_server/tools/` module in CLAUDE.md's layering) so all three module
call the same normalization instead of drifting three separate copies.
"""

from __future__ import annotations

import re


def normalize_column_name(name: str) -> str:
    """camelCase / PascalCase / already-snake_case -> lowercase snake_case,
    so `companyID`, `company_id`, and `CompanyId` all match the same rule."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()
