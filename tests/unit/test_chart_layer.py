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
    def __init__(self, output_path: Path, content_ratio: float = 1.0) -> None:
        self.url = "https://www.tradingview.com/chart/"
        self.output_path = output_path
        self.locator_obj = FakeLocator(output_path)
        self.keyboard = FakeKeyboard()
        self.content_ratio = content_ratio

    def goto(self, *_args, **_kwargs) -> None:
        return None

    def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    def content(self) -> str:
        return "<html>ok</html>"

    def locator(self, _selector: str) -> FakeLocator:
        return self.locator_obj

    def evaluate(self, _script: str) -> float:
        return self.content_ratio

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def screenshot(self, path: str, full_page: bool = False) -> None:
        Path(path).write_bytes(b"fallback")


class FakeContext:
    def __init__(self, output_path: Path, content_ratio: float = 1.0) -> None:
        self.page = FakePage(output_path, content_ratio=content_ratio)

    def new_page(self) -> FakePage:
        return self.page


class FakeBrowser:
    def __init__(self, output_path: Path, content_ratio: float = 1.0) -> None:
        self.context = FakeContext(output_path, content_ratio=content_ratio)
        self.closed = False

    def new_context(self, **_kwargs) -> FakeContext:
        return self.context

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(
        self, output_path: Path, content_ratios: list[float] | None = None
    ) -> None:
        self.output_path = output_path
        # One ratio per launch; defaults to a single fully-rendered chart.
        self.content_ratios = list(content_ratios or [1.0])
        self.launches: list[FakeBrowser] = []

    def launch(self, **_kwargs) -> FakeBrowser:
        ratio = self.content_ratios[
            min(len(self.launches), len(self.content_ratios) - 1)
        ]
        browser = FakeBrowser(self.output_path, content_ratio=ratio)
        self.launches.append(browser)
        return browser


class FakePlaywright:
    def __init__(
        self, output_path: Path, content_ratios: list[float] | None = None
    ) -> None:
        self.chromium = FakeChromium(output_path, content_ratios=content_ratios)


class FakePlaywrightContext:
    def __init__(
        self, output_path: Path, content_ratios: list[float] | None = None
    ) -> None:
        self.playwright = FakePlaywright(output_path, content_ratios=content_ratios)

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
    assert payload["anonymous_fallback"] is False
    assert output_path.exists()
    assert "symbol=BIST:THYAO" in chart_url("BIST:THYAO", "1d")


def test_chart_shot_anonymous_fallback_on_blank_layout(
    monkeypatch, tmp_path: Path
) -> None:
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
    # First launch (authenticated) renders a blank canvas; the anonymous retry
    # renders candles.
    pw_context = FakePlaywrightContext(output_path, content_ratios=[0.0, 1.0])
    monkeypatch.setattr(
        "tvcli.layers.chart._load_playwright",
        lambda: lambda: pw_context,
    )

    payload = shot_chart(
        ChartRequest(symbol="BIST:THYAO", interval="1d", out=output_path)
    )

    assert payload["anonymous_fallback"] is True
    assert payload["bytes"] > 0
    assert output_path.exists()
    # Two browsers launched: authenticated attempt + anonymous fallback.
    assert len(pw_context.playwright.chromium.launches) == 2
    assert pw_context.playwright.chromium.launches[0].closed is True
