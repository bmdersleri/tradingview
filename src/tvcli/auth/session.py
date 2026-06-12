"""TradingView session storage helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..config import default_session_path, default_storage_state_path
from ..errors import NetworkError, SessionExpiredError, SessionRequiredError


@dataclass(frozen=True, slots=True)
class SessionRecord:
    sessionid: str
    sessionid_sign: str | None
    storage_state_path: Path
    captured_at: datetime
    username: str | None = None


@dataclass(frozen=True, slots=True)
class SessionStatus:
    authenticated: bool
    username: str | None = None
    plan: str | None = None
    expires_hint: str | None = None


def storage_state_payload(record: SessionRecord) -> dict[str, Any]:
    cookies = [
        {
            "name": name,
            "value": value,
            "domain": ".tradingview.com",
            "path": "/",
            "expires": -1,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
        for name, value in cookie_jar(record).items()
    ]
    return {"cookies": cookies, "origins": []}


def write_storage_state(record: SessionRecord) -> Path:
    record.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    record.storage_state_path.write_text(
        json.dumps(storage_state_payload(record), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(record.storage_state_path, 0o600)
    return record.storage_state_path


def session_path(path: Path | None = None) -> Path:
    return path or default_session_path()


def storage_state_path(path: Path | None = None) -> Path:
    return path or default_storage_state_path()


def _encode(record: SessionRecord) -> dict[str, Any]:
    return {
        "sessionid": record.sessionid,
        "sessionid_sign": record.sessionid_sign,
        "storage_state_path": str(record.storage_state_path),
        "captured_at": record.captured_at.isoformat(),
        "username": record.username,
    }


def _decode(data: dict[str, Any]) -> SessionRecord:
    sessionid = str(data.get("sessionid", "")).strip()
    if not sessionid:
        raise SessionRequiredError(
            "No TradingView session is stored.",
            hint="Run `tvcli auth import-cookie` or `tvcli auth login` first.",
        )
    sessionid_sign = data.get("sessionid_sign")
    storage_path = Path(
        str(data.get("storage_state_path") or default_storage_state_path())
    ).expanduser()
    captured_at = datetime.fromisoformat(
        str(data.get("captured_at") or datetime.now(tz=UTC).isoformat())
    )
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    username = data.get("username")
    return SessionRecord(
        sessionid=sessionid,
        sessionid_sign=str(sessionid_sign) if sessionid_sign is not None else None,
        storage_state_path=storage_path,
        captured_at=captured_at,
        username=str(username) if username is not None else None,
    )


def load_session(path: Path | None = None) -> SessionRecord | None:
    session_file = session_path(path)
    if not session_file.exists():
        return None
    data = json.loads(session_file.read_text(encoding="utf-8"))
    return _decode(data)


def save_session(record: SessionRecord, path: Path | None = None) -> SessionRecord:
    session_file = session_path(path)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(_encode(record), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(session_file, 0o600)
    return record


def build_session_record(
    *,
    sessionid: str,
    sessionid_sign: str | None = None,
    username: str | None = None,
    storage_state_path_value: Path | None = None,
) -> SessionRecord:
    return SessionRecord(
        sessionid=sessionid,
        sessionid_sign=sessionid_sign,
        storage_state_path=storage_state_path(storage_state_path_value),
        captured_at=datetime.now(tz=UTC),
        username=username,
    )


def save_credentials(
    *,
    sessionid: str,
    sessionid_sign: str | None = None,
    username: str | None = None,
    path: Path | None = None,
    storage_state_path_value: Path | None = None,
) -> SessionRecord:
    record = save_session(
        build_session_record(
            sessionid=sessionid,
            sessionid_sign=sessionid_sign,
            username=username,
            storage_state_path_value=storage_state_path_value,
        ),
        path=path,
    )
    write_storage_state(record)
    return record


def clear_session(path: Path | None = None) -> dict[str, bool]:
    session_file = session_path(path)
    record = load_session(path)
    deleted = False
    if session_file.exists():
        session_file.unlink()
        deleted = True
    storage_deleted = False
    storage_file = (
        record.storage_state_path
        if record is not None
        else (default_storage_state_path())
    )
    if storage_file.exists():
        storage_file.unlink()
        storage_deleted = True
    return {"session": deleted, "storage_state": storage_deleted}


def require_session(path: Path | None = None) -> SessionRecord:
    record = load_session(path)
    if record is None:
        raise SessionRequiredError(
            "No TradingView session is stored.",
            hint="Run `tvcli auth import-cookie` or `tvcli auth login` first.",
        )
    return record


def cookie_jar(record: SessionRecord) -> dict[str, str]:
    cookies = {"sessionid": record.sessionid}
    if record.sessionid_sign:
        cookies["sessionid_sign"] = record.sessionid_sign
    return cookies


def validate_session(record: SessionRecord, timeout: float = 15.0) -> SessionStatus:
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": "tvcli/1.0.0"},
            cookies=cookie_jar(record),
        ) as client:
            response = client.get(
                "https://www.tradingview.com/chart/?symbol=NASDAQ:AAPL"
            )
    except httpx.HTTPError as exc:  # pragma: no cover - network dependent
        raise NetworkError(
            "Unable to validate the TradingView session.",
            hint="Check connectivity and retry.",
        ) from exc
    url = str(response.url).lower()
    body = response.text.lower()
    if (
        response.status_code in {401, 403}
        or "signin" in url
        or "captcha" in url
        or "challenge" in url
        or "cf-chl" in body
        or "verify you are human" in body
    ):
        raise SessionExpiredError(
            "Stored TradingView session is no longer valid.",
            hint=(
                "Refresh the session with `tvcli auth login` or "
                "`tvcli auth import-cookie`."
            ),
        )
    return SessionStatus(
        authenticated=True,
        username=record.username,
        plan=None,
        expires_hint=f"Captured at {record.captured_at.isoformat()}",
    )
