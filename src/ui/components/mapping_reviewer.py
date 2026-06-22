"""
Mapping reviewer component for Gradio UI.

Renders unmapped fields and schema changes for human review.
"""

from __future__ import annotations

import gradio as gr
from typing import Any


def render_mapping_reviewer(
    unmapped_fields: list[dict[str, Any]],
    pending_schema_changes: list[dict[str, str]],
) -> gr.HTML:
    """Render field mapping review panel.

    Parameters
    ----------
    unmapped_fields : list[dict]
        [{entity_type, source_field, reason, ...}, ...]
    pending_schema_changes : list[dict]
        [{table, column, data_type}, ...]

    Returns
    -------
    gr.HTML
        Gradio component with mapping review panel.
    """
    html_parts = ['<div style="max-width: 800px; margin: 20px auto;">']
    html_parts.append('<h3>Field Mapping Review</h3>')

    # Unmapped fields section
    html_parts.append('<h4>Unmapped Fields</h4>')
    if not unmapped_fields:
        html_parts.append('<p>All fields have been mapped successfully.</p>')
    else:
        html_parts.append(f'<p>{len(unmapped_fields)} field(s) require mapping:</p>')
        html_parts.append('<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">')
        html_parts.append('<tr><th>Entity</th><th>Source Field</th><th>Reason</th><th>Action</th></tr>')
        for field in unmapped_fields:
            html_parts.append(
                f'<tr><td>{field.get("entity_type", "?")}</td>'
                f'<td>{field.get("source_field", "?")}</td>'
                f'<td>{field.get("reason", "Unknown")}</td>'
                f'<td><select><option>Map to existing column</option>'
                f'<option>Discard</option></select></td></tr>'
            )
        html_parts.append('</table>')

    # Schema changes section
    html_parts.append('<h4>Proposed Schema Changes</h4>')
    if not pending_schema_changes:
        html_parts.append('<p>No schema changes needed.</p>')
    else:
        html_parts.append(f'<p>{len(pending_schema_changes)} new column(s) to be added:</p>')
        html_parts.append('<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">')
        html_parts.append('<tr><th>Table</th><th>Column</th><th>Data Type</th><th>Confirm</th></tr>')
        for change in pending_schema_changes:
            html_parts.append(
                f'<tr><td>{change.get("table", "?")}</td>'
                f'<td>{change.get("column", "?")}</td>'
                f'<td>{change.get("data_type", "?")}</td>'
                f'<td>✅ Add</td></tr>'
            )
        html_parts.append('</table>')

    html_parts.append('</div>')
    return gr.HTML(value="\n".join(html_parts))


def create_mapping_reviewer_block() -> gr.Blocks:
    """Create a reusable Gradio block for mapping review.

    Returns
    -------
    gr.Blocks
        Gradio component block.
    """
    with gr.Blocks() as block:
        gr.Markdown("### Field Mapping Review")
        mapping_html = gr.HTML(label="Mapping Review Panel")

        with gr.Row():
            approve_btn = gr.Button("✅ Approve Mappings", variant="stop", size="lg")
            reject_btn = gr.Button("❌ Reject", variant="secondary", size="lg")

    return block