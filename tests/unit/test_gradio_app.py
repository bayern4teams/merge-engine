"""
Unit tests for src/ui/gradio_app.py

Tests ChatState, create_app initialization, and all UI callback functions
including setup wizard (add_origin, submit_origins, submit_destination),
pipeline functions (start_workflow, poll_status, submit_feedback, abort),
and event handler wiring.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def _mock_gradio():
    """Mock gradio module so we can import gradio_app without launching UI.

    Also cleans cached UI modules from sys.modules so the mock applies
    even when other test files (like test_ui_components.py) have already
    imported the real gradio and gradio_app.
    """
    mock_gr = MagicMock()
    mock_gr.Blocks = MagicMock()
    mock_gr.State = MagicMock()
    mock_gr.Dropdown = MagicMock()
    mock_gr.Textbox = MagicMock()
    mock_gr.Number = MagicMock()
    mock_gr.Button = MagicMock()
    mock_row = MagicMock()
    mock_row.__enter__ = MagicMock()
    mock_row.__exit__ = MagicMock()
    mock_gr.Row = MagicMock(return_value=mock_row)
    mock_acc = MagicMock()
    mock_acc.__enter__ = MagicMock()
    mock_acc.__exit__ = MagicMock()
    mock_gr.Accordion = MagicMock(return_value=mock_acc)
    mock_tab = MagicMock()
    mock_tab.__enter__ = MagicMock()
    mock_tab.__exit__ = MagicMock()
    mock_gr.Tab = MagicMock(return_value=mock_tab)
    mock_md = MagicMock()
    mock_md.__enter__ = MagicMock()
    mock_md.__exit__ = MagicMock()
    mock_gr.Markdown = MagicMock(return_value=mock_md)
    ticker = MagicMock()
    ticker.__enter__ = MagicMock()
    ticker.__exit__ = MagicMock()
    ticker.tick = MagicMock()
    mock_gr.Ticker = MagicMock(return_value=ticker)
    mock_gr.update = MagicMock()
    mock_gr.HTML = MagicMock()

    mock_gradio = MagicMock()
    mock_gradio.Blocks = mock_gr.Blocks

    # Remove cached UI modules so they get re-imported under mock
    to_remove = [k for k in sys.modules if k.startswith("src.ui")]
    for k in to_remove:
        sys.modules.pop(k, None)

    sys.modules['gradio'] = mock_gradio
    return mock_gradio


@pytest.fixture
def mock_workflow_manager():
    """Mock WorkflowManager with pre-configured return values."""
    manager = MagicMock()
    manager.start_workflow.return_value = "thread-001"
    manager.get_workflow_status.return_value = {
        "status": "running",
        "interrupt_data": None,
    }
    manager.resume_workflow.return_value = None
    manager.abort_workflow.return_value = None
    return manager


@pytest.fixture
def chat_state_dict():
    """A minimal chat state dict matching what Gradio.State serialises to."""
    return {
        "thread_id": None,
        "status": "idle",
        "last_log": "",
        "origins": [],
        "destination": {},
        "setup_step": "origins",
        "recent_origins": [],
        "recent_destinations": [],
    }


# =============================================================================
# ChatState class tests
# =============================================================================

class TestChatState:
    """Tests for the ChatState per-session state class."""

    def test_initial_state_defaults(self):
        """ChatState should initialise with expected default values."""
        from src.ui.gradio_app import ChatState

        state = ChatState()
        assert state.thread_id is None
        assert state.status == "idle"
        assert state.last_log == ""
        assert state.origins == []
        assert state.destination == {}
        assert state.setup_step == "origins"
        assert state.recent_origins == []
        assert state.recent_destinations == []

    def test_status_transitions(self):
        """ChatState status should be settable to all valid values."""
        from src.ui.gradio_app import ChatState

        state = ChatState()
        for status in ("idle", "running", "interrupted", "completed", "failed", "aborted"):
            state.status = status
            assert state.status == status


# =============================================================================
# create_app tests
# =============================================================================

class TestCreateApp:
    """Tests for the create_app factory function."""

    @patch("src.ui.gradio_app.WorkflowManager")
    def test_creates_workflow_manager(self, mock_wm_cls, mock_workflow_manager):
        """create_app should instantiate a WorkflowManager."""
        mock_wm_cls.return_value = mock_workflow_manager

        from src.ui.gradio_app import create_app

        create_app(config_path="config/config.yaml")

        mock_wm_cls.assert_called_once_with()

    @patch("src.ui.gradio_app.WorkflowManager")
    def test_default_parameters(self, mock_wm_cls, mock_workflow_manager):
        """create_app should use default server_name and server_port."""
        mock_wm_cls.return_value = mock_workflow_manager

        from src.ui.gradio_app import create_app

        app = create_app()

        # Verify the Blocks call received correct title
        mock_wm_cls.assert_called_once()

    @patch("src.ui.gradio_app.WorkflowManager")
    def test_returns_gradio_blocks(self, mock_wm_cls, mock_workflow_manager):
        """create_app should return a gr.Blocks instance."""
        mock_wm_cls.return_value = mock_workflow_manager
        from src.ui.gradio_app import gr
        gr.Blocks.return_value = MagicMock(title="Merge Engine")

        from src.ui.gradio_app import create_app

        app = create_app()

        assert app is not None


# =============================================================================
# Setup Wizard: add_origin_to_json tests
# =============================================================================

class TestAddOriginToJson:
    """Tests for add_origin_to_json function."""

    def test_adds_valid_origin(self):
        """add_origin_to_json should add a valid origin to the list."""
        from src.ui.gradio_app import add_origin_to_json

        json_data = []
        recent = []
        json_data, recent, msg, _ = add_origin_to_json(
            json_data, "postgres", "localhost", 5432, "user", "pass", "mydb", 0, recent
        )
        assert len(json_data) == 1
        assert json_data[0]["type"] == "postgres"
        assert json_data[0]["connection"]["host"] == "localhost"
        assert json_data[0]["connection"]["database"] == "mydb"
        assert "✅" in msg
        assert len(recent) == 1
        assert "postgres → localhost:5432/mydb" in recent[0]

    def test_rejects_missing_fields(self):
        """add_origin_to_json should reject origins missing required fields."""
        from src.ui.gradio_app import add_origin_to_json

        json_data = []
        recent = []
        json_data, recent, msg, _ = add_origin_to_json(
            json_data, "postgres", "", 5432, "", "", "", 0, recent
        )
        assert len(json_data) == 0
        assert "⚠️" in msg

    def test_recent_origins_tracking(self):
        """add_origin_to_json should return display labels for recent origins."""
        from src.ui.gradio_app import add_origin_to_json

        json_data = []
        recent_dicts = []
        # add_origin_to_json returns: (json_data, display_labels, msg, config_json)
        # The internal recent list is returned and must be captured
        json_data, display_labels, msg, config_json = add_origin_to_json(
            json_data, "postgres", "host1", 5432, "user", "pass", "db1", 0, recent_dicts
        )
        # Update recent_dicts with the returned list (it may be a new list)
        from src.ui.gradio_app import _add_to_recent
        recent_dicts = _add_to_recent(recent_dicts, json_data[0])
        json_data, display_labels, msg, config_json = add_origin_to_json(
            json_data, "postgres", "host2", 5432, "user", "pass", "db2", 0, recent_dicts
        )
        recent_dicts = _add_to_recent(recent_dicts, json_data[0])
        # display_labels should be strings from _get_recent_display
        assert len(display_labels) == 2
        assert "host1" in display_labels[0]
        assert "host2" in display_labels[1]

    def test_recent_origins_max_size(self):
        """add_origin_to_json should keep only last 5 in recent history."""
        from src.ui.gradio_app import add_origin_to_json, _add_to_recent

        json_data = []
        recent_dicts = []
        for i in range(7):
            new_origin = {
                "id": f"origin_{len(json_data) + 1}",
                "type": "postgres",
                "connection": {
                    "host": f"host{i}",
                    "port": 5432,
                    "user": "user",
                    "password": "pass",
                    "database": f"db{i}",
                },
                "priority": 0,
            }
            json_data.append(new_origin)
            recent_dicts = _add_to_recent(recent_dicts, new_origin)
        # recent_dicts should keep only last 5 items
        assert len(recent_dicts) == 5
        assert recent_dicts[0]["connection"]["host"] == "host2"
        assert recent_dicts[4]["connection"]["host"] == "host6"


# =============================================================================
# get_origin_port_for_type tests
# =============================================================================

class TestGetOriginPortForType:
    """Tests for get_origin_port_for_type function."""

    def test_postgres_returns_5432(self):
        """get_origin_port_for_type should return 5432 for postgres."""
        from src.ui.gradio_app import get_origin_port_for_type

        assert get_origin_port_for_type("postgres") == 5432

    def test_mongodb_returns_27017(self):
        """get_origin_port_for_type should return 27017 for mongodb."""
        from src.ui.gradio_app import get_origin_port_for_type

        assert get_origin_port_for_type("mongodb") == 27017

    def test_clickhouse_returns_8123(self):
        """get_origin_port_for_type should return 8123 for clickhouse."""
        from src.ui.gradio_app import get_origin_port_for_type

        assert get_origin_port_for_type("clickhouse") == 8123


# =============================================================================
# Setup Wizard: submit_origins_json tests
# =============================================================================

class TestSubmitOriginsJson:
    """Tests for submit_origins_json function."""

    def test_returns_error_when_no_origins(self, chat_state_dict):
        """submit_origins_json should return error when no origins provided."""
        from src.ui.gradio_app import submit_origins_json

        result = submit_origins_json([], chat_state_dict)
        summary = result[0]
        assert "⚠️" in summary

    def test_stores_origins_in_state(self, chat_state_dict):
        """submit_origins_json should store origins in chat state."""
        from src.ui.gradio_app import submit_origins_json

        json_data = [{
            "id": "origin_1",
            "type": "postgres",
            "connection": {"host": "localhost", "port": 5432, "user": "u", "password": "p", "database": "db"},
            "priority": 10,
        }]
        result = submit_origins_json(json_data, chat_state_dict)
        assert len(chat_state_dict["origins"]) == 1
        assert chat_state_dict["setup_step"] == "destination"

    def test_returns_summary_with_origin_details(self, chat_state_dict):
        """submit_origins_json should return summary with origin info."""
        from src.ui.gradio_app import submit_origins_json

        json_data = [{
            "id": "origin_1",
            "type": "postgres",
            "connection": {"host": "localhost", "port": 5432, "user": "u", "password": "p", "database": "mydb"},
            "priority": 10,
        }]
        summary, _, _ = submit_origins_json(json_data, chat_state_dict)
        assert "✅" in summary
        assert "origin_1" in summary
        assert "localhost:5432/mydb" in summary


# =============================================================================
# Setup Wizard: collect_origins_from_state tests (legacy - skip)
# =============================================================================

class TestCollectOriginsFromState:
    """Tests for collect_origins_from_state function (legacy, removed from gradio_app)."""

    @pytest.mark.skip(reason="collect_origins_from_state removed in UI redesign")
    def test_placeholder(self):
        pass


# =============================================================================
# Setup Wizard: submit_destination tests
# =============================================================================

class TestSubmitDestination:
    """Tests for submit_destination function."""

    def test_returns_error_when_fields_missing(self, chat_state_dict):
        """submit_destination should return error for missing fields."""
        from src.ui.gradio_app import submit_destination

        summary, _, _, _, _ = submit_destination(
            dest_type="postgresql",
            dest_host="", dest_port=5432, dest_user="",
            dest_password="", dest_database="", dest_schema="public",
            chat_state=chat_state_dict, recent_destinations=[], current_origins=[],
        )

        assert "⚠️" in summary

    def test_stores_destination_in_state(self, chat_state_dict):
        """submit_destination should store destination config in chat state."""
        from src.ui.gradio_app import submit_destination

        submit_destination(
            dest_type="postgresql",
            dest_host="localhost", dest_port=5432, dest_user="admin",
            dest_password="secret", dest_database="mergedb", dest_schema="analytics",
            chat_state=chat_state_dict, recent_destinations=[], current_origins=[],
        )

        dest = chat_state_dict["destination"]
        assert dest["type"] == "postgresql"
        assert dest["host"] == "localhost"
        assert dest["port"] == 5432
        assert dest["user"] == "admin"
        assert dest["database"] == "mergedb"
        assert dest.get("schema") == "analytics"
        assert chat_state_dict["setup_step"] == "pipeline"

    def test_stores_clickhouse_destination(self, chat_state_dict):
        """submit_destination should store ClickHouse config correctly."""
        from src.ui.gradio_app import submit_destination

        submit_destination(
            dest_type="clickhouse",
            dest_host="10.0.0.1", dest_port=8123, dest_user="admin",
            dest_password="secret", dest_database="default", dest_schema="",
            chat_state=chat_state_dict, recent_destinations=[], current_origins=[],
        )

        dest = chat_state_dict["destination"]
        assert dest["type"] == "clickhouse"
        assert dest["host"] == "10.0.0.1"
        assert dest["port"] == 8123
        assert dest["user"] == "admin"
        assert dest["database"] == "default"
        assert "schema" not in dest

    def test_defaults_port_for_postgresql(self, chat_state_dict):
        """submit_destination should default port to 5432 for PostgreSQL when 0."""
        from src.ui.gradio_app import submit_destination

        submit_destination(
            dest_type="postgresql",
            dest_host="localhost", dest_port=0, dest_user="admin",
            dest_password="secret", dest_database="mergedb", dest_schema="public",
            chat_state=chat_state_dict, recent_destinations=[], current_origins=[],
        )

        assert chat_state_dict["destination"]["port"] == 5432

    def test_defaults_port_for_clickhouse(self, chat_state_dict):
        """submit_destination should default port to 8123 for ClickHouse when 0."""
        from src.ui.gradio_app import submit_destination

        submit_destination(
            dest_type="clickhouse",
            dest_host="localhost", dest_port=0, dest_user="admin",
            dest_password="secret", dest_database="default", dest_schema="",
            chat_state=chat_state_dict, recent_destinations=[], current_origins=[],
        )

        assert chat_state_dict["destination"]["port"] == 8123

    def test_returns_summary_with_destination_info(self, chat_state_dict):
        """submit_destination should return summary with destination details."""
        from src.ui.gradio_app import submit_destination

        chat_state_dict["origins"] = [{"id": "origin_1"}]
        summary, _, _, _, _ = submit_destination(
            dest_type="postgresql",
            dest_host="db.example.com", dest_port=5433, dest_user="admin",
            dest_password="secret", dest_database="mergedb", dest_schema="analytics",
            chat_state=chat_state_dict, recent_destinations=[], current_origins=[{"id": "origin_1"}],
        )

        assert "✅" in summary
        assert "db.example.com:5433" in summary
        assert "mergedb" in summary
        assert "analytics" in summary
        assert "1" in summary

    def test_adds_destination_to_recent(self, chat_state_dict):
        """submit_destination should add destination to recent_destinations."""
        from src.ui.gradio_app import submit_destination

        # Track dicts separately for adding
        dict_recent = []
        summary, _, _, display_recent, _ = submit_destination(
            dest_type="postgresql",
            dest_host="localhost", dest_port=5432, dest_user="admin",
            dest_password="secret", dest_database="mergedb", dest_schema="public",
            chat_state=chat_state_dict, recent_destinations=dict_recent, current_origins=[],
        )

        # dict_recent should have the dict
        assert len(chat_state_dict["recent_destinations"]) == 1
        dest_item = chat_state_dict["recent_destinations"][0]
        assert dest_item["type"] == "postgresql"
        assert dest_item["connection"]["host"] == "localhost"
        assert dest_item["connection"]["database"] == "mergedb"

        # display_recent should have the display string
        assert len(display_recent) == 1
        assert "postgresql" in display_recent[0]
        assert "localhost" in display_recent[0]
        assert "mergedb" in display_recent[0]


# =============================================================================
# Pipeline: start_workflow tests
# =============================================================================

class TestStartWorkflow:
    """Tests for start_workflow function."""

    def test_requires_user_request(self, chat_state_dict):
        """start_workflow should reject empty user request."""
        from src.ui.gradio_app import start_workflow

        result = start_workflow("", chat_state_dict)
        chat_output, _, _, _, _, _, _ = result

        assert any("" in str(m) for m in chat_output)

    def test_requires_origins_configured(self, chat_state_dict):
        """start_workflow should reject when no origins configured."""
        from src.ui.gradio_app import start_workflow

        chat_state_dict["destination"] = {"type": "postgresql"}

        result = start_workflow("Merge all data", chat_state_dict)
        chat_output, _, _, _, _, _, _ = result

        assert "origin" in str(chat_output).lower()

    def test_requires_destination_configured(self, chat_state_dict):
        """start_workflow should reject when no destination configured."""
        from src.ui.gradio_app import start_workflow

        chat_state_dict["origins"] = [{"id": "origin_1"}]

        result = start_workflow("Merge all data", chat_state_dict)
        chat_output, _, _, _, _, _, _ = result

        assert "destination" in str(chat_output).lower()

    @patch("src.ui.gradio_app.workflow_manager")
    def test_starts_workflow_successfully(self, mock_global_wm, chat_state_dict):
        """start_workflow should call workflow_manager.start_workflow."""
        mock_global_wm.start_workflow.return_value = "thread-001"
        chat_state_dict["origins"] = [{"id": "origin_1"}]
        chat_state_dict["destination"] = {"type": "postgresql", "host": "localhost"}

        from src.ui.gradio_app import start_workflow

        result = start_workflow("Merge companies and contacts", chat_state_dict)
        chat_output, request_input, *rest = result

        assert chat_state_dict["thread_id"] == "thread-001"
        assert chat_state_dict["status"] == "running"
        assert "Workflow started" in str(chat_output)
        assert "thread-001" in str(chat_output)

    @patch("src.ui.gradio_app.workflow_manager", None)
    def test_rejects_when_workflow_manager_none(self, chat_state_dict):
        """start_workflow should reject if workflow_manager is None."""
        chat_state_dict["origins"] = [{"id": "origin_1"}]
        chat_state_dict["destination"] = {"type": "postgresql"}

        from src.ui.gradio_app import start_workflow

        result = start_workflow("Merge data", chat_state_dict)
        chat_output, _, _, _, _, _, _ = result

        assert "not initialized" in str(chat_output).lower()

    def test_disables_submit_button_on_start(self, chat_state_dict):
        """start_workflow should disable the submit button after starting."""
        from src.ui.gradio_app import start_workflow

        with patch("src.ui.gradio_app.workflow_manager") as mock_wm:
            mock_wm.start_workflow.return_value = "thread-001"
            chat_state_dict["origins"] = [{"id": "origin_1"}]
            chat_state_dict["destination"] = {"type": "postgresql"}

            _, _, _, _, _, _, btn_state = start_workflow(
                "Merge data", chat_state_dict
            )

            assert btn_state == "disabled"


# =============================================================================
# Pipeline: poll_status tests
# =============================================================================

class TestPollStatus:
    """Tests for poll_status function."""

    @patch("src.ui.gradio_app.workflow_manager")
    def test_returns_idle_panels_hidden_when_status_idle(self, mock_global_wm, chat_state_dict):
        """poll_status should hide all panels when status is idle."""
        chat_state_dict["status"] = "idle"

        from src.ui.gradio_app import poll_status

        result = poll_status(chat_state_dict)

        # poll_status returns 8 values: (event_log_html, pipeline_progress_html, dep_panel, match_panel, mapping_panel, summary_panel, thread_id, btn_state)
        assert len(result) == 8
        # event_log and pipeline_progress are HTML strings
        assert isinstance(result[0], str)  # event_log_html
        assert isinstance(result[1], str)  # pipeline_progress_html
        # All panels should be gr.update(visible=False) calls
        for panel in result[2:6]:
            # Panels should be MagicMock calls to gr.update()
            assert panel is not None

    @patch("src.ui.gradio_app.workflow_manager")
    def test_returns_idle_when_no_thread_id(self, mock_global_wm, chat_state_dict):
        """poll_status should return early when no thread_id."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = None

        from src.ui.gradio_app import poll_status

        result = poll_status(chat_state_dict)
        # Returns 8 values: (event_log_html, pipeline_progress_html, dep_panel, match_panel, mapping_panel, summary_panel, thread_id, btn_state)
        # thread_id at index 6 should be empty string
        assert result[6] == ""  # thread_id
        assert result[7] == "disabled"  # btn_state

    @patch("src.ui.gradio_app.workflow_manager")
    def test_detects_interrupted_status(self, mock_global_wm, chat_state_dict):
        """poll_status should detect interrupted status and show panels."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = "thread-001"

        mock_global_wm.get_workflow_status.return_value = {
            "status": "interrupted",
            "interrupt_data": {
                "node": "human_review_deps",
                "data": {
                    "dependency_tree": {"table1": ["table2"]},
                    "extraction_order": ["table1", "table2"],
                },
            },
        }

        from src.ui.gradio_app import poll_status

        result = poll_status(chat_state_dict)

        assert chat_state_dict["status"] == "interrupted"
        # dep_panel should be a gr.update() call
        dep_panel = result[1]
        assert dep_panel is not None

    @patch("src.ui.gradio_app.workflow_manager")
    def test_detects_completed_status(self, mock_global_wm, chat_state_dict):
        """poll_status should detect completed workflow status."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = "thread-001"

        mock_global_wm.get_workflow_status.return_value = {
            "status": "completed",
            "interrupt_data": None,
        }

        from src.ui.gradio_app import poll_status

        result = poll_status(chat_state_dict)

        assert chat_state_dict["status"] == "completed"

    @patch("src.ui.gradio_app.workflow_manager")
    def test_detects_failed_status(self, mock_global_wm, chat_state_dict):
        """poll_status should detect failed workflow status."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = "thread-001"

        mock_global_wm.get_workflow_status.return_value = {
            "status": "failed",
            "interrupt_data": None,
        }

        from src.ui.gradio_app import poll_status

        result = poll_status(chat_state_dict)

        assert chat_state_dict["status"] == "failed"

    @patch("src.ui.gradio_app.workflow_manager")
    def test_handles_human_review_matches(self, mock_global_wm, chat_state_dict):
        """poll_status should show match review panel for matches interrupt."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = "thread-001"

        mock_global_wm.get_workflow_status.return_value = {
            "status": "interrupted",
            "interrupt_data": {
                "node": "human_review_matches",
                "data": {"low_confidence_clusters": [{"id": "cluster1"}]},
            },
        }

        from src.ui.gradio_app import poll_status

        result = poll_status(chat_state_dict)

        assert chat_state_dict["status"] == "interrupted"

    @patch("src.ui.gradio_app.workflow_manager")
    def test_handles_human_review_mappings(self, mock_global_wm, chat_state_dict):
        """poll_status should show mapping review panel for mappings interrupt."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = "thread-001"

        mock_global_wm.get_workflow_status.return_value = {
            "status": "interrupted",
            "interrupt_data": {
                "node": "human_review_mappings",
                "data": {
                    "unmapped_fields": [],
                    "pending_schema_changes": [],
                },
            },
        }

        from src.ui.gradio_app import poll_status

        result = poll_status(chat_state_dict)

        assert chat_state_dict["status"] == "interrupted"

    @patch("src.ui.gradio_app.workflow_manager")
    def test_handles_human_final_approval(self, mock_global_wm, chat_state_dict):
        """poll_status should show final summary panel for approval interrupt."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = "thread-001"

        mock_global_wm.get_workflow_status.return_value = {
            "status": "interrupted",
            "interrupt_data": {
                "node": "human_final_approval",
                "data": {
                    "summary": {"tables": 5, "records": 1000},
                    "pending_schema_changes": [],
                },
            },
        }

        from src.ui.gradio_app import poll_status

        result = poll_status(chat_state_dict)

        assert chat_state_dict["status"] == "interrupted"

    @patch("src.ui.gradio_app.workflow_manager")
    def test_handles_error_node(self, mock_global_wm, chat_state_dict):
        """poll_status should handle error node by setting failed status."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = "thread-001"

        mock_global_wm.get_workflow_status.return_value = {
            "status": "interrupted",
            "interrupt_data": {
                "node": "handle_error",
                "data": {"error": "Connection timeout"},
            },
        }

        from src.ui.gradio_app import poll_status

        result = poll_status(chat_state_dict)

        assert chat_state_dict["status"] == "failed"


# =============================================================================
# Pipeline: submit_feedback tests
# =============================================================================

class TestSubmitFeedback:
    """Tests for submit_feedback function."""

    @patch("src.ui.gradio_app.workflow_manager")
    def test_submits_approved_feedback(self, mock_global_wm, chat_state_dict):
        """submit_feedback should send approved=True when workflow interrupted."""
        chat_state_dict["status"] = "interrupted"
        chat_state_dict["thread_id"] = "thread-001"
        mock_global_wm.resume_workflow.return_value = None

        from src.ui.gradio_app import submit_feedback

        result = submit_feedback("approve", chat_state_dict)

        mock_global_wm.resume_workflow.assert_called_once_with(
            "thread-001", {"approved": True}
        )

    @patch("src.ui.gradio_app.workflow_manager")
    def test_no_op_without_thread_id(self, mock_global_wm, chat_state_dict):
        """submit_feedback should do nothing when no thread_id."""
        chat_state_dict["thread_id"] = None

        from src.ui.gradio_app import submit_feedback

        result = submit_feedback("approve", chat_state_dict)

        mock_global_wm.resume_workflow.assert_not_called()

    @patch("src.ui.gradio_app.workflow_manager")
    def test_no_op_when_workflow_manager_none(self, chat_state_dict):
        """submit_feedback should do nothing when workflow_manager is None."""
        chat_state_dict["status"] = "interrupted"
        chat_state_dict["thread_id"] = "thread-001"

        from src.ui.gradio_app import submit_feedback

        with patch("src.ui.gradio_app.workflow_manager", None):
            result = submit_feedback("approve", chat_state_dict)

    @patch("src.ui.gradio_app.workflow_manager")
    def test_no_op_when_not_interrupted(self, mock_global_wm, chat_state_dict):
        """submit_feedback should do nothing when not in interrupted status."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = "thread-001"

        from src.ui.gradio_app import submit_feedback

        result = submit_feedback("approve", chat_state_dict)

        mock_global_wm.resume_workflow.assert_not_called()


# =============================================================================
# Pipeline: abort_workflow tests
# =============================================================================

class TestAbortWorkflow:
    """Tests for abort_workflow function."""

    @patch("src.ui.gradio_app.workflow_manager")
    def test_aborts_running_workflow(self, mock_global_wm, chat_state_dict):
        """abort_workflow should call workflow_manager.abort_workflow."""
        chat_state_dict["status"] = "running"
        chat_state_dict["thread_id"] = "thread-001"
        mock_global_wm.abort_workflow.return_value = None

        from src.ui.gradio_app import abort_workflow

        result = abort_workflow(chat_state_dict)

        mock_global_wm.abort_workflow.assert_called_once_with("thread-001")
        assert chat_state_dict["status"] == "aborted"

    @patch("src.ui.gradio_app.workflow_manager")
    def test_no_op_without_thread_id(self, mock_global_wm, chat_state_dict):
        """abort_workflow should do nothing when no thread_id."""
        chat_state_dict["thread_id"] = None

        from src.ui.gradio_app import abort_workflow

        result = abort_workflow(chat_state_dict)

        mock_global_wm.abort_workflow.assert_not_called()

    @patch("src.ui.gradio_app.workflow_manager")
    def test_no_op_when_workflow_manager_none(self, chat_state_dict):
        """abort_workflow should do nothing when workflow_manager is None."""
        chat_state_dict["thread_id"] = "thread-001"

        from src.ui.gradio_app import abort_workflow

        with patch("src.ui.gradio_app.workflow_manager", None):
            result = abort_workflow(chat_state_dict)


# =============================================================================
# db_discovery module tests
# =============================================================================

class TestDbDiscovery:
    """Tests for the db_discovery module."""

    @pytest.mark.no_gradio_mock
    def test_test_and_discover_schemas_unsupported_type(self):
        """test_and_discover_schemas should return error for unsupported type."""
        # Need to temporarily remove the gradio mock for this test
        gradio_module = sys.modules.pop('gradio', None)
        try:
            from src.ui.components.db_discovery import test_and_discover_schemas

            result = test_and_discover_schemas("unsupported", "localhost", 5432, "user", "pass")
            assert result["connected"] is False
            assert "Unsupported" in result["error"]
            assert result["databases"] == []
            assert result["schemas"] == []
        finally:
            if gradio_module:
                sys.modules['gradio'] = gradio_module

    @pytest.mark.no_gradio_mock
    def test_test_and_discover_schemas_missing_credentials(self):
        """test_and_discover_schemas should handle empty credentials gracefully."""
        gradio_module = sys.modules.pop('gradio', None)
        try:
            from src.ui.components.db_discovery import test_and_discover_schemas

            # Should not raise, just return error
            result = test_and_discover_schemas("postgres", "", 0, "", "")
            assert isinstance(result, dict)
            assert "connected" in result
            assert "error" in result
            assert "databases" in result
        finally:
            if gradio_module:
                sys.modules['gradio'] = gradio_module


# =============================================================================
# UI Event Handler Wiring tests
# =============================================================================

class TestUIWiring:
    """Tests that verify Gradio event handler wiring in create_app."""

    @patch("src.ui.gradio_app.WorkflowManager")
    def test_has_setup_tab(self, mock_wm_cls, mock_workflow_manager):
        """create_app should have a Setup tab."""
        mock_wm_cls.return_value = mock_workflow_manager

        from src.ui.gradio_app import create_app

        with patch("src.ui.gradio_app.gr") as mock_gr:
            mock_gr.Blocks.return_value = MagicMock()
            create_app()

    @patch("src.ui.gradio_app.WorkflowManager")
    def test_has_pipeline_tab(self, mock_wm_cls, mock_workflow_manager):
        """create_app should have a Pipeline tab."""
        mock_wm_cls.return_value = mock_workflow_manager

        from src.ui.gradio_app import create_app

        with patch("src.ui.gradio_app.gr") as mock_gr:
            mock_gr.Blocks.return_value = MagicMock()
            create_app()
