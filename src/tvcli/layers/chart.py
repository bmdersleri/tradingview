"""TradingView chart screenshot helper."""

from __future__ import annotations

import hashlib
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..auth.session import require_session
from ..errors import BrowserError, CaptchaDetectedError

DEFAULT_CHROMIUM_ARGS = ("--disable-blink-features=AutomationControlled",)
SELECTORS = {
    "chart_canvas": "canvas",
    "chart_root": "[data-name='pane-legend-source-item']",
}


@dataclass(frozen=True, slots=True)
class BrowserSessionConfig:
    headless: bool = True
    width: int = 1600
    height: int = 900
    chromium_args: tuple[str, ...] = DEFAULT_CHROMIUM_ARGS


@dataclass(frozen=True, slots=True)
class ChartRequest:
    symbol: str
    interval: str
    out: Path
    width: int = 1600
    height: int = 900
    theme: str = "dark"
    studies: tuple[str, ...] = ()


def _load_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise BrowserError(
            "Playwright is unavailable.",
            hint="Install the `browser` extra to enable chart shots.",
        ) from exc
    return sync_playwright


def create_browser_context(
    playwright: Any,
    *,
    storage_state_path: Path | None,
    config: BrowserSessionConfig,
) -> tuple[Any, Any]:
    browser = playwright.chromium.launch(
        headless=config.headless,
        args=list(config.chromium_args),
    )
    context = browser.new_context(
        viewport={"width": config.width, "height": config.height},
        ignore_https_errors=True,
        storage_state=str(storage_state_path)
        if storage_state_path and storage_state_path.exists()
        else None,
    )
    return browser, context


def chart_url(symbol: str, interval: str, theme: str = "dark") -> str:
    params = [f"symbol={symbol}", f"interval={interval}", f"theme={theme}"]
    return "https://www.tradingview.com/chart/?" + "&".join(params)


def _looks_like_login_wall(page: Any) -> bool:
    try:
        url = str(getattr(page, "url", "")).lower()
        if "signin" in url or "login" in url:
            return True
    except Exception:
        pass
    try:
        content = str(page.content()).lower()
        if "captcha" in content or "sign in" in content or "log in" in content:
            return True
    except Exception:
        pass
    return False


def _canvas_signature(page: Any, selector: str) -> str | None:
    locator = page.locator(selector)
    if not locator.count():
        return None
    try:
        image = locator.first.screenshot()
    except Exception:
        return None
    return hashlib.sha256(image).hexdigest()


def wait_for_canvas_stability(
    page: Any,
    selector: str = SELECTORS["chart_canvas"],
    timeout_ms: int = 15_000,
    sample_ms: int = 500,
) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    previous = None
    while time.monotonic() < deadline:
        signature = _canvas_signature(page, selector)
        if signature is not None and signature == previous:
            return
        previous = signature
        with suppress(Exception):
            page.wait_for_timeout(sample_ms)
    raise BrowserError(
        "Chart canvas did not stabilize in time.",
        hint="Retry with a larger timeout or inspect the chart DOM selectors.",
    )


def _add_studies(page: Any, studies: tuple[str, ...]) -> None:
    if not studies:
        return
    # Best-effort: TradingView study UI is fragile. Keep the hook for later
    # refinement without breaking the screenshot path.
    with suppress(Exception):
        page.keyboard.press("Alt+I")


def shot_chart(request: ChartRequest, timeout_ms: int = 15_000) -> dict[str, Any]:
    record = require_session()
    sync_playwright = _load_playwright()
    request.out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = None
        browser, context = create_browser_context(
            playwright,
            storage_state_path=record.storage_state_path,
            config=BrowserSessionConfig(
                headless=True,
                width=request.width,
                height=request.height,
            ),
        )
        try:
            page = context.new_page()
            page.goto(
                chart_url(request.symbol, request.interval, request.theme),
                wait_until="domcontentloaded",
            )
            with suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            if _looks_like_login_wall(page):
                raise CaptchaDetectedError(
                    "TradingView requested re-authentication.",
                    hint=(
                        "Refresh the session with `tvcli auth login` or "
                        "`tvcli auth import-cookie`."
                    ),
                )
            _add_studies(page, request.studies)
            wait_for_canvas_stability(page, timeout_ms=timeout_ms)
            locator = page.locator(SELECTORS["chart_canvas"])
            if locator.count():
                locator.first.screenshot(path=str(request.out))
            else:
                page.screenshot(path=str(request.out), full_page=True)
            return {
                "path": str(request.out.resolve()),
                "symbol": request.symbol,
                "interval": request.interval,
                "bytes": request.out.stat().st_size,
            }
        finally:
            if browser is not None:
                browser.close()
