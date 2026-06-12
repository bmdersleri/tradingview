"""TradingView chart screenshot helper."""

from __future__ import annotations

import hashlib
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any

from ..auth.session import require_session
from ..errors import BrowserError, CaptchaDetectedError

DEFAULT_CHROMIUM_ARGS = ("--disable-blink-features=AutomationControlled",)
SELECTORS = {
    "chart_canvas": "canvas",
    "chart_root": "[data-name='pane-legend-source-item']",
    "chart_error_text": "Something went wrong",
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


def _launch_browser(playwright: Any, *, headless: bool, args: tuple[str, ...]) -> Any:
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": list(args),
    }
    if which("google-chrome") or which("google-chrome-stable"):
        launch_kwargs["channel"] = "chrome"
    return playwright.chromium.launch(**launch_kwargs)


def create_browser_context(
    playwright: Any,
    *,
    storage_state_path: Path | None,
    config: BrowserSessionConfig,
) -> tuple[Any, Any]:
    browser = _launch_browser(
        playwright,
        headless=config.headless,
        args=config.chromium_args,
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
        if (
            "accounts/signin" in url
            or "accounts/login" in url
            or "captcha" in url
            or "challenge" in url
        ):
            return True
    except Exception:
        pass
    try:
        content = str(page.locator("body").inner_text(timeout=1000)).lower()
    except Exception:
        try:
            content = str(page.content()).lower()
        except Exception:
            content = ""
    try:
        if "captcha" in content or "verify you are human" in content:
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


def _dismiss_chart_error_dialog(page: Any) -> bool:
    try:
        dialog = page.get_by_text(SELECTORS["chart_error_text"], exact=False)
        if not dialog.count():
            return False
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        return True
    except Exception:
        return False


# Below this fraction of non-background pixels the chart drawing area is treated
# as empty. A real candle chart easily clears it; an authenticated account layout
# that fails to render leaves a near-uniform canvas well under the threshold.
BLANK_CANVAS_CONTENT_RATIO = 0.002

# Runs in the page: samples every 2d canvas and returns the highest fraction of
# pixels that differ from the top-left (background) pixel. WebGL canvases and
# tainted/unreadable canvases are skipped rather than failing the whole check.
_CANVAS_CONTENT_JS = """
() => {
  const canvases = Array.from(document.querySelectorAll('canvas'));
  let best = 0;
  for (const c of canvases) {
    const w = c.width, h = c.height;
    if (!w || !h) continue;
    let ctx;
    try { ctx = c.getContext('2d'); } catch (e) { ctx = null; }
    if (!ctx) continue;
    let data;
    try { data = ctx.getImageData(0, 0, w, h).data; } catch (e) { continue; }
    if (!data.length) continue;
    const step = Math.max(1, Math.floor((w * h) / 20000)) * 4;
    const br = data[0], bg = data[1], bb = data[2];
    let nonEmpty = 0, total = 0;
    for (let i = 0; i < data.length; i += step) {
      total++;
      if (data[i + 3] === 0) continue;
      const diff = Math.abs(data[i] - br) + Math.abs(data[i + 1] - bg)
        + Math.abs(data[i + 2] - bb);
      if (diff > 24) nonEmpty++;
    }
    if (total) best = Math.max(best, nonEmpty / total);
  }
  return best;
}
"""


def _canvas_content_ratio(page: Any) -> float | None:
    try:
        value = page.evaluate(_CANVAS_CONTENT_JS)
    except Exception:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canvas_looks_blank(page: Any) -> bool:
    ratio = _canvas_content_ratio(page)
    # An unreadable canvas (ratio is None) is left alone: we never trigger the
    # anonymous fallback unless we can positively measure an empty drawing area.
    if ratio is None:
        return False
    return ratio < BLANK_CANVAS_CONTENT_RATIO


def _open_chart_page(
    context: Any,
    request: ChartRequest,
    timeout_ms: int,
    *,
    check_login_wall: bool,
) -> tuple[Any, bool]:
    page = context.new_page()
    page.goto(
        chart_url(request.symbol, request.interval, request.theme),
        wait_until="domcontentloaded",
    )
    with suppress(Exception):
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    if check_login_wall and _looks_like_login_wall(page):
        raise CaptchaDetectedError(
            "TradingView requested re-authentication.",
            hint=(
                "Refresh the session with `tvcli auth login` or "
                "`tvcli auth import-cookie`."
            ),
        )
    _add_studies(page, request.studies)
    chart_error_dismissed = _dismiss_chart_error_dialog(page)
    wait_for_canvas_stability(page, timeout_ms=timeout_ms)
    return page, chart_error_dismissed


def _capture_chart(page: Any, out: Path, *, chart_error_dismissed: bool) -> None:
    locator = page.locator(SELECTORS["chart_canvas"])
    if locator.count() and not chart_error_dismissed:
        locator.first.screenshot(path=str(out))
    else:
        page.screenshot(path=str(out), full_page=True)


def shot_chart(request: ChartRequest, timeout_ms: int = 15_000) -> dict[str, Any]:
    record = require_session()
    sync_playwright = _load_playwright()
    request.out.parent.mkdir(parents=True, exist_ok=True)
    config = BrowserSessionConfig(
        headless=True,
        width=request.width,
        height=request.height,
    )
    with sync_playwright() as playwright:
        browser = None
        anonymous_fallback = False
        try:
            # Attempt 1: the authenticated layout via the saved storage_state.
            browser, context = create_browser_context(
                playwright,
                storage_state_path=record.storage_state_path,
                config=config,
            )
            page, chart_error_dismissed = _open_chart_page(
                context, request, timeout_ms, check_login_wall=True
            )
            if _canvas_looks_blank(page):
                # The account layout rendered an empty drawing area. Retry once
                # in an anonymous context, which loads the public chart that is
                # known to render candles for the same symbol.
                with suppress(Exception):
                    browser.close()
                browser, context = create_browser_context(
                    playwright,
                    storage_state_path=None,
                    config=config,
                )
                anonymous_fallback = True
                page, chart_error_dismissed = _open_chart_page(
                    context, request, timeout_ms, check_login_wall=False
                )
            _capture_chart(
                page, request.out, chart_error_dismissed=chart_error_dismissed
            )
            return {
                "path": str(request.out.resolve()),
                "symbol": request.symbol,
                "interval": request.interval,
                "bytes": request.out.stat().st_size,
                "anonymous_fallback": anonymous_fallback,
            }
        finally:
            with suppress(Exception):
                if browser is not None:
                    browser.close()
