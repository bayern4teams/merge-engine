"""
Dependency tree editor component for Gradio UI.

Renders the dependency tree as an interactive panel where the user
can approve, modify, or add/remove edges.
"""

from __future__ import annotations

import gradio as gr
import json
from typing import Any


def render_dependency_editor(
    dependency_tree: dict[str, list[str]],
    extraction_order: list[str],
) -> gr.HTML:
    """Render the dependency tree for human review.

    Parameters
    ----------
    dependency_tree : dict
        {entity_type: [prerequisite_entity_types]}
    extraction_order : list
        Topologically sorted entity types.

    Returns
    -------
    gr.HTML
        Gradio component with the dependency tree visualization.
    """
    # Build an HTML tree representation
    html_parts = ['<div style="max-width: 600px; margin: 20px auto;">']
    html_parts.append('<h3>Entity Dependency Tree</h3>')
    html_parts.append('<p>Review and modify the extraction order below:</p>')

    # Display extraction order
    html_parts.append('<h4>Extraction Order</h4>')
    html_parts.append('<ol>')
    for i, entity in enumerate(extraction_order, 1):
        deps = dependency_tree.get(entity, [])
        if deps:
            html_parts.append(f'<li><strong>{entity}</strong> → depends on: {", ".join(deps)}</li>')
        else:
            html_parts.append(f'<li><strong>{entity}</strong> → no dependencies (extract first)</li>')
    html_parts.append('</ol>')

    # Display dependency matrix
    html_parts.append('<h4>Dependency Matrix</h4>')
    html_parts.append('<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">')
    html_parts.append('<tr><th>Entity</th><th>Depends On</th><th>Extracted</th></tr>')
    for entity in extraction_order:
        deps = dependency_tree.get(entity, [])
        all_deps_extracted = all(d in extraction_order for d in deps)
        status = "✓" if all_deps_extracted else "✗"
        html_parts.append(
            f'<tr><td>{entity}</td><td>{", ".join(deps) if deps else "—"}'
            f'</td><td>{status}</td></tr>'
        )
    html_parts.append('</table>')

    html_parts.append('</div>')

    return gr.HTML(value="\n".join(html_parts))


def create_dependency_editor_block() -> gr.Blocks:
    """Create a reusable Gradio block for dependency tree editing.

    Returns
    -------
    gr.Blocks
        Gradio component block.
    """
    with gr.Blocks() as block:
        gr.Markdown("### Dependency Tree Review")
        dependency_html = gr.HTML(label="Dependency Tree")
        approve_btn = gr.Button("✅ Approve", variant="stop", size="lg")
        reject_btn = gr.Button("❌ Reject", variant="secondary", size="lg")

    return block