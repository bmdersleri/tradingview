from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from tvcli import doctor
from tvcli.auth.session import SessionRecord, SessionStatus
from tvcli.cli import app


def test_run_doctor_reports_all_ok(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(doctor, "find_spec", lambda _name: object())
    monkeypatch.setattr(doctor, "which", lambda _name: "/usr/bin/chromium")
    monkeypatch.setattr(
        doctor,
        "load_session",
        lambda: SessionRecord(
            sessionid="abc",
            sessionid_sign="def",
            storage_state_path=tmp_path / "storage_state.json",
            captured_at=datetime.now(tz=UTC),
            username="demo-user",
        ),
    )
    monkeypatch.setattr(
        doctor,
        "validate_session",
        lambda record: SessionStatus(
            authenticated=True,
            username=record.username,
            plan=None,
            expires_hint="Captured at 2026-06-11T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        doctor.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200),
    )

    report = doctor.run_doctor()

    assert report["all_ok"] is True
    assert len(report["checks"]) == 5
    assert all(check["ok"] for check in report["checks"])


def test_doctor_command_exits_nonzero_on_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(
        "tvcli.cli.run_doctor",
        lambda: {
            "all_ok": False,
            "checks": [
                {
                    "name": "upstream",
                    "ok": False,
                    "detail": "TradingView returned HTTP 503.",
                    "hint": "Retry later.",
                }
            ],
        },
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    assert '"command": "doctor"' in result.output
    assert '"all_ok": false' in result.output
