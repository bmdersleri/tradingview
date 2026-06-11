"""Webhook sinks and dispatch helpers."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..errors import NetworkError, UsageError


class AlertSink(Protocol):
    def send(self, record: dict[str, Any]) -> dict[str, Any]: ...


def _extract_field(payload: Any, names: tuple[str, ...]) -> str | None:
    if isinstance(payload, dict):
        for name in names:
            value = payload.get(name)
            if value is not None and str(value).strip():
                return str(value)
        for value in payload.values():
            extracted = _extract_field(value, names)
            if extracted is not None:
                return extracted
    elif isinstance(payload, list):
        for value in payload:
            extracted = _extract_field(value, names)
            if extracted is not None:
                return extracted
    return None


def format_telegram_message(record: dict[str, Any]) -> str:
    payload = record.get("body")
    if isinstance(payload, str):
        return "\n".join(["TradingView alert", payload])
    symbol = _extract_field(payload, ("symbol", "ticker", "instrument"))
    price = _extract_field(payload, ("price", "close", "last", "value"))
    message = _extract_field(payload, ("message", "title", "text", "alert"))
    parts = ["TradingView alert"]
    if symbol:
        parts.append(f"Symbol: {symbol}")
    if price:
        parts.append(f"Price: {price}")
    if message:
        parts.append(f"Message: {message}")
    if len(parts) == 1:
        parts.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


def append_jsonl(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    return {"path": str(path), "bytes": path.stat().st_size}


@dataclass(slots=True)
class StdoutSink:
    def send(self, record: dict[str, Any]) -> dict[str, Any]:
        json.dump(record, sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
        sys.stdout.flush()
        return {"written": True}


@dataclass(slots=True)
class FileSink:
    path: Path

    def send(self, record: dict[str, Any]) -> dict[str, Any]:
        return append_jsonl(self.path, record)


@dataclass(slots=True)
class TelegramSink:
    token: str
    chat_id: str
    client: httpx.Client | None = None

    def send(self, record: dict[str, Any]) -> dict[str, Any]:
        message = format_telegram_message(record)
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message}
        try:
            if self.client is not None:
                response = self.client.post(url, json=payload)
            else:
                with httpx.Client(timeout=15.0) as client:
                    response = client.post(url, json=payload)
        except httpx.HTTPError as exc:  # pragma: no cover - network dependent
            raise NetworkError(
                "Unable to send Telegram alert.",
                hint="Check the bot token, chat id, and network connectivity.",
            ) from exc
        if response.status_code >= 400:
            raise NetworkError(
                "Telegram API returned an error.",
                hint="Check the bot token, chat id, and message format.",
            )
        return {"sent": True, "status_code": response.status_code}


def build_sink(
    name: str,
    *,
    alerts_path: Path | None = None,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    client: httpx.Client | None = None,
) -> AlertSink:
    normalized = name.casefold()
    if normalized == "stdout":
        return StdoutSink()
    if normalized == "file":
        if alerts_path is None:
            raise UsageError(
                "A file sink path is required.",
                hint="Pass an alerts path from the app configuration.",
            )
        return FileSink(alerts_path)
    if normalized == "telegram":
        if not telegram_token or not telegram_chat_id:
            raise UsageError(
                "Telegram sink requires token and chat id.",
                hint="Pass --telegram-token and --telegram-chat-id.",
            )
        return TelegramSink(
            token=telegram_token,
            chat_id=telegram_chat_id,
            client=client,
        )
    raise UsageError(
        f"Unsupported sink: {name}",
        hint="Use stdout, file, or telegram.",
    )
