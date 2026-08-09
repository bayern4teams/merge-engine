"""
Entry point for merge-engine.

Starts the MCP server, the web UI (FastAPI + React), or both, based on
command-line arguments. Observability is provided by a self-hosted Langfuse
stack (docker/langfuse-compose.yml), started separately.

Usage:
    python -m src.main --mode gradio      # Start web UI only
    python -m src.main --mode mcp         # Start MCP server only
    python -m src.main --mode both        # Start MCP + web UI (default)
    python -m src.main --port 7860        # Custom web UI port
    python -m src.main --mcp-port 8000    # Custom MCP port
    python -m src.main --config config/custom.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from src.utils.config_loader import load_config
from src.utils.logging_config import setup_logging

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]

    # Resolve .env relative to the project root (src/main.py is one level inside src/),
    # so defaults load regardless of the CWD the app was launched from.
    _ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_ENV_FILE, override=True)
except ImportError:
    pass


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
        default="DEBUG",
        help="Logging level (default: DEBUG)",
    )
    parser.add_argument(
        "--log-file",
        default="logs/debug.log",
        help="Log file path (default: logs/debug.log)",
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

    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]

        mcp = FastMCP(
            "merge-engine",
            dependencies=[],
        )

        # Register all tools from the server module
        from src.mcp_server.server import _TOOL_HANDLERS

        # Register tool list
        @mcp.tool()  # type: ignore[untyped-decorator]
        async def discover_schema(origin_id: str) -> str:
            result = await _TOOL_HANDLERS["discover_schema"]({"origin_id": origin_id})
            import json

            return json.dumps(result, default=str)

        @mcp.tool()  # type: ignore[untyped-decorator]
        async def extract_data(
            origin_id: str,
            table_name: str,
            columns: list[str],
            batch_size: int = 1000,
            cursor: str | None = None,
            filter: dict[str, Any] | None = None,
        ) -> str:
            result = await _TOOL_HANDLERS["extract_data"](
                {
                    "origin_id": origin_id,
                    "table_name": table_name,
                    "columns": columns,
                    "batch_size": batch_size,
                    "cursor": cursor,
                    "filter": filter,
                }
            )
            import json

            return json.dumps(result, default=str)

        # Start MCP server
        logging.info("Starting MCP server on port %d...", port)
        mcp.run(host="0.0.0.0", port=port)

    except ImportError:
        logging.warning("mcp package not installed; skipping MCP server")


def run_web_ui(config: Any, port: int = 7860, config_path: str = "config/config.yaml") -> None:
    """Run the web UI: a FastAPI backend serving the built React frontend.

    The merge workflow executes in-process (WorkflowManager) exactly as
    before — this only swaps the presentation layer (Gradio → React).

    Parameters
    ----------
    config : Any
        Validated application config.
    port : int
        Port to listen on.
    config_path : str
        Path to config.yaml passed through to the workflow manager.
    """
    import os

    import uvicorn

    from src.api.main import create_api

    os.environ["NO_PROXY"] = "localhost,127.0.0.1,0.0.0.0,::1"

    # Loopback-only by default (today's behaviour, unchanged for every
    # native/non-Docker run). Part 1.6.C's Dockerfile sets
    # MERGE_ENGINE_HOST=0.0.0.0 — a container's own loopback is not the
    # network interface Docker's `-p` port-forwarding reaches, so the app
    # must bind on every interface to be reachable from outside the
    # container at all (verified live: with 127.0.0.1, the published port
    # accepted the TCP connection but every request got an empty reply,
    # since nothing inside the container was listening on the interface
    # Docker actually forwards to).
    host = os.environ.get("MERGE_ENGINE_HOST", "127.0.0.1")
    logging.info("Starting web UI (FastAPI + React) on http://%s:%d ...", host, port)
    app = create_api(config_path=config_path)
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Setup logging with DEBUG level and file output
    setup_logging(level=args.log_level, json=False, log_file=args.log_file)
    logger = logging.getLogger("merge-engine")

    # Print DEBUG mode banner
    logger.info("=" * 60)
    logger.info("=== MERGE ENGINE DEBUG MODE ===")
    logger.info("=" * 60)
    logger.info("Log file: %s", args.log_file)
    logger.info("Mode: %s", args.mode)
    logger.info("Config: %s", args.config)
    logger.info("=" * 60)

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

    # Merge workflows always execute in-process (WorkflowManager). Tracing
    # goes to a self-hosted Langfuse stack (docker/langfuse-compose.yml),
    # started separately — nothing to launch here.

    # Run requested service(s)
    if args.mode in ("mcp", "both"):
        try:
            asyncio.run(run_mcp_server(config, args.mcp_port))
        except KeyboardInterrupt:
            logging.info("MCP server stopped")

    if args.mode in ("gradio", "both"):
        try:
            run_web_ui(config, args.port, args.config)
        except KeyboardInterrupt:
            logging.info("Web UI stopped")

    logger.info("=" * 60)
    logger.info("merge-engine shut down complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
