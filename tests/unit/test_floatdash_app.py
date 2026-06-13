from __future__ import annotations

from pathlib import Path

import anyio
import httpx

from tvcli.floatdash.app import create_app
from tvcli.layers import freefloat as ff
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


def test_floatdash_app_endpoints(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    _seed_store(store, ["THYAO", "GARAN", "EREGL"], ratio_base=5.0)
    app = create_app(store=store)

    async def run_requests() -> tuple[
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
        httpx.Response,
    ]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return (
                await client.get("/"),
                await client.get("/api/market"),
                await client.get("/api/symbol/THYAO"),
                await client.get("/api/symbol/INVALID"),
                await client.get("/img/market.png"),
                await client.get("/img/symbol/THYAO.png"),
                await client.get("/img/symbol/INVALID.png"),
            )

    (
        resp_root,
        resp_market_api,
        resp_symbol_api,
        resp_symbol_api_err,
        resp_market_img,
        resp_symbol_img,
        resp_symbol_img_err,
    ) = anyio.run(run_requests)

    assert resp_root.status_code == 200
    assert "text/html" in resp_root.headers["content-type"]
    assert "float" in resp_root.text.lower()

    assert resp_market_api.status_code == 200
    market_data = resp_market_api.json()
    assert len(market_data["leaderboard"]) == 3
    assert market_data["median_ratio"] == 7.0
    assert "dramatic_changes" in market_data
    assert isinstance(market_data["dramatic_changes"], list)
    assert "summary" in market_data
    assert market_data["summary"]["severe_risk_count"] == 3
    assert market_data["summary"]["warning_risk_count"] == 0
    assert "top_gainers" in market_data
    assert "top_losers" in market_data

    assert resp_symbol_api.status_code == 200
    assert resp_symbol_api.json()["identity"]["code"] == "THYAO"

    assert resp_symbol_api_err.status_code == 404

    assert resp_market_img.status_code == 200
    assert resp_market_img.headers["content-type"] == "image/png"
    assert resp_market_img.content[:8] == _PNG_MAGIC

    assert resp_symbol_img.status_code == 200
    assert resp_symbol_img.headers["content-type"] == "image/png"
    assert resp_symbol_img.content[:8] == _PNG_MAGIC

    assert resp_symbol_img_err.status_code == 404


def test_floatdash_app_empty_store(tmp_path: Path) -> None:
    store = ArchiveStore(tmp_path / "archive.sqlite3")
    app = create_app(store=store)

    async def run_requests() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return (
                await client.get("/api/market"),
                await client.get("/img/market.png"),
                await client.get("/img/symbol/THYAO.png"),
            )

    resp_market_api, resp_market_img, resp_symbol_img = anyio.run(run_requests)

    assert resp_market_api.status_code == 404
    assert resp_market_img.status_code == 404
    assert resp_symbol_img.status_code == 404
