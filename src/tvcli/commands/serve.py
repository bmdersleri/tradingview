"""Webhook server and dashboard command group."""

from __future__ import annotations

from typing import Annotated

import typer
import uvicorn

from ..floatdash import app as _floatdash
from ..webhook.app import create_app

app = typer.Typer(add_completion=False, help="Server commands")


def run_webhook_server(
    *,
    host: str,
    port: int,
    secret: str,
    sink: str,
    telegram_token: str | None,
    telegram_chat_id: str | None,
) -> None:
    webhook_app = create_app(
        secret=secret,
        sink=sink,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
    )
    uvicorn.run(webhook_app, host=host, port=port, log_level="warning")


@app.command("webhook")
def webhook(
    secret: Annotated[str, typer.Option("--secret")],
    host: Annotated[str, typer.Option("--host")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port")] = 8787,
    sink: Annotated[str, typer.Option("--sink")] = "stdout",
    telegram_token: Annotated[str | None, typer.Option("--telegram-token")] = None,
    telegram_chat_id: Annotated[str | None, typer.Option("--telegram-chat-id")] = None,
) -> None:
    """Start the webhook receiver server."""
    run_webhook_server(
        host=host,
        port=port,
        secret=secret,
        sink=sink,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
    )


@app.command("float-dashboard")
def float_dashboard_serve(
    host: Annotated[str, typer.Option("--host")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port")] = 8788,
) -> None:
    """Interactive free-float dashboard web server."""
    fapp = _floatdash.create_app()
    typer.echo(f"Float dashboard → http://localhost:{port}/")
    uvicorn.run(fapp, host=host, port=port, log_level="warning")
