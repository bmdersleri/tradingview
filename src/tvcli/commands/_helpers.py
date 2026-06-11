"""Shared CLI helper utilities."""

from __future__ import annotations

from collections.abc import Callable

import typer

from ..errors import TvcliError
from ..output import build_envelope, emit, envelope_from_error


def resolve_json_mode(ctx: typer.Context, json_mode: bool) -> bool:
    state = ctx.obj or {}
    return json_mode or bool(state.get("json_mode", False))


def run_command(
    command: str,
    *,
    json_mode: bool,
    handler: Callable[[], object],
) -> None:
    try:
        payload = handler()
    except TvcliError as error:
        emit(envelope_from_error(command, error), json_mode=json_mode)
        raise typer.Exit(code=error.exit_code) from error
    emit(build_envelope(command=command, data=payload), json_mode=json_mode)
