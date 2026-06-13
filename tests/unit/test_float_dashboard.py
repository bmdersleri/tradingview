"""Tests for float_dashboard rendering layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from tvcli.errors import NotFoundError, UsageError
from tvcli.layers import freefloat as ff
from tvcli.layers.float_dashboard import DashboardRequest, run_dashboard
from tvcli.layers.freefloat_archive import ArchiveStore

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _seed_store(
    store: ArchiveStore, codes: list[str], ratio_base: float = 35.0
) -> None:
    records = []
    for i, code in enumerate(codes):
        records.append(
            ff.FloatRecord(
                code=code,
                isin=f"TR{i:06d}",
                name=code,
                float_shares=500.0 + i * 10,
                capital=1000.0 + i * 10,
                ratio=ratio_base + i * 2.0,
                date="10.06.2026",
            )
        )
    store.sync_records(tuple(records))


def test_deep_dive_renders_png(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    _seed_store(store, ["THYAO"])

    out = tmp_path / "deep.png"
    payload = run_dashboard(
        DashboardRequest(out=out, symbol="THYAO", width=400, height=300),
        store=store,
    )

    assert out.exists()
    assert out.read_bytes()[:8] == _PNG_MAGIC
    assert payload["bytes"] > 0
    assert payload["path"] == str(out.resolve())
    assert payload["mode"] == "symbol"
    assert payload["symbol"] == "THYAO"


def test_market_overview_renders_png(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    _seed_store(store, ["THYAO", "GARAN", "EREGL", "ENBYA", "ENPRA"], ratio_base=5.0)

    out = tmp_path / "market.png"
    payload = run_dashboard(
        DashboardRequest(out=out, market=True, top=3, width=400, height=300),
        store=store,
    )

    assert out.exists()
    assert out.read_bytes()[:8] == _PNG_MAGIC
    assert payload["bytes"] > 0
    assert payload["mode"] == "market"
    assert payload["symbol"] is None
    assert len(payload["leaderboard"]) <= 3


def test_neither_symbol_nor_market_raises_usage_error(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    with pytest.raises(UsageError):
        run_dashboard(
            DashboardRequest(out=tmp_path / "x.png"),
            store=store,
        )


def test_both_symbol_and_market_raises_usage_error(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    with pytest.raises(UsageError):
        run_dashboard(
            DashboardRequest(out=tmp_path / "x.png", symbol="THYAO", market=True),
            store=store,
        )


def test_unknown_symbol_raises_not_found(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    with pytest.raises(NotFoundError):
        run_dashboard(
            DashboardRequest(out=tmp_path / "x.png", symbol="XXXXXX"),
            store=store,
        )


def test_market_overview_empty_store_raises_not_found(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    with pytest.raises(NotFoundError):
        run_dashboard(
            DashboardRequest(out=tmp_path / "x.png", market=True),
            store=store,
        )
