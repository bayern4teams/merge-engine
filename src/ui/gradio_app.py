"""
Gradio UI definition and integration for merge-engine.

Provides a web-based chat interface with dynamic panels for each
human-in-the-loop review stage, plus a setup wizard for loading
origin and destination database connections.
"""

from __future__ import annotations

import logging
from typing import Any

import gradio as gr

from src.ui.components.dependency_editor import render_dependency_editor
from src.ui.components.match_reviewer import render_match_reviewer
from src.ui.components.mapping_reviewer import render_mapping_reviewer
from src.ui.components.summary_view import render_final_summary
from src.ui.components.origin_loader import (
    reset_origin_counter,
    collect_origin_values,
    render_origin_loader as _render_origin_loader,
)
from src.orchestrator.workflow_manager import WorkflowManager

logger = logging.getLogger("merge-engine.ui.gradio_app")


class ChatState:
    """Per-session chat state for the Gradio UI."""

    def __init__(self) -> None:
        self.thread_id: str | None = None
        self.status: str = "idle"  # idle, running, interrupted, completed, failed, aborted
        self.last_log: str = ""
        # Setup phase data
        self.origins: list[dict[str, Any]] = []
        self.destination: dict[str, Any] = {}
        self.setup_step: str = "origins"  # origins → destination → pipeline


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

    # =========================================================================
    # SETUP WIZARD FUNCTIONS
    # =========================================================================

    def add_origin_form_row() -> tuple:
        """Add a new origin database form row."""
        global _origin_count
        idx = _origin_count
        _origin_count += 1

        origin_type = gr.Dropdown(
            label=f"Origin {_origin_count} - Type",
            choices=["postgres", "mongodb"],
            value="postgres",
        )
        origin_host = gr.Textbox(
            label=f"Origin {_origin_count} - Host",
            placeholder="Host (IP or hostname)",
        )
        origin_port = gr.Number(
            label=f"Origin {_origin_count} - Port",
            value=5432,
        )
        origin_user = gr.Textbox(
            label=f"Origin {_origin_count} - Username",
            placeholder="Username",
        )
        origin_password = gr.Textbox(
            label=f"Origin {_origin_count} - Password",
            type="password",
            placeholder="Password",
        )
        origin_database = gr.Textbox(
            label=f"Origin {_origin_count} - Database",
            placeholder="Database name",
        )
        origin_priority = gr.Number(
            label=f"Origin {_origin_count} - Priority",
            value=0,
            minimum=0,
            maximum=100,
        )
        remove_btn = gr.Button(f"🗑 Remove Origin {_origin_count}", variant="secondary", size="sm")

        # Store references in a way Gradio can track
        return (
            origin_type, origin_host, origin_port, origin_user,
            origin_password, origin_database, origin_priority,
            gr.update(visible=True),  # show this row
        )

    def collect_origins_from_state(
        indices: list[int],
        types: list[str],
        hosts: list[str],
        ports: list[int],
        users: list[str],
        passwords: list[str],
        databases: list[str],
        priorities: list[int],
    ) -> list[dict[str, Any]]:
        """Collect origin configurations from submitted form data."""
        origins = []
        for i, idx in enumerate(indices):
            if not all([types[i], hosts[i], users[i], passwords[i], databases[i]]):
                continue

            origin_id = f"origin_{idx + 1}"
            origins.append({
                "id": origin_id,
                "type": types[i],
                "connection": {
                    "host": hosts[i],
                    "port": int(ports[i]) if ports[i] else 5432,
                    "user": users[i],
                    "password": passwords[i],
                    "database": databases[i],
                },
                "priority": int(priorities[i]) if priorities[i] else 0,
            })
        return origins

    def submit_origins(
        indices: list[int],
        types: list[str],
        hosts: list[str],
        ports: list[int],
        users: list[str],
        passwords: list[str],
        databases: list[str],
        priorities: list[int],
        chat_state: dict,
    ) -> tuple[str, dict, dict, dict]:
        """Submit origin configurations and move to destination step."""
        origins = collect_origins_from_state(indices, types, hosts, ports, users, passwords, databases, priorities)

        if not origins:
            return "⚠️ Please add at least one origin database with all fields filled.", chat_state, gr.update(), gr.update()

        chat_state["origins"] = origins
        chat_state["setup_step"] = "destination"

        summary = f"✅ **{len(origins)} origin(s) configured:**\n"
        for o in origins:
            c = o["connection"]
            summary += f"  - `{o['id']}` ({o['type']}) → {c['host']}:{c['port']}/{c['database']} (priority: {o['priority']})\n"
        summary += "\nNow configure the destination database."

        system_messages.append(summary)

        return (
            summary,
            chat_state,
            gr.update(visible=False),  # hide origins panel
            gr.update(visible=True),   # show destination panel
        )

    def submit_destination(
        dest_host: str,
        dest_port: int,
        dest_user: str,
        dest_password: str,
        dest_database: str,
        dest_schema: str,
        chat_state: dict,
    ) -> tuple[str, dict, dict]:
        """Submit destination configuration and move to pipeline step."""
        if not all([dest_host, dest_user, dest_password, dest_database]):
            return "⚠️ Please fill in all destination database fields.", chat_state, gr.update()

        dest_config = {
            "type": "postgresql",
            "host": dest_host,
            "port": int(dest_port) if dest_port else 5432,
            "user": dest_user,
            "password": dest_password,
            "database": dest_database,
            "schema": dest_schema or "public",
        }

        chat_state["destination"] = dest_config
        chat_state["setup_step"] = "pipeline"

        summary = f"✅ **Destination configured:**\n"
        summary += f"  - PostgreSQL → {dest_host}:{dest_port}/{dest_database}.{dest_schema or 'public'}\n"
        summary += f"  - Origins: {len(chat_state.get('origins', []))}\n"
        summary += "\nYou can now start the merge pipeline."

        system_messages.append(summary)

        return (
            summary,
            chat_state,
            gr.update(visible=False),  # hide destination panel
            gr.update(visible=True),   # show pipeline panel
        )

    # =========================================================================
    # PIPELINE FUNCTIONS
    # =========================================================================

    def start_workflow(user_request: str, chat_state: dict) -> tuple:
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
                "disabled",
            )

        # Validate setup is complete
        if not chat_state.get("origins"):
            return (
                chat_history + [["", "⚠️ Please configure origin databases first in the Setup tab."]],
                "",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                "disabled",
            )

        if not chat_state.get("destination"):
            return (
                chat_history + [["", "⚠️ Please configure the destination database first in the Setup tab."]],
                "",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                "disabled",
            )

        if workflow_manager is None:
            return (
                chat_history + [["", "Workflow manager not initialized."]],
                "",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                "disabled",
            )

        # Build state with origins and destination
        origins = chat_state["origins"]
        destination = chat_state["destination"]

        # Start workflow
        thread_id = workflow_manager.start_workflow(
            user_request,
            config_path,
            origins=origins,
            destination=destination,
        )
        chat_state["thread_id"] = thread_id
        chat_state["status"] = "running"

        response = (
            chat_history
            + [[user_request, None]]
            + [[None, f"Workflow started (`{thread_id}`). Analyzing request..."]]
        )

        system_messages.append(f"Workflow {thread_id} started with {len(origins)} origin(s).")

        return (
            response,
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            "disabled",
        )

    def poll_status(chat_state: dict) -> tuple:
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
    ) -> tuple:
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

        feedback: dict[str, Any] = {}

        if chat_state.get("status") == "interrupted":
            feedback = {"approved": True}
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

    def abort_workflow(chat_state: dict) -> tuple:
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

    # =========================================================================
    # BUILD GRADIO BLOCKS
    # =========================================================================

    # Global counters for origin form rows
    global _origin_count
    _origin_count = 0

    with gr.Blocks(title="Merge Engine", css=".gradio-container{max-width: 900px}") as app:
        chat_state = gr.State({})

        gr.Markdown("# 🔀 Merge Engine")
        gr.Markdown("Natural language → fully migrated, deduplicated, and unified entities into PostgreSQL.")

        # ── Tabs ──────────────────────────────────────────────────────
        with gr.Tabs():
            # ===== SETUP TAB =====
            with gr.Tab("⚙️ Setup"):
                gr.Markdown("Configure origin databases and destination before running the pipeline.")

                with gr.Accordion("1. Origin Databases", open=True) as origins_acc:
                    gr.Markdown("Add the source databases to merge from. You can add multiple PostgreSQL or MongoDB databases.")

                    add_origin_btn = gr.Button("➕ Add Origin Database", variant="primary")

                    origin_display = gr.Markdown("No origins added yet. Click 'Add Origin Database' to begin.")

                    # Origin form state
                    origin_indices = gr.State([])
                    origin_types = gr.State([])
                    origin_hosts = gr.State([])
                    origin_ports = gr.State([])
                    origin_users = gr.State([])
                    origin_passwords = gr.State([])
                    origin_databases = gr.State([])
                    origin_priorities = gr.State([])

                    # Dynamic origin entry containers (hidden until added)
                    origin_rows = []

                    with gr.Row():
                        submit_origins_btn = gr.Button("✅ Save Origins & Continue", variant="stop")

                    origins_output = gr.Textbox(
                        label="Loaded Origins",
                        interactive=False,
                        lines=5,
                        placeholder="Origins will appear here after saving...",
                    )

                with gr.Accordion("2. Destination Database", open=False) as dest_acc:
                    gr.Markdown("Configure the destination PostgreSQL database where unified data will be loaded.")

                    with gr.Row():
                        with gr.Column(scale=2):
                            dest_host = gr.Textbox(
                                label="Host (IP or hostname)",
                                placeholder="e.g., localhost or db.example.com",
                            )
                            dest_port = gr.Number(
                                label="Port",
                                value=5432,
                            )
                        with gr.Column(scale=1):
                            dest_schema = gr.Textbox(
                                label="Schema (default: public)",
                                value="public",
                            )

                    with gr.Row():
                        with gr.Column(scale=2):
                            dest_user = gr.Textbox(
                                label="Username",
                                placeholder="e.g., db_admin",
                            )
                            dest_password = gr.Textbox(
                                label="Password",
                                type="password",
                                placeholder="••••••••",
                            )
                        with gr.Column(scale=1):
                            dest_database = gr.Textbox(
                                label="Database Name",
                                placeholder="e.g., mergedb",
                            )

                    with gr.Row():
                        submit_dest_btn = gr.Button("✅ Save Destination & Continue", variant="primary")

                    dest_output = gr.Textbox(
                        label="Destination Config",
                        interactive=False,
                        lines=3,
                        placeholder="Destination configuration will appear here...",
                    )

                with gr.Accordion("3. Pipeline Ready", open=False) as pipeline_ready_acc:
                    gr.Markdown("All configuration is complete. Switch to the Pipeline tab to start merging.")
                    pipeline_status = gr.Textbox(
                        label="Status",
                        interactive=False,
                        value="✅ Ready to merge!",
                    )

                # ── Setup event handlers ──────────────────────────────────
                add_origin_btn.click(
                    fn=add_origin_form_row,
                    inputs=[],
                    outputs=origin_rows,  # Will be expanded dynamically
                )

                submit_origins_btn.click(
                    fn=submit_origins,
                    inputs=[
                        origin_indices, origin_types, origin_hosts, origin_ports,
                        origin_users, origin_passwords, origin_databases, origin_priorities,
                        chat_state,
                    ],
                    outputs=[origins_output, chat_state, dest_acc, pipeline_ready_acc],
                )

                submit_dest_btn.click(
                    fn=submit_destination,
                    inputs=[
                        dest_host, dest_port, dest_user, dest_password,
                        dest_database, dest_schema, chat_state,
                    ],
                    outputs=[dest_output, chat_state, pipeline_ready_acc],
                )

            # ===== PIPELINE TAB =====
            with gr.Tab("🚀 Pipeline"):
                gr.Markdown("Enter your natural language request and start the merge pipeline.")

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

                # ── Pipeline event handlers ──────────────────────────────
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
                        submit_btn,
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

                # Approve buttons
                for btn, panel in [
                    (dep_approve, dep_panel),
                    (match_approve, match_panel),
                    (mapping_approve, mapping_panel),
                    (summary_approve, summary_panel),
                ]:
                    btn.click(
                        fn=lambda s=chat_state: submit_feedback("approve", s),
                        inputs=[s],
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
                for btn, panel in [
                    (dep_reject, dep_panel),
                    (match_reject, match_panel),
                    (mapping_reject, mapping_panel),
                    (summary_reject, summary_panel),
                ]:
                    btn.click(
                        fn=lambda s=chat_state: submit_feedback("reject", s),
                        inputs=[s],
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