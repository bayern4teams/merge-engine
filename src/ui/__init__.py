"""UI modules for merge-engine.

Provides Gradio-based interfaces for human-in-the-loop review
throughout the merge workflow.
"""

from __future__ import annotations

from src.ui.gradio_app import create_app
from src.ui.components.dependency_editor import (
    render_dependency_editor,
    create_dependency_editor_block,
)
from src.ui.components.match_reviewer import (
    render_match_reviewer,
    create_match_reviewer_block,
)
from src.ui.components.mapping_reviewer import (
    render_mapping_reviewer,
    create_mapping_reviewer_block,
)
from src.ui.components.summary_view import (
    render_final_summary,
    create_summary_view_block,
)

__all__ = [
    "create_app",
    "render_dependency_editor",
    "create_dependency_editor_block",
    "render_match_reviewer",
    "create_match_reviewer_block",
    "render_mapping_reviewer",
    "create_mapping_reviewer_block",
    "render_final_summary",
    "create_summary_view_block",
]