"""FastAPI webhook application."""

from __future__ import annotations

import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from ..config import default_alerts_path
from .sinks import AlertSink, FileSink, build_sink


@dataclass(slots=True)
class WebhookState:
    secret: str
    append_sink: FileSink
    dispatch_sink: AlertSink | None
    started_at: float


def _parse_body(raw_body: bytes) -> Any:
    text = raw_body.decode("utf-8", errors="replace")
    if not text.strip():
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def create_app(
    *,
    secret: str,
    sink: str = "stdout",
    alerts_path: Path | None = None,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    telegram_client: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="tvcli webhook")
    append_sink = FileSink(alerts_path or default_alerts_path())
    dispatch_sink = None
    if sink.casefold() != "file":
        dispatch_sink = build_sink(
            sink,
            alerts_path=append_sink.path,
            telegram_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
            client=telegram_client,
        )
    app.state.webhook = WebhookState(
        secret=secret,
        append_sink=append_sink,
        dispatch_sink=dispatch_sink,
        started_at=time.monotonic(),
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        state = cast(WebhookState, app.state.webhook)
        return {
            "ok": True,
            "uptime_seconds": round(time.monotonic() - state.started_at, 3),
        }

    @app.post("/hook/{provided_secret}")
    async def hook(provided_secret: str, request: Request) -> JSONResponse:
        state = cast(WebhookState, app.state.webhook)
        if not hmac.compare_digest(state.secret, provided_secret):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid webhook secret.",
            )
        raw_body = await request.body()
        body = _parse_body(raw_body)
        record = {
            "received_at": datetime.now(tz=UTC).isoformat(),
            "body": body,
            "content_type": request.headers.get("content-type"),
        }
        stored = state.append_sink.send(record)
        dispatched: dict[str, Any] | None = None
        if state.dispatch_sink is not None:
            dispatched = state.dispatch_sink.send(record)
        return JSONResponse(
            {
                "ok": True,
                "stored": True,
                "dispatch": state.dispatch_sink is not None,
                "path": stored.get("path"),
                "dispatched": dispatched,
            }
        )

    return app
