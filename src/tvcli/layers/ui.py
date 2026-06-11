"""TradingView UI automation helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..auth.session import require_session
from ..errors import UpstreamChangedError, UsageError
from . import chart

DEFAULT_UI_SYMBOL = "NASDAQ:AAPL"
DEFAULT_UI_TIMEOUT_MS = 30_000

SELECTORS = {
    "ALERT_DIALOG_OPEN_BTN": "[data-name='alert-dialog-open']",
    "ALERT_CONDITION_SELECT": "[data-name='alert-condition-select']",
    "ALERT_VALUE_INPUT": "[data-name='alert-value-input']",
    "ALERT_MESSAGE_INPUT": "[data-name='alert-message-input']",
    "ALERT_WEBHOOK_INPUT": "[data-name='alert-webhook-input']",
    "ALERT_SAVE_BTN": "[data-name='alert-save-button']",
    "ALERT_ROW": "[data-name='alert-row']",
    "ALERT_DELETE_BTN": "[data-name='alert-delete-button']",
    "ALERT_DELETE_ALL_BTN": "[data-name='alert-delete-all-button']",
    "WATCHLIST_ADD_INPUT": "[data-name='watchlist-add-input']",
    "WATCHLIST_LIST_SELECT": "[data-name='watchlist-list-select']",
    "WATCHLIST_ROW": "[data-name='watchlist-row']",
    "WATCHLIST_EXPORT_BTN": "[data-name='watchlist-export-button']",
    "PINE_EDITOR_TEXTAREA": "[data-name='pine-editor']",
    "PINE_SAVE_BTN": "[data-name='pine-save-button']",
}


@dataclass(frozen=True, slots=True)
class AlertCreateRequest:
    symbol: str
    condition: str
    value: float
    message: str | None = None
    webhook: str | None = None


@dataclass(frozen=True, slots=True)
class AlertDeleteRequest:
    alert_id: str | None = None
    delete_all: bool = False


@dataclass(frozen=True, slots=True)
class WatchlistAddRequest:
    symbols: tuple[str, ...]
    list_name: str


@dataclass(frozen=True, slots=True)
class PinePushRequest:
    file_path: Path
    name: str
    save_only: bool = True


def _load_playwright() -> Any:
    return chart._load_playwright()


def _upstream_changed(
    selector_key: str, action: str, exc: Exception | None = None
) -> UpstreamChangedError:
    return UpstreamChangedError(
        f"Unable to {action}.",
        hint=f"Selector key: {selector_key}",
    )


def _ensure_locator(page: Any, selector_key: str) -> Any:
    selector = SELECTORS[selector_key]
    try:
        locator = page.locator(selector)
        if locator.count() <= 0:
            raise _upstream_changed(selector_key, "find the element")
        return locator
    except UpstreamChangedError:
        raise
    except Exception as exc:  # pragma: no cover - browser/runtime dependent
        raise _upstream_changed(selector_key, "interact with the element", exc) from exc


@contextmanager
def _with_page(
    symbol: str | None = None, *, timeout_ms: int = DEFAULT_UI_TIMEOUT_MS
) -> Iterator[Any]:
    record = require_session()
    sync_playwright = _load_playwright()
    with sync_playwright() as playwright:
        browser = None
        browser, context = chart.create_browser_context(
            playwright,
            storage_state_path=record.storage_state_path,
            config=chart.BrowserSessionConfig(headless=True, width=1600, height=900),
        )
        try:
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.set_default_navigation_timeout(timeout_ms)
            page.goto(
                chart.chart_url(symbol or DEFAULT_UI_SYMBOL, "1d"),
                wait_until="domcontentloaded",
            )
            with suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            yield page
        finally:
            if browser is not None:
                with suppress(Exception):
                    browser.close()


def click_selector(page: Any, selector_key: str) -> None:
    try:
        locator = _ensure_locator(page, selector_key)
        locator.first.click()
    except UpstreamChangedError:
        raise
    except Exception as exc:  # pragma: no cover - browser/runtime dependent
        raise _upstream_changed(selector_key, "click the element", exc) from exc


def fill_selector(page: Any, selector_key: str, value: str) -> None:
    try:
        locator = _ensure_locator(page, selector_key)
        locator.first.fill(value)
    except UpstreamChangedError:
        raise
    except Exception as exc:  # pragma: no cover - browser/runtime dependent
        raise _upstream_changed(selector_key, "fill the element", exc) from exc


def select_selector(page: Any, selector_key: str, value: str) -> None:
    try:
        locator = _ensure_locator(page, selector_key)
        locator.first.select_option(value)
    except UpstreamChangedError:
        raise
    except Exception as exc:  # pragma: no cover - browser/runtime dependent
        raise _upstream_changed(selector_key, "select the option", exc) from exc


def press_selector(page: Any, selector_key: str, key: str) -> None:
    try:
        locator = _ensure_locator(page, selector_key)
        locator.first.press(key)
    except UpstreamChangedError:
        raise
    except Exception as exc:  # pragma: no cover - browser/runtime dependent
        raise _upstream_changed(
            selector_key, "press a key on the element", exc
        ) from exc


def _rows_from_locator(locator: Any) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    count = locator.count()
    for index in range(count):
        row = locator.nth(index)
        text = ""
        with suppress(Exception):
            text = str(row.inner_text()).strip()
        identifier = None
        with suppress(Exception):
            raw = row.get_attribute("data-id")
            if raw:
                identifier = str(raw)
        rows.append({"id": identifier, "text": text})
    return tuple(rows)


def _symbols_from_locator(locator: Any) -> tuple[str, ...]:
    symbols: list[str] = []
    count = locator.count()
    for index in range(count):
        row = locator.nth(index)
        text = ""
        with suppress(Exception):
            text = str(row.inner_text()).strip()
        if text:
            symbols.append(text)
    return tuple(symbols)


def run_alert_create(request: AlertCreateRequest) -> dict[str, Any]:
    with _with_page(request.symbol) as page:
        click_selector(page, "ALERT_DIALOG_OPEN_BTN")
        select_selector(page, "ALERT_CONDITION_SELECT", request.condition)
        fill_selector(page, "ALERT_VALUE_INPUT", str(request.value))
        if request.message:
            fill_selector(page, "ALERT_MESSAGE_INPUT", request.message)
        if request.webhook:
            fill_selector(page, "ALERT_WEBHOOK_INPUT", request.webhook)
        click_selector(page, "ALERT_SAVE_BTN")
    return {
        "symbol": request.symbol,
        "condition": request.condition,
        "value": request.value,
        "message": request.message,
        "webhook": request.webhook,
        "created": True,
    }


def run_alert_list() -> dict[str, Any]:
    with _with_page() as page:
        locator = _ensure_locator(page, "ALERT_ROW")
        rows = _rows_from_locator(locator)
    return {
        "returned": len(rows),
        "rows": rows,
    }


def run_alert_delete(request: AlertDeleteRequest) -> dict[str, Any]:
    if request.delete_all and request.alert_id is not None:
        raise UsageError(
            "Use either --id or --all, not both.",
            hint="Pass exactly one delete selector.",
        )
    if not request.delete_all and request.alert_id is None:
        raise UsageError(
            "An alert id or --all is required.",
            hint="Pass --id ID or --all.",
        )
    with _with_page() as page:
        if request.delete_all:
            click_selector(page, "ALERT_DELETE_ALL_BTN")
            deleted = "all"
        else:
            click_selector(page, "ALERT_DELETE_BTN")
            deleted = request.alert_id if request.alert_id is not None else ""
    return {"deleted": deleted}


def run_watchlist_add(request: WatchlistAddRequest) -> dict[str, Any]:
    with _with_page(request.symbols[0] if request.symbols else None) as page:
        fill_selector(page, "WATCHLIST_ADD_INPUT", ",".join(request.symbols))
        select_selector(page, "WATCHLIST_LIST_SELECT", request.list_name)
        press_selector(page, "WATCHLIST_ADD_INPUT", "Enter")
    return {
        "list": request.list_name,
        "added": list(request.symbols),
        "returned": len(request.symbols),
    }


def run_watchlist_export(list_name: str) -> dict[str, Any]:
    with _with_page() as page:
        select_selector(page, "WATCHLIST_LIST_SELECT", list_name)
        click_selector(page, "WATCHLIST_EXPORT_BTN")
        locator = _ensure_locator(page, "WATCHLIST_ROW")
        symbols = _symbols_from_locator(locator)
    return {
        "list": list_name,
        "returned": len(symbols),
        "symbols": list(symbols),
    }


def run_pine_push(request: PinePushRequest) -> dict[str, Any]:
    content = request.file_path.read_text(encoding="utf-8")
    with _with_page() as page:
        editor = _ensure_locator(page, "PINE_EDITOR_TEXTAREA")
        editor.first.click()
        with suppress(Exception):
            page.keyboard.press("Control+A")
        with suppress(Exception):
            page.keyboard.type(content)
        if request.save_only:
            click_selector(page, "PINE_SAVE_BTN")
        else:
            click_selector(page, "PINE_SAVE_BTN")
            with suppress(Exception):
                page.keyboard.press("Alt+Enter")
    return {
        "path": str(request.file_path.resolve()),
        "name": request.name,
        "bytes": len(content.encode("utf-8")),
        "save_only": request.save_only,
    }
