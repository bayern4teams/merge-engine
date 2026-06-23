"""
Dynamic origin database loader UI component.

Provides a form where users can add N origin databases (PostgreSQL or MongoDB)
with connection details (host, port, user, password, database, priority).
"""

from __future__ import annotations

from typing import Any

import gradio as gr


# Counter for tracking origin entry IDs
_origin_counter: int = 0
_origin_widgets: list[dict[str, Any]] = []


def reset_origin_counter() -> None:
    """Reset the origin counter and widgets list."""
    global _origin_counter, _origin_widgets
    _origin_counter = 0
    _origin_widgets = []


def get_next_origin_id() -> str:
    """Get the next unique origin entry ID."""
    global _origin_counter
    _origin_counter += 1
    return f"origin_{_origin_counter}"


def add_origin_entry() -> tuple[list, list, list, list, list, list, list, list]:
    """Add a new origin database entry form to the UI.

    Returns
    -------
    tuple
        Gradio update objects for all relevant component lists.
    """
    origin_id = get_next_origin_id()

    # Create a group of inputs for this origin entry
    type_dropdown = gr.Dropdown(
        label="Database Type",
        choices=["postgres", "mongodb"],
        value="postgres",
        interactive=True,
    )
    host_input = gr.Textbox(
        label="Host (IP or hostname)",
        placeholder="e.g., 192.168.1.100 or db.example.com",
        value="",
    )
    port_input = gr.Number(
        label="Port",
        value=5432,
        interactive=True,
    )
    user_input = gr.Textbox(
        label="Username",
        placeholder="e.g., db_reader",
        value="",
    )
    password_input = gr.Textbox(
        label="Password",
        placeholder="••••••••",
        type="password",
        value="",
    )
    database_input = gr.Textbox(
        label="Database Name",
        placeholder="e.g., crm_prod",
        value="",
    )
    priority_input = gr.Number(
        label="Priority (0-100, higher = preferred for conflicts)",
        value=0,
        minimum=0,
        maximum=100,
        interactive=True,
    )
    remove_btn = gr.Button("🗑 Remove", variant="secondary", size="sm")

    entry = {
        "id": origin_id,
        "type": type_dropdown,
        "host": host_input,
        "port": port_input,
        "user": user_input,
        "password": password_input,
        "database": database_input,
        "priority": priority_input,
        "remove": remove_btn,
    }
    _origin_widgets.append(entry)

    return (
        gr.update(),  # trigger to refresh origin list display
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
    )


def get_origin_configs() -> list[dict[str, Any]]:
    """Extract origin configurations from the UI inputs.

    Returns
    -------
    list[dict]
        List of origin config dictionaries ready for use by the pipeline.
        Each dict has: id, type, connection (host, port, user, password, database), priority.
    """
    origins = []
    for i, entry in enumerate(_origin_widgets):
        origin_id = entry["id"]

        # Get current values from Gradio components
        db_type = None
        host = None
        port = None
        user = None
        password = None
        database = None
        priority = 0

        # Values are set via state — we store them in the entry dict when submitted
        if f"type_{i}" in entry:
            db_type = entry[f"type_{i}"]
        if f"host_{i}" in entry:
            host = entry[f"host_{i}"]
        if f"port_{i}" in entry:
            port = entry[f"port_{i}"]
        if f"user_{i}" in entry:
            user = entry[f"user_{i}"]
        if f"password_{i}" in entry:
            password = entry[f"password_{i}"]
        if f"database_{i}" in entry:
            database = entry[f"database_{i}"]
        if f"priority_{i}" in entry:
            priority = entry[f"priority_{i}"]

        if not all([db_type, host, port is not None, user, password, database]):
            continue  # Skip incomplete entries

        origins.append({
            "id": origin_id,
            "type": db_type,
            "connection": {
                "host": host,
                "port": int(port),
                "user": user,
                "password": password,
                "database": database,
            },
            "priority": int(priority),
        })

    return origins


def collect_origin_values(
    indices: list[int],
    types: list[str],
    hosts: list[str],
    ports: list[int],
    users: list[str],
    passwords: list[str],
    databases: list[str],
    priorities: list[int],
) -> list[dict[str, Any]]:
    """Collect origin values from submitted form data.

    This is the primary method called when the user submits the origin form.

    Parameters
    ----------
    indices : list[int]
        List of active origin entry indices.
    types : list[str]
        Database types for each entry.
    hosts : list[str]
        Hosts for each entry.
    ports : list[int]
        Ports for each entry.
    users : list[str]
        Usernames for each entry.
    passwords : list[str]
        Passwords for each entry.
    databases : list[str]
        Database names for each entry.
    priorities : list[int]
        Priority values for each entry.

    Returns
    -------
    list[dict]
        List of origin configuration dictionaries.
    """
    origins = []
    for i, idx in enumerate(indices):
        if not all([types[i], hosts[i], users[i], passwords[i], databases[i]]):
            continue  # Skip incomplete entries

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


def render_origin_loader() -> gr.Group:
    """Render the origin database loader panel.

    Returns
    -------
    gr.Group
        Gradio component group containing the origin loader form.
    """
    global _origin_counter, _origin_widgets
    _origin_counter = 0
    _origin_widgets = []

    with gr.Group() as origin_loader:
        gr.Markdown("## Origin Databases")
        gr.Markdown("Add the source databases to merge from. You can add multiple PostgreSQL or MongoDB databases.")

        with gr.Row():
            with gr.Column(scale=1):
                add_btn = gr.Button("➕ Add Origin Database", variant="primary")

        # Dynamic origin entries will be added here
        origin_display = gr.Markdown("No origins added yet. Click 'Add Origin Database' to begin.")

        # Hidden state for collected values
        origin_indices = gr.State([])
        origin_types = gr.State([])
        origin_hosts = gr.State([])
        origin_ports = gr.State([])
        origin_users = gr.State([])
        origin_passwords = gr.State([])
        origin_databases = gr.State([])
        origin_priorities = gr.State([])

        # Submit origins button
        submit_origins_btn = gr.Button("✅ Save Origins & Continue", variant="stop")
        origins_output = gr.Textbox(
            label="Loaded Origins",
            interactive=False,
            lines=5,
            placeholder="Origins will appear here after saving...",
        )

        # Event handlers
        add_btn.click(
            fn=_add_origin_form_row,
            inputs=[],
            outputs=[origin_display],
        )

        submit_origins_btn.click(
            fn=_submit_origins,
            inputs=[
                origin_indices,
                origin_types,
                origin_hosts,
                origin_ports,
                origin_users,
                origin_passwords,
                origin_databases,
                origin_priorities,
            ],
            outputs=[origins_output],
        )

    return origin_loader


def _add_origin_form_row() -> gr.update:
    """Add a single origin form row to the display."""
    global _origin_widgets

    idx = len(_origin_widgets)
    origin_id = get_next_origin_id()

    with gr.Row():
        with gr.Column(scale=1):
            type_dd = gr.Dropdown(
                label=f"Origin {idx + 1} - Type",
                choices=["postgres", "mongodb"],
                value="postgres",
            )
        with gr.Column(scale=2):
            host_tb = gr.Textbox(
                label=f"Origin {idx + 1} - Host",
                placeholder="Host",
            )
        with gr.Column(scale=1):
            port_nb = gr.Number(
                label=f"Origin {idx + 1} - Port",
                value=5432,
            )

    with gr.Row():
        with gr.Column(scale=2):
            user_tb = gr.Textbox(
                label=f"Origin {idx + 1} - Username",
                placeholder="Username",
            )
        with gr.Column(scale=2):
            pass_tb = gr.Textbox(
                label=f"Origin {idx + 1} - Password",
                type="password",
                placeholder="Password",
            )
        with gr.Column(scale=2):
            db_tb = gr.Textbox(
                label=f"Origin {idx + 1} - Database",
                placeholder="Database name",
            )
        with gr.Column(scale=1):
            pri_nb = gr.Number(
                label=f"Origin {idx + 1} - Priority",
                value=0,
                minimum=0,
                maximum=100,
            )
        with gr.Column(scale=1):
            rmv_btn = gr.Button(f"🗑 Remove", variant="secondary", size="sm")

    entry = {
        "id": origin_id,
        f"type_{idx}": type_dd,
        f"host_{idx}": host_tb,
        f"port_{idx}": port_nb,
        f"user_{idx}": user_tb,
        f"password_{idx}": pass_tb,
        f"database_{idx}": db_tb,
        f"priority_{idx}": pri_nb,
        "remove": rmv_btn,
    }
    _origin_widgets.append(entry)

    # Update display
    display_text = f"**{len(_origin_widgets)} origin(s) configured:**\n"
    for e in _origin_widgets:
        display_text += f"- {e['id']} (type: postgres/mongodb)\n"

    return gr.update(value=display_text)


def _submit_origins(
    indices: list[int],
    types: list[str],
    hosts: list[str],
    ports: list[int],
    users: list[str],
    passwords: list[str],
    databases: list[str],
    priorities: list[int],
) -> str:
    """Submit and validate origin configurations.

    Parameters
    ----------
    indices : list[int]
        List of origin entry indices.
    types : list[str]
        Database types.
    hosts : list[str]
        Host values.
    ports : list[int]
        Port values.
    users : list[str]
        Username values.
    passwords : list[str]
        Password values.
    databases : list[str]
        Database name values.
    priorities : list[int]
        Priority values.

    Returns
    -------
    str
        Summary of loaded origins.
    """
    origins = collect_origin_values(indices, types, hosts, ports, users, passwords, databases, priorities)

    if not origins:
        return "No origins configured. Please add at least one origin database."

    summary_lines = [f"✅ **{len(origins)} origin(s) loaded:**"]
    for o in origins:
        conn = o["connection"]
        summary_lines.append(
            f"- `{o['id']}` ({o['type']}) → {conn['host']}:{conn['port']}/{conn['database']} "
            f"(priority: {o['priority']})"
        )

    summary_lines.append("")
    summary_lines.append("Ready to continue to destination configuration.")

    return "\n".join(summary_lines)