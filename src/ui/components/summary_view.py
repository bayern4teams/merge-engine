"""
Summary view component for Gradio UI.

Renders the final approval summary showing entity counts, conflicts,
schema changes, and errors.
"""

from __future__ import annotations

import gradio as gr
from typing import Any


def render_final_summary(
    summary: dict[str, Any],
    pending_schema_changes: list[dict[str, str]],
) -> gr.HTML:
    """Render the final approval summary panel.

    Parameters
    ----------
    summary : dict
        {total_entities, entity_counts, total_conflicts, schema_changes, errors}
    pending_schema_changes : list[dict]
        [{table, column, data_type}, ...]

    Returns
    -------
    gr.HTML
        Gradio component with final summary panel.
    """
    html_parts = ['<div style="max-width: 700px; margin: 20px auto; padding: 20px; '
                   'background: #f5f5f5; border-radius: 10px;">']
    html_parts.append('<h2 style="text-align: center;">Merge Summary</h2>')

    total_entities = summary.get("total_entities", 0)
    entity_counts = summary.get("entity_counts", {})
    total_conflicts = summary.get("total_conflicts", 0)
    schema_changes = summary.get("schema_changes", 0)
    errors = summary.get("errors", 0)

    html_parts.append('<table border="0" cellpadding="8" cellspacing="0" style="width: 100%;">')
    html_parts.append(
        f'<tr><td><strong>Total Entities</strong></td>'
        f'<td>{total_entities}</td></tr>'
    )

    # Entity counts
    html_parts.append('<tr><td><strong>Records per Entity</strong></td><td>')
    html_parts.append('<ul>')
    for entity, count in entity_counts.items():
        html_parts.append(f'<li>{entity}: {count} unified records</li>')
    html_parts.append('</ul></td></tr>')

    html_parts.append(
        f'<tr><td><strong>Total Conflicts</strong></td>'
        f'<td>{total_conflicts}</td></tr>'
    )
    html_parts.append(
        f'<tr><td><strong>Schema Changes</strong></td>'
        f'<td>{schema_changes} new column(s)</td></tr>'
    )
    html_parts.append(
        f'<tr><td><strong>Errors</strong></td>'
        f'<td style="color: red;">{errors}</td></tr>'
    )
    html_parts.append('</table>')

    # Schema changes detail
    if pending_schema_changes:
        html_parts.append('<h4>Schema Changes Detail</h4>')
        html_parts.append('<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">')
        html_parts.append('<tr><th>Table</th><th>Column</th><th>Type</th></tr>')
        for change in pending_schema_changes:
            html_parts.append(
                f'<tr><td>{change.get("table", "?")}</td>'
                f'<td>{change.get("column", "?")}</td>'
                f'<td>{change.get("data_type", "?")}</td></tr>'
            )
        html_parts.append('</table>')

    html_parts.append('<p style="margin-top: 20px; text-align: center;">')
    html_parts.append('Click <strong>Approve</strong> to proceed with loading,')
    html_parts.append('or <strong>Abort</strong> to cancel.</p>')
    html_parts.append('</div>')

    return gr.HTML(value="\n".join(html_parts))


def create_summary_view_block() -> gr.Blocks:
    """Create a reusable Gradio block for final summary.

    Returns
    -------
    gr.Blocks
        Gradio component block.
    """
    with gr.Blocks() as block:
        gr.Markdown("### Final Approval")
        summary_html = gr.HTML(label="Summary Panel")

        with gr.Row():
            approve_btn = gr.Button("✅ Approve & Load", variant="stop", size="lg")
            abort_btn = gr.Button("❌ Abort", variant="secondary", size="lg")

    return block