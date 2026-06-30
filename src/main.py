"""
Entry point for merge-engine.

Starts the LangGraph CLI server, MCP server, Gradio UI, or all based on
command-line arguments.

Usage:
    python -m src.main --mode gradio      # Start Gradio UI only (requires langgraph server)
    python -m src.main --mode mcp         # Start MCP server only
    python -m src.main --mode both        # Start MCP + Gradio (default)
    python -m src.main --port 7860        # Custom Gradio port
    python -m src.main --mcp-port 8000    # Custom MCP port
    python -m src.main --config config/custom.yaml
    python -m src.main --start-langgraph  # Also start LangGraph CLI server
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import subprocess
import sys
import time
import urllib.request
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
        "--langgraph-port",
        type=int,
        default=8123,
        help="LangGraph CLI server port (default: 8123)",
    )
    parser.add_argument(
        "--start-langgraph",
        action="store_true",
        help="Start LangGraph CLI server before other services",
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


def start_langgraph_server(port: int = 8123) -> subprocess.Popen | None:
    """Start the LangGraph CLI dev server as a subprocess.
    
    Parameters
    ----------
    port : int
        Port for the LangGraph server (default: 8123)
        
    Returns
    -------
    subprocess.Popen | None
        Process handle, or None if failed to start.
    """
    logger = logging.getLogger("merge-engine.main")
    
    try:
        proc = subprocess.Popen(
            ["langgraph", "dev", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        logger.info("Started LangGraph CLI server (PID: %d, port: %d)", proc.pid, port)
        
        # Wait for server to be ready
        ready = _wait_for_server(port, timeout=30)
        if ready:
            logger.info("LangGraph CLI server is ready on http://localhost:%d", port)
            return proc
        else:
            logger.warning("LangGraph CLI server did not become ready in time")
            proc.terminate()
            return None
            
    except FileNotFoundError:
        logger.error("langgraph CLI not found. Install with: pip install langgraph-cli")
        return None
    except Exception as exc:
        logger.error("Failed to start LangGraph CLI server: %s", exc)
        return None


def _wait_for_server(port: int, timeout: int = 30) -> bool:
    """Poll the LangGraph server until it's ready.
    
    Parameters
    ----------
    port : int
        Port to check.
    timeout : int
        Maximum seconds to wait.
        
    Returns
    -------
    bool
        True if server became ready, False if timed out.
    """
    logger = logging.getLogger("merge-engine.main")
    start = time.time()
    
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f"http://localhost:{port}/docs")
            resp = urllib.request.urlopen(req, timeout=2)
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    
    logger.warning("Server did not become ready after %d seconds", timeout)
    return False


def run_gradio_ui(config: Any, port: int = 7860, langgraph_server_url: str = "http://localhost:8123") -> None:
    """Run the Gradio UI.

    Parameters
    ----------
    config : Any
        Validated application config.
    port : int
        Port to listen on.
    langgraph_server_url : str
        URL of the LangGraph CLI server.
    """
    import gradio as gr

    logging.info("Starting Gradio UI on port %d...", port)
    logging.info("Connecting to LangGraph server at %s", langgraph_server_url)

    from src.ui.gradio_app import CUSTOM_CSS, create_app

    app = create_app(
        config_path="config/config.yaml",
        langgraph_server_url=langgraph_server_url,
    )
    app.launch(
        css=CUSTOM_CSS,  # Gradio 6.0+: CSS must be passed to launch(), not Blocks()
        ssr_mode=False,  # Disable SSR to avoid headless browser issues
        server_port=port,
        server_name="0.0.0.0",
    )


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

    # Start LangGraph CLI server if requested
    lg_proc = None
    langgraph_server_url = "http://localhost:8123"
    
    if args.start_langgraph or args.mode in ("gradio", "both"):
        lg_proc = start_langgraph_server(args.langgraph_port)
        if lg_proc is None:
            logger.warning("LangGraph server not available; Gradio will use in-process mode")
        else:
            logger.info("LangGraph server available at %s", langgraph_server_url)

    # Run requested service(s)
    if args.mode in ("mcp", "both"):
        try:
            asyncio.run(run_mcp_server(config, args.mcp_port))
        except KeyboardInterrupt:
            logging.info("MCP server stopped")

    if args.mode in ("gradio", "both"):
        try:
            run_gradio_ui(config, args.port, langgraph_server_url)
        except KeyboardInterrupt:
            logging.info("Gradio UI stopped")

    # Cleanup
    if lg_proc:
        logger.info("Terminating LangGraph CLI server...")
        lg_proc.terminate()
        try:
            lg_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            lg_proc.kill()

    logger.info("=" * 60)
    logger.info("merge-engine shut down complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()