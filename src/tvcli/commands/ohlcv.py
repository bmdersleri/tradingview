"""OHLCV command group."""

from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path
from typing import Annotated, Any, cast

import typer

from ..errors import TvcliError
from ..layers import ohlcv
from ..output import build_envelope, emit, envelope_from_error
from ._helpers import (
    resolve_json_mode,
    resolve_retry_policy,
    run_command,
    run_with_retries,
)

app = typer.Typer(add_completion=False, help="OHLCV commands")


def fetch_history_query(request: ohlcv.OhlcvRequest) -> tuple[ohlcv.OhlcvBar, ...]:
    return ohlcv.fetch_history(request)


def _bars_to_csv(bars: tuple[ohlcv.OhlcvBar, ...]) -> str:
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=["time", "open", "high", "low", "close", "volume"]
    )
    writer.writeheader()
    for bar in bars:
        writer.writerow(
            {
                "time": bar.time,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
        )
    return buffer.getvalue()


def _write_human_output(format_name: str, payload: dict[str, Any]) -> None:
    data = payload["data"]
    if format_name == "csv":
        bars_data = data["bars"]
        bars = tuple(ohlcv.OhlcvBar(**row) for row in bars_data)
        sys.stdout.write(_bars_to_csv(bars))
        return
    json.dump(data, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


@app.command("get")
def get(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    interval: Annotated[str, typer.Option("--interval")] = "1d",
    bars: Annotated[int, typer.Option("--bars")] = 500,
    format_name: Annotated[str, typer.Option("--format")] = "json",
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)
    request = ohlcv.OhlcvRequest(symbol=symbol, interval=interval, bars=bars)

    def handler() -> dict[str, Any]:
        retries, backoff_seconds = resolve_retry_policy(ctx)
        history = run_with_retries(
            lambda: fetch_history_query(request),
            retries=retries,
            backoff_seconds=backoff_seconds,
        )
        return ohlcv.build_ohlcv_payload(request, history)

    try:
        result = handler()
    except TvcliError as error:
        emit(envelope_from_error("ohlcv.get", error), json_mode=json_mode)
        raise typer.Exit(code=error.exit_code) from error

    payload = build_envelope(command="ohlcv.get", data=result)
    if json_mode:
        emit(payload, json_mode=True)
        return
    _write_human_output(format_name, payload)


@app.command("export")
def export(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument()],
    out: Annotated[Path, typer.Option("--out")],
    interval: Annotated[str, typer.Option("--interval")] = "1d",
    bars: Annotated[int, typer.Option("--bars")] = 5000,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    json_mode = resolve_json_mode(ctx, json_mode)

    def handler() -> dict[str, Any]:
        request = ohlcv.OhlcvRequest(symbol=symbol, interval=interval, bars=bars)
        retries, backoff_seconds = resolve_retry_policy(ctx)
        history = run_with_retries(
            lambda: fetch_history_query(request),
            retries=retries,
            backoff_seconds=backoff_seconds,
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".parquet":
            try:
                pd = cast(Any, importlib.import_module("pandas"))
            except ImportError as exc:  # pragma: no cover - optional extra
                raise TvcliError(
                    "Parquet export requires pandas and pyarrow.",
                    hint="Install the `parquet` extra and retry.",
                ) from exc
            frame = pd.DataFrame(
                [
                    {
                        "time": row.time,
                        "open": row.open,
                        "high": row.high,
                        "low": row.low,
                        "close": row.close,
                        "volume": row.volume,
                    }
                    for row in history
                ]
            )
            frame.to_parquet(out, index=False)
        else:
            out.write_text(_bars_to_csv(history), encoding="utf-8")
        return {
            "path": str(out.resolve()),
            "symbol": symbol,
            "interval": interval,
            "count": len(history),
            "format": "parquet" if out.suffix.lower() == ".parquet" else "csv",
            "bytes": out.stat().st_size,
        }

    run_command("ohlcv.export", json_mode=json_mode, handler=handler)
