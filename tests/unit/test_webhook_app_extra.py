from __future__ import annotations

from unittest.mock import patch

import anyio
import httpx

from tvcli.errors import NotFoundError
from tvcli.webhook.app import create_app


def test_webhook_app_empty_body(tmp_path) -> None:
    alerts_path = tmp_path / "alerts.jsonl"
    app = create_app(secret="secret", sink="file", alerts_path=alerts_path)

    async def run_requests() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                "/hook/secret", content="   ", headers={"content-type": "text/plain"}
            )

    resp = anyio.run(run_requests)
    assert resp.status_code == 200


def test_webhook_dashboard_exceptions() -> None:
    app = create_app(secret="secret", sink="file")

    async def run_requests() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # 1. /dashboard/market NotFoundError
            with patch(
                "tvcli.layers.float_dashboard.run_dashboard",
                side_effect=NotFoundError("No records"),
            ):
                resp_market_nf = await client.get("/dashboard/market")

            # 2. /dashboard/market generic Exception
            with patch(
                "tvcli.layers.float_dashboard.run_dashboard",
                side_effect=ValueError("Unexpected crash"),
            ):
                resp_market_err = await client.get("/dashboard/market")

            # 3. /dashboard/symbol/{symbol} generic Exception
            with patch(
                "tvcli.layers.float_dashboard.run_dashboard",
                side_effect=ValueError("Unexpected crash"),
            ):
                resp_symbol_err = await client.get("/dashboard/symbol/THYAO")

            return resp_market_nf, resp_market_err, resp_symbol_err

    resp_market_nf, resp_market_err, resp_symbol_err = anyio.run(run_requests)
    assert resp_market_nf.status_code == 404
    assert resp_market_err.status_code == 500
    assert resp_symbol_err.status_code == 500
