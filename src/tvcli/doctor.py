"""Environment diagnostics for tvcli."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.util import find_spec
from shutil import which
from typing import Any

import httpx

from .auth.session import load_session, validate_session
from .cache import SQLiteTTLCache
from .config import default_cache_path
from .errors import TvcliError


@dataclass(slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    hint: str | None = None


def _pass(name: str, detail: str) -> DoctorCheck:
    return DoctorCheck(name=name, ok=True, detail=detail)


def _fail(name: str, detail: str, hint: str) -> DoctorCheck:
    return DoctorCheck(name=name, ok=False, detail=detail, hint=hint)


def check_dependencies() -> DoctorCheck:
    modules = (
        "typer",
        "rich",
        "httpx",
        "pydantic",
        "fastapi",
        "uvicorn",
        "playwright",
        "tradingview_screener",
        "tradingview_ta",
    )
    missing = [module for module in modules if find_spec(module) is None]
    if missing:
        return _fail(
            "dependencies",
            f"Missing modules: {', '.join(sorted(missing))}",
            "Install the project extras and retry.",
        )
    return _pass("dependencies", "Required Python modules are importable.")


def check_chromium() -> DoctorCheck:
    candidates = (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    )
    browser = next((candidate for candidate in candidates if which(candidate)), None)
    if browser is None:
        return _fail(
            "chromium",
            "No Chromium-compatible browser was found on PATH.",
            "Install Chromium with `playwright install chromium`.",
        )
    return _pass("chromium", f"Found browser binary: {browser}.")


def check_cache() -> DoctorCheck:
    cache = SQLiteTTLCache(default_cache_path())
    key = "__tvcli_doctor__"
    try:
        cache.set(key, {"ok": True}, ttl_seconds=5)
        value = cache.get(key)
        cache.clear()
    except Exception as exc:  # pragma: no cover - defensive
        return _fail(
            "cache",
            f"Cache write failed: {exc}",
            "Check filesystem permissions for the cache directory.",
        )
    if value != {"ok": True}:
        return _fail(
            "cache",
            "Cache round-trip did not return the expected value.",
            "Check filesystem permissions for the cache directory.",
        )
    return _pass("cache", f"Cache is writable at {default_cache_path()}.")


def check_session() -> DoctorCheck:
    record = load_session()
    if record is None:
        return _fail(
            "session",
            "No stored TradingView session was found.",
            "Run `tvcli auth import-cookie` or `tvcli auth login` first.",
        )
    try:
        status = validate_session(record)
    except TvcliError as exc:
        return _fail(
            "session",
            exc.message,
            exc.hint,
        )
    return _pass(
        "session",
        (
            f"Authenticated as {status.username or 'unknown'}; captured at "
            f"{status.expires_hint}."
        ),
    )


def check_upstream() -> DoctorCheck:
    try:
        response = httpx.get("https://www.tradingview.com", timeout=10.0)
    except httpx.HTTPError as exc:  # pragma: no cover - network dependent
        return _fail(
            "upstream",
            f"Unable to reach TradingView: {exc}",
            "Check network connectivity and retry.",
        )
    if response.status_code >= 400:
        return _fail(
            "upstream",
            f"TradingView returned HTTP {response.status_code}.",
            "Check network connectivity and retry.",
        )
    return _pass("upstream", "TradingView responds to HTTPS requests.")


def run_doctor() -> dict[str, Any]:
    checks = [
        check_dependencies(),
        check_chromium(),
        check_cache(),
        check_session(),
        check_upstream(),
    ]
    all_ok = all(check.ok for check in checks)
    return {
        "all_ok": all_ok,
        "checks": [asdict(check) for check in checks],
    }
