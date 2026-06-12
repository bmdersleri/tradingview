"""Playwright-assisted TradingView login flow."""

from __future__ import annotations

import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..config import default_storage_state_path
from ..errors import BrowserError, CaptchaDetectedError, SessionExpiredError
from ..layers.chart import _launch_browser
from .session import (
    SessionRecord,
    build_session_record,
    cookie_jar,
    save_session,
)

TRADINGVIEW_SIGNIN_URL = "https://www.tradingview.com/accounts/signin/"


def _load_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise BrowserError(
            "Playwright is unavailable.",
            hint="Install the `browser` extra to enable auth login and chart shots.",
        ) from exc
    return sync_playwright


def _captcha_detected(page: Any) -> bool:
    with suppress(Exception):
        url = str(getattr(page, "url", "")).lower()
        if "captcha" in url or "challenge" in url:
            return True
    with suppress(Exception):
        content = str(page.content()).lower()
        if "captcha" in content or "cloudflare" in content:
            return True
    return False


def _extract_session_cookies(context: Any) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for cookie in context.cookies():
        name = str(cookie.get("name", ""))
        value = str(cookie.get("value", ""))
        if name in {"sessionid", "sessionid_sign"} and value:
            cookies[name] = value
    return cookies


def _detect_username(page: Any) -> str | None:
    selectors = [
        "[data-name='header-user-menu']",
        "[data-role='user-menu']",
        "a[href*='/u/']",
    ]
    for selector in selectors:
        with suppress(Exception):
            locator = page.locator(selector)
            if locator.count():
                text = locator.first.text_content()
                if isinstance(text, str):
                    stripped = text.strip()
                    if stripped:
                        return stripped
    return None


def run_login(
    *,
    headless: bool,
    timeout_ms: int,
    storage_state_path: Path | None = None,
) -> SessionRecord:
    if not headless and not os.environ.get("DISPLAY"):
        raise BrowserError(
            "Headed login requires a display server.",
            hint="Use xvfb-run or pass --headless.",
        )
    storage_path = storage_state_path or default_storage_state_path()
    sync_playwright = _load_playwright()
    try:
        with sync_playwright() as playwright:
            browser = None
            browser = _launch_browser(
                playwright,
                headless=headless,
                args=("--disable-blink-features=AutomationControlled",),
            )
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    ignore_https_errors=True,
                    storage_state=str(storage_path) if storage_path.exists() else None,
                )
                page = context.new_page()
                page.goto(TRADINGVIEW_SIGNIN_URL, wait_until="domcontentloaded")
                deadline = time.monotonic() + timeout_ms / 1000
                cookies = _extract_session_cookies(context)
                while time.monotonic() < deadline:
                    if _captcha_detected(page):
                        raise CaptchaDetectedError(
                            "TradingView presented a CAPTCHA challenge.",
                            hint=(
                                "Use `tvcli auth import-cookie` from a browser where "
                                "you already solved the challenge."
                            ),
                        )
                    cookies = _extract_session_cookies(context)
                    if cookies.get("sessionid"):
                        break
                    with suppress(Exception):
                        page.wait_for_timeout(1000)
                else:
                    raise SessionExpiredError(
                        "Login did not complete before timeout.",
                        hint=(
                            "Finish sign-in in the browser or import cookies manually."
                        ),
                    )
                context.storage_state(path=str(storage_path))
                record = build_session_record(
                    sessionid=cookies["sessionid"],
                    sessionid_sign=cookies.get("sessionid_sign"),
                    username=_detect_username(page),
                    storage_state_path_value=storage_path,
                )
                return save_session(record)
            finally:
                if browser is not None:
                    with suppress(Exception):
                        browser.close()
    except CaptchaDetectedError:
        raise
    except SessionExpiredError:
        raise
    except BrowserError:
        raise
    except Exception as exc:  # pragma: no cover - browser/runtime dependent
        raise BrowserError(
            "TradingView login flow failed.",
            hint="Inspect the browser session or try auth import-cookie.",
        ) from exc


def session_from_cookies(
    sessionid: str,
    sessionid_sign: str | None = None,
    *,
    storage_state_path: Path | None = None,
) -> SessionRecord:
    return build_session_record(
        sessionid=sessionid,
        sessionid_sign=sessionid_sign,
        storage_state_path_value=storage_state_path or default_storage_state_path(),
    )


def session_cookie_payload(record: SessionRecord) -> dict[str, str]:
    return cookie_jar(record)
