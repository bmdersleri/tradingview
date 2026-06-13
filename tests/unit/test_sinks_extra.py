from __future__ import annotations

import httpx
import pytest

from tvcli.errors import NetworkError, UsageError
from tvcli.webhook.sinks import (
    StdoutSink,
    TelegramSink,
    _extract_field,
    build_sink,
    format_telegram_message,
)


def test_extract_field_nested() -> None:
    # Test dictionary nesting recursion
    payload_dict = {"outer": {"inner": {"symbol": "THYAO"}}}
    assert _extract_field(payload_dict, ("symbol",)) == "THYAO"

    # Test list nesting recursion
    payload_list = {"items": [{"price": 123.45}]}
    assert _extract_field(payload_list, ("price",)) == "123.45"

    # Test empty returns
    assert _extract_field(None, ("symbol",)) is None
    assert _extract_field("plain", ("symbol",)) is None


def test_format_telegram_message_plain_body() -> None:
    record = {"body": "Plain alert text"}
    msg = format_telegram_message(record)
    assert "TradingView alert" in msg
    assert "Plain alert text" in msg


def test_format_telegram_message_json_fallback() -> None:
    # If no recognized fields are present, it falls back to dumping the whole JSON
    record = {"body": {"unknown_field": "some_value"}}
    msg = format_telegram_message(record)
    assert "TradingView alert" in msg
    assert '{"unknown_field": "some_value"}' in msg


def test_stdout_sink(capsys) -> None:
    sink = StdoutSink()
    record = {"hello": "world"}
    res = sink.send(record)
    assert res == {"written": True}
    captured = capsys.readouterr()
    assert '{"hello": "world"}\n' in captured.out


def test_telegram_sink_default_client() -> None:
    # Test sending telegram alert with default httpx client (creating one inside)
    # We mock the httpx.Client post to avoid real network call
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def post(self, url, json):
            return httpx.Response(200, json={"ok": True})

    original_client = httpx.Client
    try:
        httpx.Client = MockClient  # type: ignore
        sink = TelegramSink(token="tok", chat_id="chat")
        res = sink.send({"body": "Test message"})
        assert res == {"sent": True, "status_code": 200}
    finally:
        httpx.Client = original_client


def test_telegram_sink_error_response() -> None:
    # Test HTTP error response status >= 400
    class MockHttpxClient:
        def post(self, url, json):
            return httpx.Response(400, text="Bad Request")

    sink = TelegramSink(token="tok", chat_id="chat", client=MockHttpxClient())  # type: ignore
    with pytest.raises(NetworkError) as exc_info:
        sink.send({"body": "Test message"})
    assert "Telegram API returned an error" in str(exc_info.value)


def test_build_sink_invalid_and_missing() -> None:
    # stdout build
    sink_stdout = build_sink("stdout")
    assert isinstance(sink_stdout, StdoutSink)

    # file build missing path
    with pytest.raises(UsageError) as exc_info:
        build_sink("file", alerts_path=None)
    assert "A file sink path is required" in str(exc_info.value)

    # telegram build missing token/chat
    with pytest.raises(UsageError) as exc_info:
        build_sink("telegram", telegram_token=None, telegram_chat_id="123")
    assert "Telegram sink requires token and chat id" in str(exc_info.value)

    # unsupported sink
    with pytest.raises(UsageError) as exc_info:
        build_sink("unsupported-sink")
    assert "Unsupported sink: unsupported-sink" in str(exc_info.value)
