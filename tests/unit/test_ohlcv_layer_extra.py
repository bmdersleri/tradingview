from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tvcli.auth.session import SessionRecord
from tvcli.layers.ohlcv import (
    OhlcvRequest,
    build_ohlcv_payload,
    encode_frame,
    fetch_history,
)


class FakeWebSocket:
    def __init__(self, frames: list[str]) -> None:
        self.frames = frames
        self.sent: list[str] = []
        self.closed = False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self) -> str:
        return self.frames.pop(0)

    def close(self) -> None:
        self.closed = True


def test_fetch_history_success(monkeypatch, tmp_path: Path) -> None:
    record = SessionRecord(
        sessionid="abc",
        sessionid_sign="def",
        storage_state_path=tmp_path / "storage_state.json",
        captured_at=datetime.now(tz=UTC),
        username="demo",
    )
    record.storage_state_path.write_text("{}", encoding="utf-8")
    timescale = encode_frame(
        {
            "m": "timescale_update",
            "p": [
                "cs_tvcli",
                {"s1": {"s": [{"v": [1718064000, 310, 315.5, 308, 312.5, 18234567]}]}},
            ],
        }
    )
    completed = encode_frame({"m": "series_completed", "p": ["cs_tvcli"]})
    ws = FakeWebSocket([timescale, completed])
    monkeypatch.setattr("tvcli.layers.ohlcv.require_session", lambda: record)
    monkeypatch.setattr(
        "tvcli.layers.ohlcv._capture_chart_auth_token",
        lambda _record, _request: "chart-token",
    )
    monkeypatch.setattr(
        "tvcli.layers.ohlcv._load_websocket",
        lambda: SimpleNamespace(create_connection=lambda *args, **_kwargs: ws),
    )

    bars = fetch_history(OhlcvRequest(symbol="BIST:THYAO", interval="1d", bars=1))

    assert bars[0].close == 312.5
    assert ws.closed is True
    assert build_ohlcv_payload(OhlcvRequest("BIST:THYAO", "1d", 1), bars)["count"] == 1


def test_frame_text() -> None:
    from tvcli.layers.ohlcv import _frame_text

    assert _frame_text("hello") == "hello"

    class FakeFrame:
        def __init__(self, payload: Any) -> None:
            self.payload = payload

    assert _frame_text(FakeFrame(b"world")) == "world"
    assert _frame_text(FakeFrame("demo")) == "demo"
    assert _frame_text(123) == ""
