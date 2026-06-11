from __future__ import annotations

from pathlib import Path

import pytest

from tvcli.auth.login import run_login
from tvcli.errors import BrowserError


class FakeLocator:
    def __init__(self, text: str = "demo-user") -> None:
        self._text = text

    def count(self) -> int:
        return 1

    @property
    def first(self) -> FakeLocator:
        return self

    def text_content(self) -> str:
        return self._text


class FakePage:
    def __init__(self) -> None:
        self.url = "https://www.tradingview.com/chart/"

    def goto(self, *_args, **_kwargs) -> None:
        return None

    def content(self) -> str:
        return "<html>ok</html>"

    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator()

    def wait_for_timeout(self, _ms: int) -> None:
        return None


class FakeContext:
    def __init__(self, storage_state_path: Path) -> None:
        self.storage_state_path = storage_state_path
        self.page = FakePage()

    def new_page(self) -> FakePage:
        return self.page

    def cookies(self) -> list[dict[str, str]]:
        return [
            {"name": "sessionid", "value": "abc"},
            {"name": "sessionid_sign", "value": "def"},
        ]

    def storage_state(self, path: str) -> None:
        Path(path).write_text("{}", encoding="utf-8")


class FakeBrowser:
    def __init__(self, storage_state_path: Path) -> None:
        self.context = FakeContext(storage_state_path)
        self.closed = False

    def new_context(self, **_kwargs) -> FakeContext:
        return self.context

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, storage_state_path: Path) -> None:
        self.storage_state_path = storage_state_path

    def launch(self, **_kwargs) -> FakeBrowser:
        return FakeBrowser(self.storage_state_path)


class FakePlaywright:
    def __init__(self, storage_state_path: Path) -> None:
        self.chromium = FakeChromium(storage_state_path)


class FakePlaywrightContext:
    def __init__(self, storage_state_path: Path) -> None:
        self.playwright = FakePlaywright(storage_state_path)

    def __enter__(self) -> FakePlaywright:
        return self.playwright

    def __exit__(self, *_exc) -> None:
        return None


def test_run_login_saves_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    storage_state_path = tmp_path / "storage_state.json"
    monkeypatch.setattr(
        "tvcli.auth.login._load_playwright",
        lambda: lambda: FakePlaywrightContext(storage_state_path),
    )

    record = run_login(
        headless=True,
        timeout_ms=1000,
        storage_state_path=storage_state_path,
    )

    assert record.sessionid == "abc"
    assert record.sessionid_sign == "def"
    assert record.username == "demo-user"
    assert storage_state_path.exists()
    assert (tmp_path / "config" / "tvcli" / "session.json").exists()


def test_run_login_requires_display_when_headed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    with pytest.raises(BrowserError):
        run_login(
            headless=False,
            timeout_ms=1000,
            storage_state_path=tmp_path / "storage_state.json",
        )
