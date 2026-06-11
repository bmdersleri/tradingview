"""MCP command group."""

from __future__ import annotations

import typer

from ..mcp import run_mcp_server

app = typer.Typer(add_completion=False, help="MCP wrapper commands")


@app.command("serve")
def serve() -> None:
    run_mcp_server()
