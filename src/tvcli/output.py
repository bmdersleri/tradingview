"""JSON envelope and human output helpers."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from io import StringIO
from typing import Any, cast

from rich.console import Console
from rich.table import Table

from .errors import TvcliError


def build_envelope(
    *,
    command: str,
    data: Any = None,
    ok: bool = True,
    error: dict[str, object] | None = None,
    cache: dict[str, object] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "ok": ok,
        "command": command,
        "generated_at": (generated_at or datetime.now(tz=UTC)).isoformat(),
        "cache": cache,
        "data": data,
        "error": error,
    }


def render_table(data: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> str:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    table = Table(show_header=True)
    rows: list[Mapping[str, Any]]
    if isinstance(data, Mapping):
        rows = [{"key": key, "value": value} for key, value in data.items()]
        table.add_column("key")
        table.add_column("value")
        for row in rows:
            table.add_row(str(row["key"]), str(row["value"]))
    else:
        rows = list(data)
        columns = sorted({key for row in rows for key in row})
        for column in columns:
            table.add_column(column)
        for row in rows:
            table.add_row(*(str(row.get(column, "")) for column in columns))
    console.print(table)
    return buffer.getvalue()


def emit(
    payload: dict[str, object], *, json_mode: bool, stream: Any | None = None
) -> None:
    if stream is None:
        stream = sys.stdout
    if json_mode:
        json.dump(payload, stream, ensure_ascii=False)
        stream.write("\n")
        return
    data = payload.get("data")
    if isinstance(data, (Mapping, list)):
        stream.write(render_table(data))
        return
    if isinstance(payload.get("error"), dict):
        error = cast(Mapping[str, object], payload["error"])
        stream.write(f"{error['code']}: {error['message']}\n")
        return
    if data is not None:
        stream.write(f"{data}\n")


def envelope_from_error(command: str, error: TvcliError) -> dict[str, object]:
    return build_envelope(
        command=command,
        ok=False,
        data=None,
        error=asdict(error.to_payload()),
    )
