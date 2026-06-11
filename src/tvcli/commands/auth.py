"""Auth command group."""

from __future__ import annotations

from datetime import UTC
from typing import Annotated, Any

import typer

from ..auth.login import run_login
from ..auth.session import (
    SessionRecord,
    clear_session,
    require_session,
    save_credentials,
    validate_session,
)
from ..config import default_session_path
from ._helpers import resolve_json_mode, run_command

app = typer.Typer(add_completion=False, help="Authentication commands")


def _session_payload(record: SessionRecord, authenticated: bool) -> dict[str, Any]:
    return {
        "authenticated": authenticated,
        "username": record.username,
        "plan": None,
        "expires_hint": f"Captured at {record.captured_at.isoformat()}",
        "session": {
            "path": str(default_session_path()),
            "storage_state_path": str(record.storage_state_path),
            "captured_at": record.captured_at.astimezone(UTC).isoformat(),
        },
    }


@app.command("import-cookie")
def import_cookie(
    ctx: typer.Context,
    sessionid: Annotated[str, typer.Option("--sessionid")],
    sessionid_sign: Annotated[str | None, typer.Option("--sessionid-sign")] = None,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "auth.import-cookie",
        json_mode=json_mode,
        handler=lambda: _session_payload(
            save_credentials(sessionid=sessionid, sessionid_sign=sessionid_sign),
            authenticated=True,
        ),
    )


@app.command("status")
def status(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)

    def handler() -> dict[str, Any]:
        record = require_session()
        status = validate_session(record)
        return {
            "authenticated": status.authenticated,
            "username": status.username,
            "plan": status.plan,
            "expires_hint": status.expires_hint,
            "session": {
                "path": str(default_session_path()),
                "storage_state_path": str(record.storage_state_path),
                "captured_at": record.captured_at.astimezone(UTC).isoformat(),
            },
        }

    run_command("auth.status", json_mode=json_mode, handler=handler)


@app.command("logout")
def logout(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    run_command(
        "auth.logout",
        json_mode=json_mode,
        handler=lambda: clear_session(),
    )


@app.command("login")
def login(
    ctx: typer.Context,
    headless: Annotated[bool, typer.Option("--headless/--headed")] = True,
    timeout: Annotated[int, typer.Option("--timeout")] = 300,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)

    def handler() -> dict[str, Any]:
        record = run_login(headless=headless, timeout_ms=timeout * 1000)
        return _session_payload(record, authenticated=True)

    run_command("auth.login", json_mode=json_mode, handler=handler)
