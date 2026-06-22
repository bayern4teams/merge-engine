"""
Match reviewer component for Gradio UI.

Renders ambiguous entity clusters side-by-side for human review.
"""

from __future__ import annotations

import gradio as gr
from typing import Any


def render_match_reviewer(
    low_confidence_clusters: list[dict[str, Any]],
) -> gr.HTML:
    """Render low-confidence entity match clusters for review.

    Parameters
    ----------
    low_confidence_clusters : list[dict]
        [{entity_type, cluster_id, record_ids, confidence}, ...]

    Returns
    -------
    gr.HTML
        Gradio component with match review panel.
    """
    html_parts = ['<div style="max-width: 800px; margin: 20px auto;">']
    html_parts.append('<h3>Entity Match Review</h3>')

    if not low_confidence_clusters:
        html_parts.append('<p>No ambiguous matches found. All clusters are clear.</p>')
    else:
        html_parts.append(f'<p>{len(low_confidence_clusters)} cluster(s) require review:</p>')

        for cluster in low_confidence_clusters:
            entity_type = cluster.get("entity_type", "?")
            cluster_id = cluster.get("cluster_id", "?")[:8]
            confidence = cluster.get("confidence", 0)

            html_parts.append(
                f'<details style="margin: 10px 0; padding: 10px; '
                f'border: 1px solid #ccc; border-radius: 5px;">'
            )
            html_parts.append(
                f'<summary><strong>{entity_type}</strong> '
                f'(confidence: {confidence:.2f})</summary>'
            )
            html_parts.append(f'<p>Cluster: <code>{cluster_id}</code></p>')
            html_parts.append(f'<p>Records: {", ".join(cluster.get("record_ids", []))}</p>')
            html_parts.append(
                '<p>Decision: Match / Not a match / Split manually</p>'
            )
            html_parts.append('</details>')

    html_parts.append('</div>')
    return gr.HTML(value="\n".join(html_parts))


def create_match_reviewer_block() -> gr.Blocks:
    """Create a reusable Gradio block for match reviewing.

    Returns
    -------
    gr.Blocks
        Gradio component block.
    """
    with gr.Blocks() as block:
        gr.Markdown("### Entity Match Review")
        match_html = gr.HTML(label="Match Review Panel")

        with gr.Row():
            approve_btn = gr.Button("✅ Approve All Matches", variant="stop", size="lg")
            reject_btn = gr.Button("❌ Reject All", variant="secondary", size="lg")

    return block