"""
Gradio UI definition and integration for merge-engine.

Provides a web-based chat interface with dynamic panels for each
human-in-the-loop review stage.
"""

from __future__ import annotations

import logging
from typing import Any

import gradio as gr

from src.ui.components.dependency_editor import render_dependency_editor
from src.ui.components.match_reviewer import render_match_reviewer
from src.ui.components.mapping_reviewer import render_mapping_reviewer
from src.ui.components.summary_view import render_final_summary
from src.orchestrator.workflow_manager import WorkflowManager

logger = logging.getLogger("merge-engine.ui.gradio_app")


class ChatState:
    """Per-session chat state for the Gradio UI."""

    def __init__(self) -> None:
        self.thread_id: str | None = None
        self.status: str = "idle"  # idle, running, interrupted, completed, failed, aborted
        self.last_log: str = ""


# Global workflow manager (initialized on app start)
workflow_manager: WorkflowManager | None = None


def create_app(
    config_path: str = "config/config.yaml",
    server_name: str = "0.0.0.0",
    server_port: int = 7860,
) -> gr.Blocks:
    """Create the Gradio application.

    Parameters
    ----------
    config_path : str
        Path to config.yaml.
    server_name : str
        Bind address.
    server_port : int
        Port to listen on.

    Returns
    -------
    gr.Blocks
        The Gradio Blocks app instance.
    """
    global workflow_manager

    # Initialize workflow manager
    workflow_manager = WorkflowManager()
    logger.info("Initialized WorkflowManager")

    # Initialize chat state (stored in session)
    chat_history: list[list[str | None]] = []
    system_messages: list[str] = []

    def log_message(msg: str) -> str:
        """Append a system message to the chat log."""
        system_messages.append(msg)
        return "\n".join(system_messages[-20:])  # Last 20 messages

    def start_workflow(user_request: str, chat_state: dict) -> tuple[list, str, dict, dict, dict, dict]:
        """Handle user starting a new merge workflow."""
        global workflow_manager
        if not user_request or not user_request.strip():
            return (
                chat_history + [["", "Please enter a request."]],
                "",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
            )

        if workflow_manager is None:
            return (
                chat_history + [["", "Workflow manager not initialized."]],
                "",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
            )

        # Start workflow
        thread_id = workflow_manager.start_workflow(user_request, config_path)
        chat_state["thread_id"] = thread_id
        chat_state["status"] = "running"

        response = (
            chat_history
            + [[user_request, None]]
            + [[None, f"Workflow started (`{thread_id}`). Analyzing request..."]]
        )

        return (
            response,
            "",
            gr.update(visible=False),  # dependency panel
            gr.update(visible=False),  # match panel
            gr.update(visible=False),  # mapping panel
            gr.update(visible=False),  # summary panel
        )

    def poll_status(chat_state: dict) -> tuple[str, dict, dict, dict, dict, str, str]:
        """Poll workflow status and update panels if interrupted."""
        global workflow_manager
        if not workflow_manager or chat_state.get("status") in ("idle", "completed", "failed", "aborted"):
            return (
                "\n".join(system_messages[-20:]),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                "",
                "disabled",
            )

        thread_id = chat_state.get("thread_id")
        if not thread_id:
            return (
                "\n".join(system_messages[-20:]),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                "",
                "disabled",
            )

        status = workflow_manager.get_workflow_status(thread_id)
        wf_status = status.get("status", "running")
        interrupt_data = status.get("interrupt_data")

        # Update chat state
        chat_state["status"] = wf_status

        # Update status message
        status_messages = {
            "running": "Workflow running...",
            "interrupted": "Review required.",
            "completed": "Workflow completed successfully.",
            "failed": "Workflow failed.",
            "aborted": "Workflow aborted.",
        }
        status_msg = status_messages.get(wf_status, "Running...")
        system_messages.append(status_msg)

        # Build dependency panel
        dep_panel = gr.update(visible=False)
        match_panel = gr.update(visible=False)
        mapping_panel = gr.update(visible=False)
        summary_panel = gr.update(visible=False)
        resume_btn_state = "disabled"

        if interrupt_data:
            node = interrupt_data.get("node", "")
            data = interrupt_data.get("data", {})

            if node == "human_review_deps":
                dep_panel = gr.update(
                    value=render_dependency_editor(
                        data.get("dependency_tree", {}),
                        data.get("extraction_order", []),
                    ),
                    visible=True,
                )
                resume_btn_state = "enabled"

            elif node == "human_review_matches":
                clusters = data.get("low_confidence_clusters", [])
                match_panel = gr.update(
                    value=render_match_reviewer(clusters),
                    visible=True,
                )
                resume_btn_state = "enabled"

            elif node == "human_review_mappings":
                unmapped = data.get("unmapped_fields", [])
                schema_changes = data.get("pending_schema_changes", [])
                mapping_panel = gr.update(
                    value=render_mapping_reviewer(unmapped, schema_changes),
                    visible=True,
                )
                resume_btn_state = "enabled"

            elif node == "human_final_approval":
                summary = data.get("summary", {})
                schema_changes = data.get("pending_schema_changes", [])
                summary_panel = gr.update(
                    value=render_final_summary(summary, schema_changes),
                    visible=True,
                )
                resume_btn_state = "enabled"

            elif node == "handle_error":
                error = data.get("error", "Unknown error")
                system_messages.append(f"ERROR: {error}")
                chat_state["status"] = "failed"

        return (
            "\n".join(system_messages[-20:]),
            dep_panel,
            match_panel,
            mapping_panel,
            summary_panel,
            chat_state.get("thread_id", ""),
            resume_btn_state,
        )

    def submit_feedback(
        decision: str,
        chat_state: dict,
    ) -> tuple[dict, dict, dict, dict, str, str]:
        """Handle human feedback from interrupt review panels."""
        global workflow_manager
        thread_id = chat_state.get("thread_id")

        if not workflow_manager or not thread_id:
            return (
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                "",
                "disabled",
            )

        # Build feedback based on which panel was active
        # The decision comes from which button was clicked
        feedback: dict[str, Any] = {}

        # Check which panel was visible (by checking chat_state)
        status = chat_state.get("status", "")

        if status == "interrupted":
            # Default feedback: approve
            feedback = {"approved": True}

            # Resume the workflow
            workflow_manager.resume_workflow(thread_id, feedback)
            system_messages.append(f"Feedback submitted for workflow {thread_id}")

        return (
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "disabled",
        )

    def abort_workflow(chat_state: dict) -> tuple[dict, dict, dict, dict, str, str]:
        """Abort the current workflow."""
        global workflow_manager
        thread_id = chat_state.get("thread_id")

        if workflow_manager and thread_id:
            workflow_manager.abort_workflow(thread_id)
            chat_state["status"] = "aborted"
            system_messages.append(f"Workflow {thread_id} aborted.")

        return (
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "disabled",
        )

    # ── Build Gradio Blocks ─────────────────────────────────────────
    with gr.Blocks(title="Merge Engine", css=".gradio-container{max-width: 900px}") as app:
        chat_state = gr.State({})

        gr.Markdown("# 🔀 Merge Engine")
        gr.Markdown("Natural language → fully migrated, deduplicated, and unified entities.")

        with gr.Row():
            with gr.Column(scale=3):
                request_input = gr.Textbox(
                    label="Your Request",
                    placeholder="e.g., Import all companies and their contacts",
                    lines=2,
                )
                submit_btn = gr.Button("🚀 Start Merge", variant="primary")
                abort_btn = gr.Button("⏹ Abort", variant="secondary", size="sm")

            with gr.Column(scale=1):
                status_output = gr.Textbox(
                    label="Status",
                    interactive=False,
                    lines=1,
                )

        chat_output = gr.Textbox(
            label="Activity Log",
            interactive=False,
            lines=10,
            autoscroll=True,
        )

        # Interrupt panels (initially hidden)
        with gr.Accordion("Dependency Review", open=False) as dep_accordion:
            dep_panel = gr.HTML(visible=False)
            with gr.Row():
                dep_approve = gr.Button("✅ Approve Dependencies", variant="stop")
                dep_reject = gr.Button("❌ Reject", variant="secondary")

        with gr.Accordion("Entity Match Review", open=False) as match_accordion:
            match_panel = gr.HTML(visible=False)
            with gr.Row():
                match_approve = gr.Button("✅ Approve Matches", variant="stop")
                match_reject = gr.Button("❌ Reject", variant="secondary")

        with gr.Accordion("Field Mapping Review", open=False) as mapping_accordion:
            mapping_panel = gr.HTML(visible=False)
            with gr.Row():
                mapping_approve = gr.Button("✅ Approve Mappings", variant="stop")
                mapping_reject = gr.Button("❌ Reject", variant="secondary")

        with gr.Accordion("Final Approval", open=False) as summary_accordion:
            summary_panel = gr.HTML(visible=False)
            with gr.Row():
                summary_approve = gr.Button("✅ Approve & Load", variant="stop")
                summary_reject = gr.Button("❌ Abort", variant="secondary")

        # ── Event handlers ──────────────────────────────────────────
        submit_btn.click(
            fn=start_workflow,
            inputs=[request_input, chat_state],
            outputs=[
                chat_output,
                request_input,
                dep_panel,
                match_panel,
                mapping_panel,
                summary_panel,
            ],
        ).then(
            fn=poll_status,
            inputs=[chat_state],
            outputs=[
                chat_output,
                dep_panel,
                match_panel,
                mapping_panel,
                summary_panel,
                status_output,
                submit_btn,
            ],
        )

        # Poll ticker (every 2 seconds)
        ticker = gr.Ticker(value=2000, period=2)
        ticker.tick(
            fn=poll_status,
            inputs=[chat_state],
            outputs=[
                chat_output,
                dep_panel,
                match_panel,
                mapping_panel,
                summary_panel,
                status_output,
                submit_btn,
            ],
        )

        # Approve buttons on each panel
        for btn, panel, accordion in [
            (dep_approve, dep_panel, dep_accordion),
            (match_approve, match_panel, match_accordion),
            (mapping_approve, mapping_panel, mapping_accordion),
            (summary_approve, summary_panel, summary_accordion),
        ]:
            btn.click(
                fn=lambda state=chat_state: submit_feedback("approve", state),
                inputs=[state],
                outputs=[dep_panel, match_panel, mapping_panel, summary_panel],
            ).then(
                fn=poll_status,
                inputs=[chat_state],
                outputs=[
                    chat_output,
                    dep_panel,
                    match_panel,
                    mapping_panel,
                    summary_panel,
                    status_output,
                    submit_btn,
                ],
            )

        # Reject buttons
        for btn, panel, accordion in [
            (dep_reject, dep_panel, dep_accordion),
            (match_reject, match_panel, match_accordion),
            (mapping_reject, mapping_panel, mapping_accordion),
            (summary_reject, summary_panel, summary_accordion),
        ]:
            btn.click(
                fn=lambda state=chat_state: submit_feedback("reject", state),
                inputs=[state],
                outputs=[dep_panel, match_panel, mapping_panel, summary_panel],
            ).then(
                fn=abort_workflow,
                inputs=[chat_state],
                outputs=[dep_panel, match_panel, mapping_panel, summary_panel],
            )

        # Abort button
        abort_btn.click(
            fn=abort_workflow,
            inputs=[chat_state],
            outputs=[dep_panel, match_panel, mapping_panel, summary_panel],
        )

    return app