"""Shared CLI helper utilities."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

import click
import typer

from ..errors import TvcliError
from ..output import build_envelope, emit, envelope_from_error

T = TypeVar("T")


def resolve_json_mode(ctx: typer.Context, json_mode: bool) -> bool:
    state = ctx.obj or {}
    return json_mode or bool(state.get("json_mode", False))


def resolve_retry_policy(ctx: typer.Context | None = None) -> tuple[int, float]:
    current_ctx: Any = ctx
    if current_ctx is None:
        current_ctx = click.get_current_context(silent=True)
    state = getattr(current_ctx, "obj", None) or {}
    retries = max(0, int(state.get("retries", 0)))
    backoff_seconds = float(state.get("backoff_seconds", 1.0))
    return retries, backoff_seconds


def run_with_retries(
    handler: Callable[[], T],
    *,
    retries: int,
    backoff_seconds: float,
) -> T:
    attempt = 0
    while True:
        try:
            return handler()
        except TvcliError as error:
            if not error.retryable or attempt >= retries:
                raise
            delay = max(backoff_seconds, 0.0) * (2**attempt)
            if delay > 0:
                time.sleep(delay)
            attempt += 1


def run_command(
    command: str,
    *,
    json_mode: bool,
    handler: Callable[[], object],
) -> None:
    try:
        retries, backoff_seconds = resolve_retry_policy()
        payload = run_with_retries(
            handler,
            retries=retries,
            backoff_seconds=backoff_seconds,
        )
    except TvcliError as error:
        emit(envelope_from_error(command, error), json_mode=json_mode)
        raise typer.Exit(code=error.exit_code) from error
    emit(build_envelope(command=command, data=payload), json_mode=json_mode)
