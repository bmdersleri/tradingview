"""FastAPI webhook application."""
# ruff: noqa: E501

from __future__ import annotations

import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..config import default_alerts_path
from .sinks import AlertSink, FileSink, build_sink


@dataclass(slots=True)
class WebhookState:
    secret: str
    append_sink: FileSink
    dispatch_sink: AlertSink | None
    started_at: float


def _parse_body(raw_body: bytes) -> Any:
    text = raw_body.decode("utf-8", errors="replace")
    if not text.strip():
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def create_app(
    *,
    secret: str,
    sink: str = "stdout",
    alerts_path: Path | None = None,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    telegram_client: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="tvcli webhook")
    append_sink = FileSink(alerts_path or default_alerts_path())
    dispatch_sink = None
    if sink.casefold() != "file":
        dispatch_sink = build_sink(
            sink,
            alerts_path=append_sink.path,
            telegram_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
            client=telegram_client,
        )
    app.state.webhook = WebhookState(
        secret=secret,
        append_sink=append_sink,
        dispatch_sink=dispatch_sink,
        started_at=time.monotonic(),
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        state = cast(WebhookState, app.state.webhook)
        return {
            "ok": True,
            "uptime_seconds": round(time.monotonic() - state.started_at, 3),
        }

    @app.get("/dashboard", response_class=HTMLResponse)
    async def get_dashboard_page() -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TVCLI Free-Float Analytics</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0f111a;
            --card-bg: rgba(24, 28, 39, 0.6);
            --border-color: rgba(42, 46, 57, 0.4);
            --text-primary: #d1d4dc;
            --text-secondary: #787b86;
            --accent-color: #26a69a;
            --accent-hover: #2bbbad;
            --error-color: #ef5350;
        }
        
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }

        header {
            background-color: rgba(19, 23, 34, 0.8);
            backdrop-filter: blur(10px);
            border-bottom: 1px solid var(--border-color);
            padding: 1.25rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .logo-container {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .logo-icon {
            color: var(--accent-color);
            font-weight: 700;
            font-size: 1.5rem;
            background: linear-gradient(135deg, var(--accent-color), #2bbbad);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .logo-title {
            font-size: 1.25rem;
            font-weight: 600;
            letter-spacing: 0.5px;
        }

        .search-container {
            display: flex;
            gap: 0.5rem;
        }

        .search-input {
            background-color: #1e222d;
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            padding: 0.6rem 1rem;
            border-radius: 6px;
            outline: none;
            width: 240px;
            font-family: inherit;
            font-size: 0.9rem;
            transition: border-color 0.2s, box-shadow 0.2s;
        }

        .search-input:focus {
            border-color: var(--accent-color);
            box-shadow: 0 0 0 2px rgba(38, 166, 154, 0.2);
        }

        .btn {
            background-color: var(--accent-color);
            color: #ffffff;
            border: none;
            padding: 0.6rem 1.2rem;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-family: inherit;
            font-size: 0.9rem;
            transition: background-color 0.2s, transform 0.1s;
        }

        .btn:hover {
            background-color: var(--accent-hover);
        }

        .btn:active {
            transform: scale(0.98);
        }

        .container {
            max-width: 1400px;
            margin: 2rem auto;
            padding: 0 2rem;
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 2rem;
            flex: 1;
        }

        .tabs {
            display: flex;
            gap: 1rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.5rem;
        }

        .tab {
            color: var(--text-secondary);
            font-weight: 600;
            font-size: 1rem;
            padding: 0.5rem 1rem;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            transition: color 0.2s, border-color 0.2s;
        }

        .tab.active {
            color: var(--accent-color);
            border-bottom-color: var(--accent-color);
        }

        .dashboard-view {
            display: none;
            animation: fadeIn 0.3s ease-in-out forwards;
        }

        .dashboard-view.active {
            display: block;
        }

        .card {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(5px);
        }

        .image-wrapper {
            width: 100%;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 500px;
            position: relative;
            background-color: #131722;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.03);
        }

        .dashboard-img {
            max-width: 100%;
            height: auto;
            display: block;
            border-radius: 6px;
        }

        .loading-overlay {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(19, 23, 34, 0.85);
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 1rem;
            z-index: 10;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.25s ease-in-out;
        }

        .loading-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }

        .spinner {
            width: 40px;
            height: 40px;
            border: 3px solid rgba(38, 166, 154, 0.1);
            border-top-color: var(--accent-color);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }

        footer {
            margin-top: auto;
            border-top: 1px solid var(--border-color);
            padding: 1.5rem;
            text-align: center;
            color: var(--text-secondary);
            font-size: 0.85rem;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .error-message {
            color: var(--error-color);
            text-align: center;
            font-weight: 600;
            display: none;
            padding: 1rem;
            background-color: rgba(239, 83, 80, 0.1);
            border: 1px solid rgba(239, 83, 80, 0.2);
            border-radius: 6px;
            margin-bottom: 1rem;
        }
    </style>
</head>
<body>
    <header>
        <div class="logo-container">
            <span class="logo-icon">📊</span>
            <span class="logo-title">TVCLI Free-Float Analytics</span>
        </div>
        <div class="search-container">
            <input type="text" id="symbolSearch" class="search-input"
                   placeholder="Enter Symbol (e.g. THYAO)" />
            <button onclick="searchSymbol()" class="btn">Search</button>
        </div>
    </header>

    <div class="container">
        <div class="tabs">
            <div id="tab-market" class="tab active"
                 onclick="switchTab('market')">Market Overview</div>
            <div id="tab-symbol" class="tab"
                 onclick="symbolTabClicked()">Symbol Deep-Dive</div>
        </div>

        <div id="errorBox" class="error-message"></div>

        <!-- Market Tab -->
        <div id="view-market" class="dashboard-view active">
            <div class="card">
                <div class="image-wrapper">
                    <img id="marketImg" class="dashboard-img"
                         src="/dashboard/market" alt="Market Overview"
                         onload="hideLoader('market')" />
                    <div id="loader-market" class="loading-overlay active">
                        <div class="spinner"></div>
                        <p>Generating Market Overview...</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- Symbol Tab -->
        <div id="view-symbol" class="dashboard-view">
            <div class="card">
                <div class="image-wrapper">
                    <img id="symbolImg" class="dashboard-img" src=""
                         alt="Symbol Deep-Dive" onload="hideLoader('symbol')"
                         onerror="handleImageError()" />
                    <div id="loader-symbol" class="loading-overlay">
                        <div class="spinner"></div>
                        <p id="symbolLoaderText">
                            Please enter a symbol above to load analytics...
                        </p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <footer>
        <p>TVCLI Free-Float Dashboard • Driven by Local Archive Data</p>
    </footer>

    <script>
        let currentTab = 'market';
        let activeSymbol = '';

        function switchTab(tabName) {
            currentTab = tabName;
            
            document.querySelectorAll('.tab')
                .forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.dashboard-view')
                .forEach(v => v.classList.remove('active'));
            
            document.getElementById(`tab-${tabName}`).classList.add('active');
            document.getElementById(`view-${tabName}`).classList.add('active');
        }

        function symbolTabClicked() {
            switchTab('symbol');
            if (!activeSymbol) {
                document.getElementById('loader-symbol').classList.add('active');
                document.getElementById('symbolLoaderText').innerText =
                    "Please enter a symbol above to load analytics...";
            }
        }

        function searchSymbol() {
            const val = document.getElementById('symbolSearch')
                .value.trim().toUpperCase();
            if (!val) return;
            
            activeSymbol = val;
            switchTab('symbol');
            
            const loader = document.getElementById('loader-symbol');
            const img = document.getElementById('symbolImg');
            const errorBox = document.getElementById('errorBox');
            
            errorBox.style.display = 'none';
            document.getElementById('symbolLoaderText').innerText =
                `Generating Deep-Dive for ${activeSymbol}...`;
            loader.classList.add('active');
            
            // Set source with random parameter to prevent browser caching
            img.src = `/dashboard/symbol/${activeSymbol}?t=${Date.now()}`;
        }

        function hideLoader(type) {
            const loader = document.getElementById(`loader-${type}`);
            if (loader) loader.classList.remove('active');
        }

        function handleImageError() {
            const loader = document.getElementById('loader-symbol');
            const errorBox = document.getElementById('errorBox');
            if (loader) loader.classList.remove('active');
            
            if (activeSymbol) {
                errorBox.innerText =
                    `Symbol "${activeSymbol}" not found in local archive.`;
                errorBox.style.display = 'block';
            }
        }

        // Add Enter key listener to search input
        document.getElementById('symbolSearch')
            .addEventListener('keypress', function (e) {
                if (e.key === 'Enter') {
                    searchSymbol();
                }
            });
    </script>
</body>
</html>"""

    @app.get("/dashboard/market")
    async def get_market_dashboard() -> FileResponse:
        import tempfile

        from ..layers.float_dashboard import DashboardRequest, run_dashboard

        tmp_dir = tempfile.gettempdir()
        out_path = Path(tmp_dir) / "tvcli_market_overview.png"
        try:
            req = DashboardRequest(out=out_path, market=True)
            run_dashboard(req)
            return FileResponse(out_path, media_type="image/png")
        except Exception as exc:
            from ..errors import NotFoundError

            if isinstance(exc, NotFoundError):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            ) from exc

    @app.get("/dashboard/symbol/{symbol}")
    async def get_symbol_dashboard(symbol: str) -> FileResponse:
        import tempfile

        from ..layers.float_dashboard import DashboardRequest, run_dashboard

        tmp_dir = tempfile.gettempdir()
        out_path = Path(tmp_dir) / f"tvcli_symbol_{symbol.upper()}.png"
        try:
            req = DashboardRequest(out=out_path, symbol=symbol.upper())
            run_dashboard(req)
            return FileResponse(out_path, media_type="image/png")
        except Exception as exc:
            from ..errors import NotFoundError

            if isinstance(exc, NotFoundError):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            ) from exc

    @app.post("/hook/{provided_secret}")
    async def hook(provided_secret: str, request: Request) -> JSONResponse:
        state = cast(WebhookState, app.state.webhook)
        if not hmac.compare_digest(state.secret, provided_secret):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid webhook secret.",
            )
        raw_body = await request.body()
        body = _parse_body(raw_body)
        record = {
            "received_at": datetime.now(tz=UTC).isoformat(),
            "body": body,
            "content_type": request.headers.get("content-type"),
        }
        stored = state.append_sink.send(record)
        dispatched: dict[str, Any] | None = None
        if state.dispatch_sink is not None:
            dispatched = state.dispatch_sink.send(record)
        return JSONResponse(
            {
                "ok": True,
                "stored": True,
                "dispatch": state.dispatch_sink is not None,
                "path": stored.get("path"),
                "dispatched": dispatched,
            }
        )

    return app
