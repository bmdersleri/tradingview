from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tvcli.auth.session import (
    SessionRecord,
    clear_session,
    cookie_jar,
    load_session,
    save_session,
)


def test_save_load_and_clear_session(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    storage_state = tmp_path / "storage_state.json"
    record = SessionRecord(
        sessionid="abc",
        sessionid_sign="def",
        storage_state_path=storage_state,
        captured_at=datetime(2026, 6, 11, tzinfo=UTC),
        username="demo",
    )

    save_session(record, session_file)

    loaded = load_session(session_file)
    assert loaded is not None
    assert loaded.sessionid == "abc"
    assert loaded.storage_state_path == storage_state
    assert session_file.exists()

    removed = clear_session(session_file)
    assert removed["session"] is True
    assert load_session(session_file) is None


def test_cookie_jar_includes_sign_cookie() -> None:
    record = SessionRecord(
        sessionid="abc",
        sessionid_sign="def",
        storage_state_path=Path("/tmp/storage_state.json"),
        captured_at=datetime.now(tz=UTC),
        username=None,
    )

    assert cookie_jar(record) == {
        "sessionid": "abc",
        "sessionid_sign": "def",
    }
