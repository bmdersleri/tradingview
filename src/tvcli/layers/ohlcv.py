"""TradingView historical bar client."""

from __future__ import annotations

import importlib
import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from shutil import which
from typing import Any, cast

from ..auth.session import require_session
from ..errors import TvcliError, UsageError

FRAME_MARKER = "~m~"

INTERVAL_TO_RESOLUTION = {
    "1": "1",
    "3": "3",
    "5": "5",
    "15": "15",
    "30": "30",
    "45": "45",
    "1h": "60",
    "2h": "120",
    "3h": "180",
    "4h": "240",
    "1d": "1D",
    "1W": "1W",
    "1M": "1M",
}


@dataclass(frozen=True, slots=True)
class OhlcvRequest:
    symbol: str
    interval: str
    bars: int


@dataclass(frozen=True, slots=True)
class OhlcvBar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def resolution_for_interval(interval: str) -> str:
    if interval not in INTERVAL_TO_RESOLUTION:
        raise UsageError(
            f"Unsupported interval: {interval}",
            hint="Use one of: 1, 3, 5, 15, 30, 45, 1h, 2h, 3h, 4h, 1d, 1W, 1M.",
        )
    return INTERVAL_TO_RESOLUTION[interval]


def encode_frame(message: Mapping[str, Any] | list[Any] | str) -> str:
    if isinstance(message, str):
        payload = message
    else:
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
    return f"{FRAME_MARKER}{len(payload)}{FRAME_MARKER}{payload}"


def decode_frames(raw: str | bytes) -> tuple[str, ...]:
    payload = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    frames: list[str] = []
    index = 0
    while True:
        marker = payload.find(FRAME_MARKER, index)
        if marker < 0:
            break
        size_start = marker + len(FRAME_MARKER)
        size_end = payload.find(FRAME_MARKER, size_start)
        if size_end < 0:
            break
        length_text = payload[size_start:size_end]
        if not length_text.isdigit():
            index = size_end + len(FRAME_MARKER)
            continue
        length = int(length_text)
        data_start = size_end + len(FRAME_MARKER)
        data_end = data_start + length
        frames.append(payload[data_start:data_end])
        index = data_end
    return tuple(frames)


def parse_message(message: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(message)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _is_bar_candidate(values: Any) -> bool:
    return (
        isinstance(values, (list, tuple))
        and len(values) >= 6
        and all(isinstance(value, (int, float)) for value in values[:6])
    )


def _extract_bars(value: Any) -> list[OhlcvBar]:
    bars: list[OhlcvBar] = []
    if isinstance(value, dict):
        if "v" in value and _is_bar_candidate(value["v"]):
            stamp, open_, high, low, close, volume = value["v"][:6]
            bars.append(
                OhlcvBar(
                    time=int(stamp),
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=float(volume),
                )
            )
            return bars
        for child in value.values():
            bars.extend(_extract_bars(child))
        return bars
    if isinstance(value, (list, tuple)):
        if _is_bar_candidate(value):
            stamp, open_, high, low, close, volume = value[:6]
            bars.append(
                OhlcvBar(
                    time=int(stamp),
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=float(volume),
                )
            )
            return bars
        for child in value:
            bars.extend(_extract_bars(child))
    return bars


def parse_updates(messages: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    parsed: list[dict[str, Any]] = []
    for message in messages:
        payload = parse_message(message)
        if payload is not None:
            parsed.append(payload)
    return tuple(parsed)


def extract_bars(messages: tuple[dict[str, Any], ...]) -> tuple[OhlcvBar, ...]:
    seen: dict[int, OhlcvBar] = {}
    for message in messages:
        if message.get("m") != "timescale_update":
            continue
        seen.update({bar.time: bar for bar in _extract_bars(message.get("p", []))})
    return tuple(seen[key] for key in sorted(seen))


def build_request_messages(
    *,
    sessionid: str,
    symbol: str,
    interval: str,
    bars: int,
) -> tuple[str, ...]:
    chart_session = "cs_tvcli"
    series_id = "s1"
    symbol_payload = {
        "adjustment": "splits",
        "symbol": symbol,
    }
    return (
        encode_frame({"m": "set_auth_token", "p": [sessionid]}),
        encode_frame({"m": "chart_create_session", "p": [chart_session, ""]}),
        encode_frame(
            {
                "m": "resolve_symbol",
                "p": [
                    chart_session,
                    "symbol_1",
                    "=" + json.dumps(symbol_payload, separators=(",", ":")),
                ],
            }
        ),
        encode_frame(
            {
                "m": "create_series",
                "p": [
                    chart_session,
                    series_id,
                    series_id,
                    "symbol_1",
                    resolution_for_interval(interval),
                    bars,
                ],
            }
        ),
    )


def _load_websocket() -> Any:
    try:
        websocket = importlib.import_module("websocket")
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise TvcliError(
            "WebSocket client is unavailable.",
            hint=(
                "Install the `websocket-client` dependency to enable ohlcv get/export."
            ),
        ) from exc
    return cast(Any, websocket)


def _load_playwright() -> Any:
    try:
        module = importlib.import_module("playwright.sync_api")
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise TvcliError(
            "Playwright is unavailable for TradingView auth token capture.",
            hint="Install the `browser` extra or run `just install`.",
        ) from exc
    return cast(Any, module).sync_playwright


def _frame_text(frame: Any) -> str:
    if isinstance(frame, str):
        return frame
    payload = getattr(frame, "payload", frame)
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="ignore")
    if isinstance(payload, str):
        return payload
    return ""


def _capture_chart_auth_token(record: Any, request: OhlcvRequest) -> str:
    if not record.storage_state_path.exists():
        raise TvcliError(
            "TradingView browser storage state is unavailable.",
            hint="Run `tvcli auth import-cookie` or `tvcli auth login` first.",
        )
    sync_playwright = _load_playwright()
    auth_token: str | None = None
    with sync_playwright() as playwright:
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if which("google-chrome") or which("google-chrome-stable"):
            launch_kwargs["channel"] = "chrome"
        browser = playwright.chromium.launch(**launch_kwargs)
        try:
            context = browser.new_context(
                viewport={"width": 1600, "height": 900},
                ignore_https_errors=True,
                storage_state=str(record.storage_state_path),
            )
            page = context.new_page()

            def on_websocket(ws: Any) -> None:
                def on_frame(frame: Any) -> None:
                    nonlocal auth_token
                    for message in decode_frames(_frame_text(frame)):
                        payload = parse_message(message)
                        if (
                            payload
                            and payload.get("m") == "set_auth_token"
                            and payload.get("p")
                        ):
                            auth_token = str(payload["p"][0])

                ws.on("framesent", on_frame)

            page.on("websocket", on_websocket)
            url = (
                "https://www.tradingview.com/chart/?"
                f"symbol={request.symbol}&interval={request.interval}&theme=dark"
            )
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            deadline = time.monotonic() + 20
            while auth_token is None and time.monotonic() < deadline:
                page.wait_for_timeout(500)
        finally:
            browser.close()
    if not auth_token:
        raise TvcliError(
            "Unable to capture TradingView chart auth token.",
            hint="Refresh the browser session with `tvcli auth import-cookie`.",
        )
    return auth_token


def fetch_history(request: OhlcvRequest, timeout: float = 15.0) -> tuple[OhlcvBar, ...]:
    record = require_session()
    websocket = _load_websocket()
    auth_token = _capture_chart_auth_token(record, request)
    url = "wss://data.tradingview.com/socket.io/websocket"
    messages = build_request_messages(
        sessionid=auth_token,
        symbol=request.symbol,
        interval=request.interval,
        bars=request.bars,
    )
    received: list[dict[str, Any]] = []
    ws: Any | None = None
    try:
        ws = websocket.create_connection(
            url,
            timeout=timeout,
            origin="https://www.tradingview.com",
        )
    except Exception as exc:  # pragma: no cover - network dependent
        raise TvcliError(
            "Unable to connect to the TradingView WebSocket.",
            hint="Check connectivity and retry.",
        ) from exc
    try:
        for message in messages:
            ws.send(message)
        deadline = time.monotonic() + timeout
        completed = False
        while time.monotonic() < deadline:
            raw = ws.recv()
            for payload in parse_updates(decode_frames(raw)):
                received.append(payload)
                if payload.get("m") == "series_completed":
                    completed = True
            if completed:
                break
        bars = extract_bars(tuple(received))
        if not bars:
            raise TvcliError(
                "TradingView returned no OHLCV bars.",
                hint="Try another symbol or interval.",
            )
        return bars[-request.bars :]
    except TvcliError:
        raise
    except Exception as exc:  # pragma: no cover - network dependent
        raise TvcliError(
            "Failed to read OHLCV history from TradingView.",
            hint="Retry after validating the session.",
        ) from exc
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def build_ohlcv_payload(
    request: OhlcvRequest, bars: tuple[OhlcvBar, ...]
) -> dict[str, Any]:
    return {
        "symbol": request.symbol,
        "interval": request.interval,
        "count": len(bars),
        "bars": [asdict(bar) for bar in bars],
    }
