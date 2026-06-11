from __future__ import annotations

from datetime import UTC, datetime

from typer.testing import CliRunner

from tvcli.auth.session import SessionRecord, SessionStatus
from tvcli.cli import app
from tvcli.commands import auth


def test_auth_import_status_logout_and_login(monkeypatch, tmp_path) -> None:
    record = SessionRecord(
        sessionid="abc",
        sessionid_sign="def",
        storage_state_path=tmp_path / "storage_state.json",
        captured_at=datetime(2026, 6, 11, tzinfo=UTC),
        username="demo",
    )

    monkeypatch.setattr(auth, "save_credentials", lambda **kwargs: record)
    monkeypatch.setattr(auth, "require_session", lambda: record)
    monkeypatch.setattr(
        auth,
        "validate_session",
        lambda current: SessionStatus(
            authenticated=True,
            username=current.username,
            plan="free",
            expires_hint="Captured at 2026-06-11T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        auth,
        "clear_session",
        lambda: {"session": True, "storage_state": True},
    )
    monkeypatch.setattr(auth, "run_login", lambda **kwargs: record)

    runner = CliRunner()
    imported = runner.invoke(
        app,
        [
            "auth",
            "import-cookie",
            "--sessionid",
            "abc",
            "--sessionid-sign",
            "def",
            "--json",
        ],
    )
    status = runner.invoke(app, ["auth", "status", "--json"])
    logout = runner.invoke(app, ["auth", "logout", "--json"])
    login = runner.invoke(app, ["auth", "login", "--json"])

    assert imported.exit_code == 0
    assert status.exit_code == 0
    assert logout.exit_code == 0
    assert login.exit_code == 0
    assert '"command": "auth.import-cookie"' in imported.output
    assert '"authenticated": true' in status.output
    assert '"command": "auth.logout"' in logout.output
    assert '"command": "auth.login"' in login.output
