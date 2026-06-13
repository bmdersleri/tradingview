from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tvcli.auth.session import (
    SessionRecord,
    _decode,
    clear_session,
    require_session,
    validate_session,
)
from tvcli.errors import NetworkError, SessionExpiredError, SessionRequiredError


def test_decode_missing_sessionid() -> None:
    # Line 83: SessionRequiredError raised when sessionid is empty
    with pytest.raises(SessionRequiredError):
        _decode({})


def test_decode_tzinfo_replacement() -> None:
    # Lines 94-95: no timezone info in captured_at gets replaced with UTC
    dt_naive = datetime(2026, 6, 11, 12, 0, 0)
    data = {"sessionid": "abc", "captured_at": dt_naive.isoformat()}
    record = _decode(data)
    assert record.captured_at.tzinfo == UTC


def test_clear_session_deletes_storage_file(tmp_path: Path) -> None:
    # Lines 176-177: clear_session deletes storage file if it exists
    session_file = tmp_path / "session.json"
    storage_state = tmp_path / "storage_state.json"
    storage_state.write_text("dummy storage state")

    record = SessionRecord(
        sessionid="abc",
        sessionid_sign="def",
        storage_state_path=storage_state,
        captured_at=datetime.now(),
        username="demo",
    )
    from tvcli.auth.session import save_session

    save_session(record, session_file)

    assert storage_state.exists()
    res = clear_session(session_file)
    assert res["storage_state"] is True
    assert not storage_state.exists()


def test_require_session_error(tmp_path: Path) -> None:
    # require_session raises SessionRequiredError when session doesn't exist
    nonexistent = tmp_path / "nonexistent.json"
    with pytest.raises(SessionRequiredError):
        require_session(nonexistent)


def test_validate_session_success(monkeypatch) -> None:
    # Lines 199-236: validate_session success path
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url):
            req = httpx.Request(
                "GET", "https://www.tradingview.com/chart/?symbol=NASDAQ:AAPL"
            )
            res = httpx.Response(200, text="valid page content", request=req)
            return res

    monkeypatch.setattr(httpx, "Client", MockClient)
    record = SessionRecord(
        sessionid="abc",
        sessionid_sign=None,
        storage_state_path=Path("/tmp/x"),
        captured_at=datetime.now(),
        username="demo",
    )
    status = validate_session(record)
    assert status.authenticated is True


def test_validate_session_expired(monkeypatch) -> None:
    # validate_session raises SessionExpiredError when session is expired
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url):
            req = httpx.Request("GET", "https://www.tradingview.com/accounts/signin/")
            res = httpx.Response(200, text="please verify you are human", request=req)
            return res

    monkeypatch.setattr(httpx, "Client", MockClient)
    record = SessionRecord(
        sessionid="abc",
        sessionid_sign=None,
        storage_state_path=Path("/tmp/x"),
        captured_at=datetime.now(),
        username="demo",
    )
    with pytest.raises(SessionExpiredError):
        validate_session(record)


def test_validate_session_network_error(monkeypatch) -> None:
    # validate_session raises NetworkError on HTTPError
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url):
            raise httpx.ConnectError("Network is down")

    monkeypatch.setattr(httpx, "Client", MockClient)
    record = SessionRecord(
        sessionid="abc",
        sessionid_sign=None,
        storage_state_path=Path("/tmp/x"),
        captured_at=datetime.now(),
        username="demo",
    )
    with pytest.raises(NetworkError):
        validate_session(record)
