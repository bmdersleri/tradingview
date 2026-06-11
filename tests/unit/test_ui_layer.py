from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from tvcli.errors import UpstreamChangedError
from tvcli.layers import ui


class FakeKeyboard:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    def press(self, key: str) -> None:
        self.page.calls.append(("keyboard.press", key))

    def type(self, text: str) -> None:
        self.page.calls.append(("keyboard.type", text))


class FakeRow:
    def __init__(self, text: str, data_id: str | None = None) -> None:
        self._text = text
        self._data_id = data_id

    def inner_text(self) -> str:
        return self._text

    def get_attribute(self, name: str) -> str | None:
        if name == "data-id":
            return self._data_id
        return None


class FakeLocator:
    def __init__(
        self,
        page: FakePage,
        selector: str,
        *,
        rows: list[FakeRow] | None = None,
        count_value: int = 1,
    ) -> None:
        self.page = page
        self.selector = selector
        self.rows = rows or []
        self.count_value = count_value

    def count(self) -> int:
        return len(self.rows) if self.rows else self.count_value

    @property
    def first(self) -> FakeLocator:
        return self

    def click(self) -> None:
        self.page.calls.append(("click", self.selector))

    def fill(self, value: str) -> None:
        self.page.calls.append(("fill", self.selector, value))

    def select_option(self, value: str) -> None:
        self.page.calls.append(("select", self.selector, value))

    def press(self, key: str) -> None:
        self.page.calls.append(("press", self.selector, key))

    def nth(self, index: int) -> FakeRow:
        return self.rows[index]


class FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.keyboard = FakeKeyboard(self)
        self.locators: dict[str, FakeLocator] = {}

    def set_default_timeout(self, timeout_ms: int) -> None:
        self.calls.append(("timeout", timeout_ms))

    def set_default_navigation_timeout(self, timeout_ms: int) -> None:
        self.calls.append(("nav-timeout", timeout_ms))

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.calls.append(("goto", url, wait_until))

    def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    def locator(self, selector: str) -> FakeLocator:
        if selector not in self.locators:
            self.locators[selector] = FakeLocator(self, selector)
        return self.locators[selector]


@contextmanager
def page_context(page: FakePage):
    yield page


def test_selector_registry_and_error_mapping() -> None:
    assert {"ALERT_DIALOG_OPEN_BTN", "WATCHLIST_ROW", "PINE_SAVE_BTN"} <= set(
        ui.SELECTORS
    )

    page = FakePage()
    page.locators[ui.SELECTORS["ALERT_SAVE_BTN"]] = FakeLocator(
        page,
        ui.SELECTORS["ALERT_SAVE_BTN"],
        count_value=0,
    )

    with pytest.raises(UpstreamChangedError) as exc_info:
        ui.click_selector(page, "ALERT_SAVE_BTN")

    assert "ALERT_SAVE_BTN" in str(exc_info.value.hint)


def test_ui_layer_automation_paths(monkeypatch, tmp_path: Path) -> None:
    page = FakePage()
    page.locators[ui.SELECTORS["ALERT_ROW"]] = FakeLocator(
        page,
        ui.SELECTORS["ALERT_ROW"],
        rows=[FakeRow("1"), FakeRow("2")],
    )
    page.locators[ui.SELECTORS["WATCHLIST_ROW"]] = FakeLocator(
        page,
        ui.SELECTORS["WATCHLIST_ROW"],
        rows=[FakeRow("AAPL"), FakeRow("TSLA")],
    )
    monkeypatch.setattr(ui, "_with_page", lambda *args, **kwargs: page_context(page))

    created = ui.run_alert_create(
        ui.AlertCreateRequest(
            symbol="BIST:THYAO",
            condition="Crossing",
            value=320.0,
            message="msg",
            webhook="https://example.com/hook",
        )
    )
    listed = ui.run_alert_list()
    deleted = ui.run_alert_delete(ui.AlertDeleteRequest(alert_id="1"))
    deleted_all = ui.run_alert_delete(ui.AlertDeleteRequest(delete_all=True))
    added = ui.run_watchlist_add(
        ui.WatchlistAddRequest(symbols=("BIST:THYAO", "NASDAQ:NVDA"), list_name="main")
    )

    pine_file = tmp_path / "script.pine"
    pine_file.write_text("indicator('x')", encoding="utf-8")
    pushed = ui.run_pine_push(
        ui.PinePushRequest(file_path=pine_file, name="demo", save_only=False)
    )
    exported = ui.run_watchlist_export("main")

    assert created["created"] is True
    assert listed["returned"] == 2
    assert deleted["deleted"] == "1"
    assert deleted_all["deleted"] == "all"
    assert added["returned"] == 2
    assert pushed["name"] == "demo"
    assert exported["returned"] == 2
    assert ("click", ui.SELECTORS["ALERT_SAVE_BTN"]) in page.calls
