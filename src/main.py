"""
Entry point for merge-engine.

Starts the MCP server, Gradio UI, or both based on command-line arguments.

Usage:
    python -m src.main --mode mcp        # Start MCP server only
    python -m src.main --mode gradio      # Start Gradio UI only
    python -m src.main --mode both        # Start both (default)
    python -m src.main --port 7860        # Custom Gradio port
    python -m src.main --config config/custom.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Any

from src.utils.config_loader import load_config
from src.utils.logging_config import setup_logging
from src.mcp_server.server import app as mcp_app, initialize_server
from src.ui.gradio_app import create_app


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="merge-engine: Multi-source data merge with human oversight",
    )
    parser.add_argument(
        "--mode",
        choices=["mcp", "gradio", "both"],
        default="both",
        help="Which service to start (default: both)",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to config.yaml (default: config/config.yaml)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7860,
        help="Gradio UI port (default: 7860)",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=8000,
        help="MCP server port (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


async def run_mcp_server(config: Any, port: int = 8000) -> None:
    """Run the MCP server.

    Parameters
    ----------
    config : Any
        Validated application config.
    port : int
        Port to listen on.
    """
    import asyncio

    try:
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP(
            "merge-engine",
            dependencies=[],
        )

        # Register all tools from the server module
        from src.mcp_server.server import _TOOL_HANDLERS, list_tools, call_tool

        # Register tool list
        @mcp.tool()
        async def discover_schema(origin_id: str) -> str:
            result = await _TOOL_HANDLERS["discover_schema"]({"origin_id": origin_id})
            import json
            return json.dumps(result, default=str)

        @mcp.tool()
        async def extract_data(
            origin_id: str,
            table_name: str,
            columns: list[str],
            batch_size: int = 1000,
            cursor: str | None = None,
            filter: dict[str, Any] | None = None,
        ) -> str:
            result = await _TOOL_HANDLERS["extract_data"]({
                "origin_id": origin_id,
                "table_name": table_name,
                "columns": columns,
                "batch_size": batch_size,
                "cursor": cursor,
                "filter": filter,
            })
            import json
            return json.dumps(result, default=str)

        @mcp.tool()
        async def mark_imported(
            origin_id: str,
            table_name: str,
            ids: list[str],
        ) -> str:
            result = await _TOOL_HANDLERS["mark_imported"]({
                "origin_id": origin_id,
                "table_name": table_name,
                "ids": ids,
            })
            import json
            return json.dumps(result, default=str)

        # Start MCP server
        logging.info("Starting MCP server on port %d...", port)
        mcp.run(host="0.0.0.0", port=port)

    except ImportError:
        logging.warning("mcp package not installed; skipping MCP server")


def run_gradio_ui(config: Any, port: int = 7860) -> None:
    """Run the Gradio UI.

    Parameters
    ----------
    config : Any
        Validated application config.
    port : int
        Port to listen on.
    """
    import gradio as gr

    logging.info("Starting Gradio UI on port %d...", port)

    app = create_app(config_path="config/config.yaml")
    app.launch(server_port=port, server_name="0.0.0.0")


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Setup logging
    setup_logging(level=args.log_level, json=False)
    logger = logging.getLogger("merge-engine")

    logger.info("merge-engine starting (mode=%s, config=%s)", args.mode, args.config)

    # Load config
    try:
        config = load_config(args.config)
        logging.info(
            "Config loaded (origins configured via UI at runtime, config_path=%s)",
            args.config,
        )
    except Exception as exc:
        logging.error("Failed to load config: %s", exc)
        sys.exit(1)

    # Run requested service(s)
    if args.mode in ("mcp", "both"):
        try:
            asyncio.run(run_mcp_server(config, args.mcp_port))
        except KeyboardInterrupt:
            logging.info("MCP server stopped")

    if args.mode in ("gradio", "both"):
        try:
            run_gradio_ui(config, args.port)
        except KeyboardInterrupt:
            logging.info("Gradio UI stopped")

    logging.info("merge-engine shut down complete")


if __name__ == "__main__":
    main()