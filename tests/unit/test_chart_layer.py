from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tvcli.auth.session import SessionRecord
from tvcli.layers.chart import ChartRequest, chart_url, shot_chart


class FakeLocator:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.calls = 0

    def count(self) -> int:
        return 1

    @property
    def first(self) -> FakeLocator:
        return self

    def screenshot(self, path: str | None = None) -> bytes:
        self.calls += 1
        payload = b"stable-canvas"
        if path is not None:
            Path(path).write_bytes(payload)
        return payload


class FakeKeyboard:
    def press(self, _key: str) -> None:
        return None


class FakePage:
    def __init__(self, output_path: Path) -> None:
        self.url = "https://www.tradingview.com/chart/"
        self.output_path = output_path
        self.locator_obj = FakeLocator(output_path)
        self.keyboard = FakeKeyboard()

    def goto(self, *_args, **_kwargs) -> None:
        return None

    def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    def content(self) -> str:
        return "<html>ok</html>"

    def locator(self, _selector: str) -> FakeLocator:
        return self.locator_obj

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def screenshot(self, path: str, full_page: bool = False) -> None:
        Path(path).write_bytes(b"fallback")


class FakeContext:
    def __init__(self, output_path: Path) -> None:
        self.page = FakePage(output_path)

    def new_page(self) -> FakePage:
        return self.page


class FakeBrowser:
    def __init__(self, output_path: Path) -> None:
        self.context = FakeContext(output_path)

    def new_context(self, **_kwargs) -> FakeContext:
        return self.context

    def close(self) -> None:
        return None


class FakeChromium:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path

    def launch(self, **_kwargs) -> FakeBrowser:
        return FakeBrowser(self.output_path)


class FakePlaywright:
    def __init__(self, output_path: Path) -> None:
        self.chromium = FakeChromium(output_path)


class FakePlaywrightContext:
    def __init__(self, output_path: Path) -> None:
        self.playwright = FakePlaywright(output_path)

    def __enter__(self) -> FakePlaywright:
        return self.playwright

    def __exit__(self, *_exc) -> None:
        return None


def test_chart_shot_success(monkeypatch, tmp_path: Path) -> None:
    output_path = tmp_path / "chart.png"
    record = SessionRecord(
        sessionid="abc",
        sessionid_sign="def",
        storage_state_path=tmp_path / "storage_state.json",
        captured_at=datetime.now(tz=UTC),
        username="demo",
    )
    record.storage_state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("tvcli.layers.chart.require_session", lambda: record)
    monkeypatch.setattr(
        "tvcli.layers.chart._load_playwright",
        lambda: lambda: FakePlaywrightContext(output_path),
    )

    payload = shot_chart(
        ChartRequest(
            symbol="BIST:THYAO",
            interval="1d",
            out=output_path,
        )
    )

    assert payload["path"] == str(output_path.resolve())
    assert payload["bytes"] > 0
    assert output_path.exists()
    assert "symbol=BIST:THYAO" in chart_url("BIST:THYAO", "1d")
