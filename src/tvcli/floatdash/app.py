# ruff: noqa: E501
"""Interactive free-float dashboard web server application."""

from __future__ import annotations

import atexit
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from ..layers import freefloat_archive
from ..layers.float_dashboard import DashboardRequest, run_dashboard
from ..logging_utils import setup_logger

logger = setup_logger("tvcli.floatdash")


def create_app(store: freefloat_archive.ArchiveStore | None = None) -> FastAPI:
    app = FastAPI(title="tvcli float-dashboard")
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    logger.info("Initializing tvcli float-dashboard FastAPI app")

    if store is None:
        store = freefloat_archive.ArchiveStore()

    # Reset any sync tasks that were left in "running" state because of server restart
    try:
        with store._connect() as conn:
            rowcount = conn.execute(
                "UPDATE sync_state SET last_status = ?, last_error = ? WHERE last_status = ?",
                ("failed", "Server restarted while sync was running", "running"),
            ).rowcount
            if rowcount > 0:
                logger.info(
                    "Reset sync tasks that were left running", extra={"count": rowcount}
                )
    except Exception:
        logger.exception("Failed to reset stuck sync tasks during app startup")

    tmp_dir = tempfile.mkdtemp(prefix="tvcli_dash_")

    def cleanup() -> None:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    atexit.register(cleanup)

    # Simple LRU cache for PNG files
    cache: dict[str, Path] = {}

    def get_latest_report_date() -> str | None:
        stats = store.archive_stats()
        return stats.get("last_report_date")

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard_page() -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BIST Free-Float Terminal</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <!-- TradingView Lightweight Charts & Chart.js -->
    <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-color: #0f111a;
            --sidebar-bg: #131722;
            --card-bg: rgba(24, 28, 39, 0.6);
            --border-color: rgba(42, 46, 57, 0.4);
            --text-primary: #d1d4dc;
            --text-secondary: #787b86;
            --accent-color: #26a69a;
            --accent-hover: #2bbbad;
            --bullish: #26a69a;
            --bearish: #ef5350;
            --warning: #f57c00;
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
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        header {
            background-color: var(--sidebar-bg);
            border-bottom: 1px solid var(--border-color);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            height: 64px;
        }

        .logo-container {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .logo-icon {
            font-size: 1.5rem;
            background: linear-gradient(135deg, var(--accent-color), #2bbbad);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 700;
        }

        .logo-title {
            font-size: 1.15rem;
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
            padding: 0.5rem 1rem;
            border-radius: 6px;
            outline: none;
            width: 240px;
            font-family: inherit;
            font-size: 0.9rem;
            transition: border-color 0.2s;
        }

        .search-input:focus {
            border-color: var(--accent-color);
        }

        .btn {
            background-color: var(--accent-color);
            color: #ffffff;
            border: none;
            padding: 0.5rem 1.2rem;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-family: inherit;
            font-size: 0.9rem;
            transition: background-color 0.2s;
        }

        .btn:hover {
            background-color: var(--accent-hover);
        }

        .btn:active {
            transform: scale(0.98);
        }

        .main-container {
            display: flex;
            flex: 1;
            height: calc(100vh - 64px);
            overflow: hidden;
        }

        .sidebar {
            width: 320px;
            background-color: var(--sidebar-bg);
            border-right: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        .sidebar-header {
            padding: 1rem;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.9rem;
            color: var(--text-secondary);
            font-weight: 600;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }

        .leaderboard-list {
            flex: 1;
            overflow-y: auto;
            list-style: none;
        }

        .leaderboard-item {
            padding: 0.85rem 1rem;
            border-bottom: 1px solid rgba(42, 46, 57, 0.2);
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background-color 0.2s;
            font-size: 0.9rem;
        }

        .leaderboard-item:hover {
            background-color: rgba(255, 255, 255, 0.02);
        }

        .leaderboard-item.active {
            background-color: rgba(38, 166, 154, 0.1);
            border-left: 3px solid var(--accent-color);
            padding-left: 13px;
        }

        .item-code {
            font-weight: 600;
        }

        .item-name {
            font-size: 0.75rem;
            color: var(--text-secondary);
            max-width: 140px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .item-ratio {
            font-weight: 600;
        }

        .ratio-severe { color: var(--bearish); }
        .ratio-low { color: var(--warning); }
        .ratio-normal { color: var(--bullish); }

        .content-area {
            flex: 1;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
            overflow-y: auto;
            background-color: var(--bg-color);
        }

        .info-bar {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1rem;
        }

        .info-card {
            background-color: rgba(24, 28, 39, 0.4);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }

        .info-label {
            font-size: 0.75rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            font-weight: 600;
        }

        .info-value {
            font-size: 1.25rem;
            font-weight: 700;
        }

        .chart-card {
            background-color: rgba(24, 28, 39, 0.6);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            min-height: 500px;
            position: relative;
            box-shadow: 0 4px 24px rgba(0, 0, 0, 0.2);
        }

        .chart-img {
            max-width: 100%;
            height: auto;
            max-height: 700px;
            border-radius: 6px;
        }

        .loader-overlay {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(15, 17, 26, 0.9);
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 1rem;
            z-index: 10;
            border-radius: 12px;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s;
        }

        .loader-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }

        .spinner {
            width: 44px;
            height: 44px;
            border: 3px solid rgba(38, 166, 154, 0.1);
            border-top-color: var(--accent-color);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        .error-box {
            background-color: rgba(239, 83, 80, 0.1);
            border: 1px solid rgba(239, 83, 80, 0.2);
            color: var(--bearish);
            padding: 0.75rem 1rem;
            border-radius: 6px;
            font-size: 0.9rem;
            font-weight: 600;
            display: none;
        }

        /* Events and Alerts Styling */
        .events-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
            text-align: left;
        }

        .events-table th {
            padding: 0.75rem;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-secondary);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.5px;
        }

        .events-table td {
            padding: 0.75rem;
            border-bottom: 1px solid rgba(42, 46, 57, 0.2);
            color: var(--text-primary);
        }

        .events-table tr:hover td {
            background-color: rgba(255, 255, 255, 0.01);
        }

        .badge {
            display: inline-block;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
        }

        .badge-high {
            background-color: rgba(239, 83, 80, 0.15);
            color: var(--bearish);
            border: 1px solid rgba(239, 83, 80, 0.3);
        }

        .badge-medium {
            background-color: rgba(245, 124, 0, 0.15);
            color: var(--warning);
            border: 1px solid rgba(245, 124, 0, 0.3);
        }

        .clickable-code {
            color: var(--accent-color);
            font-weight: 600;
            cursor: pointer;
            text-decoration: underline;
        }

        .clickable-code:hover {
            color: var(--accent-hover);
        }

        .card {
            background-color: rgba(24, 28, 39, 0.6);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            box-shadow: 0 4px 24px rgba(0, 0, 0, 0.15);
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .card:hover {
            box-shadow: 0 6px 30px rgba(38, 166, 154, 0.05);
        }

        .summary-card {
            background: linear-gradient(135deg, rgba(24, 28, 39, 0.8), rgba(15, 17, 26, 0.9));
            border-left: 4px solid var(--accent-color);
        }

        .insights-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 1.5rem;
        }

        @keyframes pulse {
            0% { opacity: 0.4; }
            50% { opacity: 1.0; }
            100% { opacity: 0.4; }
        }
    </style>
</head>
<body>
    <header>
        <div class="logo-container">
            <span class="logo-icon">📊</span>
            <span class="logo-title">BIST Free-Float Terminal</span>
        </div>
        <div id="syncStatusHeader" style="display: flex; align-items: center; gap: 0.6rem; font-size: 0.8rem; background-color: rgba(24, 28, 39, 0.6); border: 1px solid var(--border-color); padding: 0.35rem 0.75rem; border-radius: 6px; margin-right: auto; margin-left: 2rem;">
            <span id="syncStatusDot" style="width: 8px; height: 8px; border-radius: 50%; background-color: var(--bullish); display: inline-block;"></span>
            <span id="syncStatusText" style="color: var(--text-primary); font-weight: 500;">Veri Durumu Yükleniyor...</span>
            <button id="syncNowBtn" onclick="triggerSync()" class="btn" style="padding: 0.15rem 0.5rem; font-size: 0.75rem; border-radius: 4px; line-height: 1; margin-left: 0.5rem;">Eşitle</button>
        </div>
        <div class="search-container">
            <div style="position: relative; display: flex; align-items: center;">
                <input type="text" id="symbolSearch" class="search-input" placeholder="Search symbol or company..." />
                <span id="searchClear" onclick="clearSearch()" style="position: absolute; right: 10px; cursor: pointer; color: var(--text-secondary); display: none; font-size: 1.1rem; user-select: none;">&times;</span>
            </div>
            <button onclick="searchSymbol()" class="btn">Search</button>
        </div>
    </header>

    <div class="main-container">
        <aside class="sidebar">
            <div class="sidebar-header">Symbols Leaderboard</div>
            <ul id="leaderboardList" class="leaderboard-list">
                <li id="item-market" class="leaderboard-item active" onclick="loadMarketOverview()">
                    <div>
                        <div class="item-code">Market Overview</div>
                        <div class="item-name">BIST General Stats</div>
                    </div>
                    <div class="item-ratio ratio-normal">BIST</div>
                </li>
            </ul>
        </aside>

        <main class="content-area">
            <div id="errorBox" class="error-box"></div>

            <div id="marketInfoBar" class="info-bar">
                <div class="info-card">
                    <span class="info-label">Piyasa Medyan Oranı</span>
                    <span id="marketMedianRatio" class="info-value">-</span>
                </div>
                <div class="info-card">
                    <span class="info-label">Takip Edilen Hisse</span>
                    <span id="marketTotalSymbols" class="info-value">-</span>
                </div>
                <div class="info-card">
                    <span class="info-label" style="color: var(--bearish);">Risk Oranı Kritik (<10%)</span>
                    <span id="marketSevereRisk" class="info-value" style="color: var(--bearish);">-</span>
                </div>
                <div class="info-card">
                    <span class="info-label" style="color: var(--warning);">Aktif Kritik Alarmlar</span>
                    <span id="marketHighAlerts" class="info-value" style="color: var(--warning);">-</span>
                </div>
            </div>

            <div id="infoBar" class="info-bar" style="display: none;">
                <div class="info-card">
                    <span class="info-label">Symbol / Name</span>
                    <span id="infoSymbol" class="info-value">-</span>
                </div>
                <div class="info-card">
                    <span class="info-label">Latest Float Ratio</span>
                    <span id="infoRatio" class="info-value">-</span>
                </div>
                <div class="info-card">
                    <span class="info-label">Risk Profile</span>
                    <span id="infoRisk" class="info-value">-</span>
                </div>
                <div class="info-card">
                    <span class="info-label">Percentile Rank</span>
                    <span id="infoPercentile" class="info-value">-</span>
                </div>
            </div>

            <!-- Interactive Charts Container -->
            <div class="chart-card">
                <div id="loaderOverlay" class="loader-overlay active">
                    <div class="spinner"></div>
                    <p id="loaderText">Loading BIST Market Overview...</p>
                </div>

                <!-- Market Charts Container -->
                <div id="marketChartsContainer" style="display: grid; grid-template-columns: 3fr 2fr; gap: 1.5rem; width: 100%; height: 500px;">
                    <div style="background-color: rgba(24, 28, 39, 0.4); border: 1px solid var(--border-color); border-radius: 8px; padding: 1rem; position: relative;">
                        <span class="info-label" style="display: block; margin-bottom: 0.5rem;">Oran Dağılımı (Histogram)</span>
                        <div style="position: relative; width: 100%; height: calc(100% - 24px);">
                            <canvas id="histogramChart"></canvas>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-rows: 1fr 1fr; gap: 1.5rem; height: 100%;">
                        <div style="background-color: rgba(24, 28, 39, 0.4); border: 1px solid var(--border-color); border-radius: 8px; padding: 1rem; position: relative;">
                            <span class="info-label" style="display: block; margin-bottom: 0.5rem;">En Düşük Oranlı Hisseler</span>
                            <div style="position: relative; width: 100%; height: calc(100% - 24px);">
                                <canvas id="leaderboardChart"></canvas>
                            </div>
                        </div>
                        <div style="background-color: rgba(24, 28, 39, 0.4); border: 1px solid var(--border-color); border-radius: 8px; padding: 1rem; position: relative;">
                            <span class="info-label" style="display: block; margin-bottom: 0.5rem;">Aktif Kritik Alarmlar (Son Rapor)</span>
                            <div style="position: relative; width: 100%; height: calc(100% - 24px);">
                                <canvas id="eventsChart"></canvas>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Symbol Charts Container -->
                <div id="symbolChartsContainer" style="display: none; flex-direction: column; gap: 1rem; width: 100%;">
                    <!-- Chart Settings Toggles & Comparison Input -->
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem; background-color: rgba(24, 28, 39, 0.4); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.5rem 1rem; font-size: 0.85rem;">
                        <div style="display: flex; gap: 1.5rem; align-items: center;">
                            <span style="font-weight: 600; color: var(--text-secondary);">GÖSTERGELER:</span>
                            <label id="lblSma" style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="toggleSma" checked onchange="updateSymbolCharts()" /> SMA 20 (Sarı)
                            </label>
                            <label id="lblEma" style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="toggleEma" checked onchange="updateSymbolCharts()" /> EMA 20 (Mor)
                            </label>
                            <label id="lblWma" style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="toggleWma" onchange="updateSymbolCharts()" /> WMA 20 (Mavi)
                            </label>
                            <label id="lblBbands" style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="toggleBbands" onchange="updateSymbolCharts()" /> BBands (20, 2)
                            </label>
                            <label id="lblRsi" style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="toggleRsi" onchange="toggleIndicatorPanes()" /> RSI 14
                            </label>
                            <label id="lblMacd" style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="toggleMacd" onchange="toggleIndicatorPanes()" /> MACD
                            </label>
                            <label id="lblThresholds" style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; user-select: none;">
                                <input type="checkbox" id="toggleThresholds" checked onchange="updateSymbolCharts()" /> Eşik Çizgileri (%10 & %20)
                            </label>
                        </div>
                        <div style="display: flex; gap: 0.5rem; align-items: center;">
                            <span style="font-weight: 600; color: var(--text-secondary);">KARŞILAŞTIRMA:</span>
                            <input type="text" id="compareSearchInput" placeholder="Hisse kodu..." onkeypress="if(event.key==='Enter') addCompareSymbol()" style="background-color: #1e222d; border: 1px solid var(--border-color); color: var(--text-primary); padding: 0.25rem 0.5rem; border-radius: 4px; outline: none; width: 100px; font-family: inherit; font-size: 0.8rem;" />
                            <button onclick="addCompareSymbol()" class="btn" style="padding: 0.25rem 0.5rem; font-size: 0.8rem; border-radius: 4px;">Ekle</button>
                        </div>
                    </div>

                    <!-- Comparison chips display -->
                    <div id="compareListContainer" style="display: none; align-items: center; gap: 0.75rem; background-color: rgba(24, 28, 39, 0.2); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.5rem 1rem; font-size: 0.85rem;">
                        <span style="font-weight: 600; color: var(--text-secondary);">KARŞILAŞTIRILAN HİSSELER:</span>
                        <div id="compareList" style="display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center;"></div>
                    </div>

                    <!-- Chart Divs -->
                    <div id="ratioChartDiv" style="width: 100%; height: 320px; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color);"></div>
                    <div id="sharesChartDiv" style="width: 100%; height: 160px; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color);"></div>
                    <div id="rsiChartDiv" style="width: 100%; height: 120px; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color); display: none;"></div>
                    <div id="macdChartDiv" style="width: 100%; height: 120px; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color); display: none;"></div>
                </div>
            </div>

            <!-- Sektörel Dağılım & Isı Haritası (Heatmap) -->
            <div id="sectorHeatmapCard" class="card" style="margin-top: 0rem;">
                <h3 style="margin-bottom: 0.5rem; display: flex; align-items: center; justify-content: space-between; font-size: 1.05rem;">
                    <span style="display: flex; align-items: center; gap: 0.5rem; color: var(--accent-color);">
                        <span>🔥</span> Sektörel Likidite & Dolaşım Isı Haritası
                    </span>
                    <span style="font-size: 0.75rem; font-weight: normal; color: var(--text-secondary);">
                        Bloklar serbest dolaşımdaki nominal sermaye büyüklüğünü, renkler ise oran durumunu (<span style="color: var(--bearish); font-weight: bold;">%10 Altı Kritik</span>, <span style="color: var(--warning); font-weight: bold;">%10-%20 Riskli</span>, <span style="color: var(--bullish); font-weight: bold;">%20+ Normal</span>) temsil eder.
                    </span>
                </h3>
                <div id="sectorHeatmapGrid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; margin-top: 1rem; max-height: 400px; overflow-y: auto; padding-right: 0.25rem;">
                    <p style="text-align: center; color: var(--text-secondary); padding: 2rem; grid-column: 1 / -1;">Sektör verileri yükleniyor...</p>
                </div>
            </div>

            <!-- Market Insights & Analytics -->
            <div id="marketInsightsContainer" class="insights-grid">
                <!-- Market Analyst Summary Card -->
                <div class="card summary-card">
                    <h3 style="margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem; color: var(--accent-color); font-size: 1.1rem;">
                        <span>💡</span> Piyasa Analiz & Yönetici Özeti
                    </h3>
                    <p id="marketAnalystSummary" style="font-size: 0.95rem; line-height: 1.6; color: var(--text-primary);">
                        Yükleniyor...
                    </p>
                </div>

                <!-- Gainers & Losers Columns -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem;">
                    <!-- Gainers Card -->
                    <div class="card">
                        <h3 style="margin-bottom: 0.75rem; display: flex; align-items: center; gap: 0.5rem; color: var(--bullish); font-size: 1.05rem;">
                            <span>📈</span> En Çok Artan Dolaşım Oranları (Son Rapor)
                        </h3>
                        <div style="overflow-x: auto;">
                            <table class="events-table">
                                <thead>
                                    <tr>
                                        <th>Hisse</th>
                                        <th>Önceki Oran</th>
                                        <th>Yeni Oran</th>
                                        <th>Değişim</th>
                                    </tr>
                                </thead>
                                <tbody id="topGainersBody">
                                    <tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 1rem;">Yükleniyor...</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- Losers Card -->
                    <div class="card">
                        <h3 style="margin-bottom: 0.75rem; display: flex; align-items: center; gap: 0.5rem; color: var(--bearish); font-size: 1.05rem;">
                            <span>📉</span> En Çok Düşen Dolaşım Oranları (Son Rapor)
                        </h3>
                        <div style="overflow-x: auto;">
                            <table class="events-table">
                                <thead>
                                    <tr>
                                        <th>Hisse</th>
                                        <th>Önceki Oran</th>
                                        <th>Yeni Oran</th>
                                        <th>Değişim</th>
                                    </tr>
                                </thead>
                                <tbody id="topLosersBody">
                                    <tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 1rem;">Yükleniyor...</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Market Tab Events List -->
            <div id="marketEventsCard" class="card">
                <h3 style="margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
                    <span>⚠️</span> Piyasa Alarm Akışı / Son Dramatik Değişiklikler
                </h3>
                <p style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 1rem;">
                    Fiili dolaşım oranlarında veya dolaşımdaki hisse sayılarında sert sıçramalar ya da kritik eşik geçişleri gözlenen hisseler.
                </p>
                <div style="overflow-x: auto;">
                    <table class="events-table">
                        <thead>
                            <tr>
                                <th>Hisse</th>
                                <th>Tarih</th>
                                <th>Olay / Açıklama</th>
                                <th>Değer / Değişim</th>
                                <th>Önem</th>
                            </tr>
                        </thead>
                        <tbody id="marketEventsBody">
                            <tr><td colspan="5" style="text-align: center; color: var(--text-secondary); padding: 1.5rem;">Yükleniyor...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Symbol Tab Events List -->
            <div id="symbolEventsCard" class="card" style="display: none;">
                <h3 style="margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
                    <span>🔔</span> Hisse Olay Geçmişi & Risk Alarmları
                </h3>
                <p style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 1rem;">
                    Bu hissede geçmişte gerçekleşen fiili dolaşım değişimleri, sermaye hareketleri ve eşik geçişleri.
                </p>
                <div style="overflow-x: auto;">
                    <table class="events-table">
                        <thead>
                            <tr>
                                <th>Tarih</th>
                                <th>Olay / Açıklama</th>
                                <th>Değer / Değişim</th>
                                <th>Önem</th>
                            </tr>
                        </thead>
                        <tbody id="symbolEventsBody">
                            <tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 1.5rem;">Hisse analizi yüklenince burası güncellenecektir.</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </main>
    </div>

    <script>
        let activeCode = 'MARKET';
        let cachedMarketData = null;
        let activeSymbolData = null;
        let compareSymbols = [];
        let compareDataStore = {};
        const compareColors = ['#ef5350', '#ffaa00', '#9c27b0', '#00b0ff'];

        let ratioChartInstance = null;
        let sharesChartInstance = null;
        let areaSeries = null;
        let smaSeries = null;
        let emaSeries = null;
        let wmaSeries = null;
        let bbBasisSeries = null;
        let bbUpperSeries = null;
        let bbLowerSeries = null;
        let rsiChartInstance = null;
        let rsiSeries = null;
        let macdChartInstance = null;
        let macdLineSeries = null;
        let macdSignalSeries = null;
        let macdHistSeries = null;

        let thresholdLine10 = null;
        let thresholdLine20 = null;
        let sharesSeries = null;
        let activeLine10 = null;
        let activeLine20 = null;

        let histogramChartInstance = null;
        let leaderboardChartInstance = null;
        let eventsChartInstance = null;

        function parseDateToISO(dateStr) {
            if (!dateStr) return '';
            if (/^\\d{4}-\\d{2}-\\d{2}$/.test(dateStr)) return dateStr;
            const match = dateStr.match(/^(\\d{2})\\.(\\d{2})\\.(\\d{4})$/);
            if (match) {
                return `${match[3]}-${match[2]}-${match[1]}`;
            }
            return dateStr;
        }

        function calculateSMA(data, period) {
            const sma = [];
            for (let i = 0; i < data.length; i++) {
                if (i < period - 1) {
                    let sum = 0;
                    for (let j = 0; j <= i; j++) sum += data[j].value;
                    sma.push({ time: data[i].time, value: sum / (i + 1) });
                } else {
                    let sum = 0;
                    for (let j = i - period + 1; j <= i; j++) sum += data[j].value;
                    sma.push({ time: data[i].time, value: sum / period });
                }
            }
            return sma;
        }

        function calculateEMA(data, period) {
            const ema = [];
            if (data.length === 0) return ema;
            const k = 2 / (period + 1);
            let emaVal = data[0].value;
            ema.push({ time: data[0].time, value: emaVal });
            for (let i = 1; i < data.length; i++) {
                emaVal = data[i].value * k + emaVal * (1 - k);
                ema.push({ time: data[i].time, value: emaVal });
            }
            return ema;
        }

        function calculateWMA(data, period) {
            const wma = [];
            const denom = (period * (period + 1)) / 2;
            for (let i = 0; i < data.length; i++) {
                if (i < period - 1) {
                    wma.push({ time: data[i].time, value: data[i].value });
                } else {
                    let sum = 0;
                    for (let j = 0; j < period; j++) {
                        sum += data[i - j].value * (period - j);
                    }
                    wma.push({ time: data[i].time, value: sum / denom });
                }
            }
            return wma;
        }

        function calculateBollingerBands(data, period, multiplier) {
            const basis = calculateSMA(data, period);
            const bands = [];
            for (let i = 0; i < data.length; i++) {
                if (i < period - 1) {
                    bands.push({
                        time: data[i].time,
                        basis: data[i].value,
                        upper: data[i].value,
                        lower: data[i].value
                    });
                } else {
                    let sum = 0;
                    for (let j = i - period + 1; j <= i; j++) {
                        sum += Math.pow(data[j].value - basis[i].value, 2);
                    }
                    const stdDev = Math.sqrt(sum / period);
                    bands.push({
                        time: data[i].time,
                        basis: basis[i].value,
                        upper: basis[i].value + multiplier * stdDev,
                        lower: basis[i].value - multiplier * stdDev
                    });
                }
            }
            return bands;
        }

        function calculateRSI(data, period) {
            const rsi = [];
            if (data.length < 2) return rsi;

            let avgGain = 0;
            let avgLoss = 0;

            for (let i = 1; i <= Math.min(period, data.length - 1); i++) {
                const diff = data[i].value - data[i - 1].value;
                if (diff > 0) avgGain += diff;
                else avgLoss -= diff;
            }
            avgGain /= period;
            avgLoss /= period;

            const firstRsi = avgLoss === 0 ? 100 : (avgGain === 0 ? 0 : 100 - (100 / (1 + avgGain / avgLoss)));
            rsi.push({ time: data[0].time, value: 50 });
            for (let i = 1; i < Math.min(period + 1, data.length); i++) {
                rsi.push({ time: data[i].time, value: firstRsi });
            }

            for (let i = period + 1; i < data.length; i++) {
                const diff = data[i].value - data[i - 1].value;
                const gain = diff > 0 ? diff : 0;
                const loss = diff < 0 ? -diff : 0;

                avgGain = (avgGain * (period - 1) + gain) / period;
                avgLoss = (avgLoss * (period - 1) + loss) / period;

                const val = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));
                rsi.push({ time: data[i].time, value: val });
            }
            return rsi;
        }

        function calculateMACD(data, shortPeriod = 12, longPeriod = 26, signalPeriod = 9) {
            const shortEma = calculateEMA(data, shortPeriod);
            const longEma = calculateEMA(data, longPeriod);

            const macdLine = [];
            for (let i = 0; i < data.length; i++) {
                macdLine.push({
                    time: data[i].time,
                    value: shortEma[i].value - longEma[i].value
                });
            }

            const signalLine = calculateEMA(macdLine, signalPeriod);

            const macd = [];
            for (let i = 0; i < data.length; i++) {
                macd.push({
                    time: data[i].time,
                    macd: macdLine[i].value,
                    signal: signalLine[i].value,
                    histogram: macdLine[i].value - signalLine[i].value
                });
            }
            return macd;
        }

        function createSymbolCharts(recentChanges, risk) {
            if (ratioChartInstance) {
                ratioChartInstance.remove();
                ratioChartInstance = null;
            }
            if (sharesChartInstance) {
                sharesChartInstance.remove();
                sharesChartInstance = null;
            }
            if (rsiChartInstance) {
                rsiChartInstance.remove();
                rsiChartInstance = null;
            }
            if (macdChartInstance) {
                macdChartInstance.remove();
                macdChartInstance = null;
            }
            wmaSeries = null;
            bbBasisSeries = null;
            bbUpperSeries = null;
            bbLowerSeries = null;
            rsiSeries = null;
            macdLineSeries = null;
            macdSignalSeries = null;
            macdHistSeries = null;

            const ratioContainer = document.getElementById('ratioChartDiv');
            const sharesContainer = document.getElementById('sharesChartDiv');
            const rsiContainer = document.getElementById('rsiChartDiv');
            const macdContainer = document.getElementById('macdChartDiv');

            const sortedData = [...recentChanges].reverse().map(item => {
                const parsedDate = parseDateToISO(item.report_date);
                return {
                    time: parsedDate,
                    ratio: item.ratio,
                    shares: item.float_shares
                };
            }).filter(d => d.time);

            if (sortedData.length === 0) return;

            const ratioData = sortedData.map(d => ({ time: d.time, value: d.ratio }));
            const sharesData = sortedData.map(d => ({ time: d.time, value: d.shares }));

            const chartOptions = {
                layout: {
                    background: { type: 'solid', color: '#181c27' },
                    textColor: '#d1d4dc',
                    fontFamily: "'Outfit', sans-serif",
                },
                grid: {
                    vertLines: { color: 'rgba(42, 46, 57, 0.15)', style: 3 },
                    horzLines: { color: 'rgba(42, 46, 57, 0.15)', style: 3 },
                },
                rightPriceScale: {
                    borderColor: 'rgba(42, 46, 57, 0.3)',
                },
                timeScale: {
                    borderColor: 'rgba(42, 46, 57, 0.3)',
                },
            };

            ratioChartInstance = LightweightCharts.createChart(ratioContainer, {
                ...chartOptions,
                width: ratioContainer.clientWidth || 800,
                height: 320,
            });

            const lineColor = risk.low_float ? '#ef5350' : '#26a69a';
            const areaColor = risk.low_float ? 'rgba(239, 83, 80, 0.15)' : 'rgba(38, 166, 154, 0.15)';

            areaSeries = ratioChartInstance.addAreaSeries({
                lineColor: lineColor,
                topColor: areaColor,
                bottomColor: 'rgba(15, 17, 26, 0.0)',
                lineWidth: 2.5,
                priceFormat: {
                    type: 'custom',
                    formatter: price => price.toFixed(2) + '%',
                },
            });
            areaSeries.setData(ratioData);

            smaSeries = ratioChartInstance.addLineSeries({
                color: '#ffaa00',
                lineWidth: 1.5,
                lineStyle: 2,
                priceLineVisible: false,
            });
            const smaData = calculateSMA(ratioData, 20);
            smaSeries.setData(smaData);

            emaSeries = ratioChartInstance.addLineSeries({
                color: '#9c27b0',
                lineWidth: 1.5,
                lineStyle: 0,
                priceLineVisible: false,
            });
            const emaData = calculateEMA(ratioData, 20);
            emaSeries.setData(emaData);

            wmaSeries = ratioChartInstance.addLineSeries({
                color: '#00b0ff',
                lineWidth: 1.5,
                lineStyle: 0,
                priceLineVisible: false,
            });
            const wmaData = calculateWMA(ratioData, 20);
            wmaSeries.setData(wmaData);

            bbBasisSeries = ratioChartInstance.addLineSeries({
                color: 'rgba(255, 255, 255, 0.3)',
                lineWidth: 1,
                lineStyle: 2,
                priceLineVisible: false,
            });
            bbUpperSeries = ratioChartInstance.addLineSeries({
                color: 'rgba(41, 182, 246, 0.6)',
                lineWidth: 1,
                priceLineVisible: false,
            });
            bbLowerSeries = ratioChartInstance.addLineSeries({
                color: 'rgba(41, 182, 246, 0.6)',
                lineWidth: 1,
                priceLineVisible: false,
            });
            const bbData = calculateBollingerBands(ratioData, 20, 2);
            bbBasisSeries.setData(bbData.map(d => ({ time: d.time, value: d.basis })));
            bbUpperSeries.setData(bbData.map(d => ({ time: d.time, value: d.upper })));
            bbLowerSeries.setData(bbData.map(d => ({ time: d.time, value: d.lower })));

            thresholdLine10 = {
                price: 10.0,
                color: '#ef5350',
                lineWidth: 1,
                lineStyle: 3,
                axisLabelVisible: true,
                title: 'Severe Limit (10%)',
            };
            thresholdLine20 = {
                price: 20.0,
                color: '#ffaa00',
                lineWidth: 1,
                lineStyle: 3,
                axisLabelVisible: true,
                title: 'Low Limit (20%)',
            };

            sharesChartInstance = LightweightCharts.createChart(sharesContainer, {
                ...chartOptions,
                width: sharesContainer.clientWidth || 800,
                height: 160,
            });

            sharesSeries = sharesChartInstance.addHistogramSeries({
                color: 'rgba(38, 166, 154, 0.6)',
                priceFormat: {
                    type: 'volume',
                },
            });
            sharesSeries.setData(sharesData);

            const showRsi = document.getElementById('toggleRsi').checked;
            if (showRsi) {
                rsiContainer.style.display = 'block';
                rsiChartInstance = LightweightCharts.createChart(rsiContainer, {
                    ...chartOptions,
                    width: rsiContainer.clientWidth || 800,
                    height: 120,
                });
                rsiSeries = rsiChartInstance.addLineSeries({
                    color: '#ab47bc',
                    lineWidth: 1.5,
                    priceFormat: {
                        type: 'custom',
                        formatter: val => val.toFixed(1),
                    },
                });
                const rsiData = calculateRSI(ratioData, 14);
                rsiSeries.setData(rsiData);

                rsiSeries.createPriceLine({
                    price: 70.0,
                    color: 'rgba(239, 83, 80, 0.4)',
                    lineWidth: 1,
                    lineStyle: 3,
                    axisLabelVisible: true,
                });
                rsiSeries.createPriceLine({
                    price: 30.0,
                    color: 'rgba(38, 166, 154, 0.4)',
                    lineWidth: 1,
                    lineStyle: 3,
                    axisLabelVisible: true,
                });
            } else {
                rsiContainer.style.display = 'none';
            }

            const showMacd = document.getElementById('toggleMacd').checked;
            if (showMacd) {
                macdContainer.style.display = 'block';
                macdChartInstance = LightweightCharts.createChart(macdContainer, {
                    ...chartOptions,
                    width: macdContainer.clientWidth || 800,
                    height: 120,
                });
                macdLineSeries = macdChartInstance.addLineSeries({
                    color: '#29b6f6',
                    lineWidth: 1.5,
                    priceLineVisible: false,
                });
                macdSignalSeries = macdChartInstance.addLineSeries({
                    color: '#ff7043',
                    lineWidth: 1.5,
                    priceLineVisible: false,
                });
                macdHistSeries = macdChartInstance.addHistogramSeries({
                    priceLineVisible: false,
                });

                const macdData = calculateMACD(ratioData, 12, 26, 9);
                const lineData = macdData.map(d => ({ time: d.time, value: d.value }));
                const signalData = macdData.map(d => ({ time: d.time, value: d.signal }));
                const histData = macdData.map(d => ({
                    time: d.time,
                    value: d.histogram,
                    color: d.histogram >= 0 ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)'
                }));

                macdLineSeries.setData(lineData);
                macdSignalSeries.setData(signalData);
                macdHistSeries.setData(histData);
            } else {
                macdContainer.style.display = 'none';
            }

            // Sync timelines of all active charts
            const activeCharts = [ratioChartInstance, sharesChartInstance];
            if (rsiChartInstance) activeCharts.push(rsiChartInstance);
            if (macdChartInstance) activeCharts.push(macdChartInstance);

            activeCharts.forEach(chart => {
                chart.timeScale().subscribeVisibleTimeRangeChange(range => {
                    activeCharts.forEach(otherChart => {
                        if (otherChart !== chart) {
                            otherChart.timeScale().setVisibleRange(range);
                        }
                    });
                });
            });

            updateSymbolCharts();
            ratioChartInstance.timeScale().fitContent();
        }

        function updateSymbolCharts() {
            if (!ratioChartInstance || !areaSeries) return;

            const showSma = document.getElementById('toggleSma').checked;
            const showEma = document.getElementById('toggleEma').checked;
            const showWma = document.getElementById('toggleWma').checked;
            const showBbands = document.getElementById('toggleBbands').checked;
            const showThresholds = document.getElementById('toggleThresholds').checked;

            if (smaSeries) smaSeries.applyOptions({ visible: showSma });
            if (emaSeries) emaSeries.applyOptions({ visible: showEma });
            if (wmaSeries) wmaSeries.applyOptions({ visible: showWma });

            if (bbBasisSeries) bbBasisSeries.applyOptions({ visible: showBbands });
            if (bbUpperSeries) bbUpperSeries.applyOptions({ visible: showBbands });
            if (bbLowerSeries) bbLowerSeries.applyOptions({ visible: showBbands });

            if (activeLine10) {
                areaSeries.removePriceLine(activeLine10);
                activeLine10 = null;
            }
            if (activeLine20) {
                areaSeries.removePriceLine(activeLine20);
                activeLine20 = null;
            }

            if (showThresholds) {
                activeLine10 = areaSeries.createPriceLine(thresholdLine10);
                activeLine20 = areaSeries.createPriceLine(thresholdLine20);
            }
        }

        function toggleIndicatorPanes() {
            const showRsi = document.getElementById('toggleRsi').checked;
            const showMacd = document.getElementById('toggleMacd').checked;

            document.getElementById('rsiChartDiv').style.display = showRsi ? 'block' : 'none';
            document.getElementById('macdChartDiv').style.display = showMacd ? 'block' : 'none';

            if (activeSymbolData && activeSymbolData.recent_changes) {
                const risk = activeSymbolData.risk || { low_float: false };
                createSymbolCharts(activeSymbolData.recent_changes, risk);
            }
        }

        function createMarketCharts(data) {
            if (histogramChartInstance) histogramChartInstance.destroy();
            if (leaderboardChartInstance) leaderboardChartInstance.destroy();
            if (eventsChartInstance) eventsChartInstance.destroy();

            const ratios = data.leaderboard.map(item => item.ratio);
            const binSize = 2.5;
            const binsCount = Math.ceil(100 / binSize);
            const bins = Array.from({ length: binsCount }, (_, i) => ({
                label: `${(i * binSize).toFixed(1)}-${((i + 1) * binSize).toFixed(1)}%`,
                min: i * binSize,
                max: (i + 1) * binSize,
                count: 0
            }));
            ratios.forEach(r => {
                const idx = Math.min(Math.floor(r / binSize), bins.length - 1);
                if (idx >= 0 && idx < bins.length) bins[idx].count++;
            });
            const activeBins = bins.filter(b => b.count > 0 || b.min < 50);

            const histCtx = document.getElementById('histogramChart').getContext('2d');
            histogramChartInstance = new Chart(histCtx, {
                type: 'bar',
                data: {
                    labels: activeBins.map(b => b.label),
                    datasets: [{
                        label: 'Hisse Sayısı',
                        data: activeBins.map(b => b.count),
                        backgroundColor: 'rgba(38, 166, 154, 0.65)',
                        borderColor: '#26a69a',
                        borderWidth: 1,
                        barPercentage: 0.95,
                        categoryPercentage: 0.95,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: '#1e222d',
                            titleColor: '#d1d4dc',
                            bodyColor: '#d1d4dc',
                            borderColor: 'rgba(42, 46, 57, 0.4)',
                            borderWidth: 1,
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(42, 46, 57, 0.15)' },
                            ticks: { color: '#787b86', font: { size: 9 } }
                        },
                        y: {
                            grid: { color: 'rgba(42, 46, 57, 0.15)' },
                            ticks: { color: '#787b86', font: { size: 9 } }
                        }
                    }
                }
            });

            const lowestItems = data.leaderboard.slice(0, 15);
            const leadCtx = document.getElementById('leaderboardChart').getContext('2d');
            
            const leadColors = lowestItems.map(item => {
                if (item.ratio < 10.0) return 'rgba(239, 83, 80, 0.75)';
                if (item.ratio < 20.0) return 'rgba(245, 124, 0, 0.75)';
                return 'rgba(38, 166, 154, 0.75)';
            });

            leaderboardChartInstance = new Chart(leadCtx, {
                type: 'bar',
                data: {
                    labels: lowestItems.map(item => item.code),
                    datasets: [{
                        label: 'Dolaşım Oranı',
                        data: lowestItems.map(item => item.ratio),
                        backgroundColor: leadColors,
                        borderWidth: 0,
                        borderRadius: 4,
                    }]
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: '#1e222d',
                            titleColor: '#d1d4dc',
                            bodyColor: '#d1d4dc',
                            borderColor: 'rgba(42, 46, 57, 0.4)',
                            borderWidth: 1,
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(42, 46, 57, 0.15)' },
                            ticks: { color: '#787b86', font: { size: 9 } }
                        },
                        y: {
                            grid: { display: false },
                            ticks: { color: '#d1d4dc', font: { size: 9, weight: 'bold' } }
                        }
                    }
                }
            });

            const eventLabels = data.event_summary.map(item => getEventName(item.event_type));
            const eventCounts = data.event_summary.map(item => item.count);
            const eventsCtx = document.getElementById('eventsChart').getContext('2d');

            eventsChartInstance = new Chart(eventsCtx, {
                type: 'bar',
                data: {
                    labels: eventLabels,
                    datasets: [{
                        label: 'Olay Sayısı',
                        data: eventCounts,
                        backgroundColor: 'rgba(245, 124, 0, 0.75)',
                        borderWidth: 0,
                        borderRadius: 4,
                    }]
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: '#1e222d',
                            titleColor: '#d1d4dc',
                            bodyColor: '#d1d4dc',
                            borderColor: 'rgba(42, 46, 57, 0.4)',
                            borderWidth: 1,
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(42, 46, 57, 0.15)' },
                            ticks: { color: '#787b86', font: { size: 9 }, stepSize: 1 }
                        },
                        y: {
                            grid: { display: false },
                            ticks: { color: '#d1d4dc', font: { size: 8 } }
                        }
                    }
                }
            });
        }

        window.addEventListener('resize', () => {
            if (ratioChartInstance && document.getElementById('symbolChartsContainer').style.display === 'flex') {
                const ratioContainer = document.getElementById('ratioChartDiv');
                const sharesContainer = document.getElementById('sharesChartDiv');
                ratioChartInstance.resize(ratioContainer.clientWidth, 320);
                sharesChartInstance.resize(sharesContainer.clientWidth, 160);
            }
        });

        function getEventName(type) {
            const names = {
                'ratio_jump_up': 'Fiili Dolaşım Oranı Yükseldi 📈',
                'ratio_jump_down': 'Fiili Dolaşım Oranı Düştü 📉',
                'ratio_threshold_cross_down': 'Kritik Eşik Altına Geçiş ⚠️',
                'ratio_threshold_cross_up': 'Kritik Eşik Üstüne Geçiş ✅',
                'float_shares_jump_up': 'Dolaşımdaki Hisse Artışı 📊',
                'float_shares_jump_down': 'Dolaşımdaki Hisse Düşüşü 📊',
                'capital_change_detected': 'Sermaye Değişimi 💼',
                'new_52w_high_ratio': '52 Haftalık Oran Zirvesi 🚀',
                'new_52w_low_ratio': '52 Haftalık Oran Dibi 📉',
                'liquidity_risk_low_float': 'Düşük Fiili Dolaşım Riski ⚠️'
            };
            return names[type] || type;
        }

        function formatEventValue(event) {
            const type = event.event_type;
            const payload = event.payload;
            const val = event.metric_value;

            if (type === 'ratio_jump_up' || type === 'ratio_jump_down') {
                return `${val > 0 ? '+' : ''}${val.toFixed(2)}% (Mevcut: ${payload.ratio.toFixed(2)}%)`;
            }
            if (type === 'ratio_threshold_cross_down' || type === 'ratio_threshold_cross_up') {
                return `${payload.from.toFixed(2)}% ➔ ${payload.to.toFixed(2)}%`;
            }
            if (type === 'float_shares_jump_up' || type === 'float_shares_jump_down') {
                return `${val > 0 ? '+' : ''}${val.toFixed(1)}%`;
            }
            if (type === 'capital_change_detected') {
                return `${val > 0 ? '+' : ''}${val.toLocaleString()}`;
            }
            if (type === 'new_52w_high_ratio' || type === 'new_52w_low_ratio') {
                return `${payload.ratio.toFixed(2)}% (Zaman: ${payload.window} Rapor)`;
            }
            if (type === 'liquidity_risk_low_float') {
                return `${payload.ratio.toFixed(2)}%`;
            }
            return val ? val.toString() : '-';
        }

        // Fetch leaderboard and market events on startup
        window.addEventListener('DOMContentLoaded', async () => {
            try {
                const response = await fetch('/api/market');
                if (!response.ok) return;
                const data = await response.json();

                // Populate Sidebar
                const list = document.getElementById('leaderboardList');
                data.leaderboard.forEach(item => {
                    const li = document.createElement('li');
                    li.id = `item-${item.code}`;
                    li.className = 'leaderboard-item';
                    li.onclick = () => loadSymbol(item.code);

                    let colorClass = 'ratio-normal';
                    if (item.ratio < 10.0) colorClass = 'ratio-severe';
                    else if (item.ratio < 20.0) colorClass = 'ratio-low';

                    li.innerHTML = `
                        <div>
                            <div class="item-code">${item.code}</div>
                            <div class="item-name">${item.name}</div>
                        </div>
                        <div class="item-ratio ${colorClass}">${item.ratio.toFixed(2)}%</div>
                    `;
                    list.appendChild(li);
                });

                // Populate market alerts table
                const eventsBody = document.getElementById('marketEventsBody');
                if (data.dramatic_changes && data.dramatic_changes.length > 0) {
                    eventsBody.innerHTML = '';
                    data.dramatic_changes.forEach(event => {
                        const tr = document.createElement('tr');
                        const badgeClass = event.severity === 'high' ? 'badge-high' : 'badge-medium';

                        tr.innerHTML = `
                            <td><span class="clickable-code" onclick="loadSymbol('${event.code}')">${event.code}</span></td>
                            <td>${event.report_date}</td>
                            <td>${getEventName(event.event_type)}</td>
                            <td>${formatEventValue(event)}</td>
                            <td><span class="badge ${badgeClass}">${event.severity}</span></td>
                        `;
                        eventsBody.appendChild(tr);
                    });
                } else {
                    eventsBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-secondary); padding: 1.5rem;">Hiçbir alarm/dramatik değişiklik bulunamadı.</td></tr>';
                }

                // Populate Market Info Bar
                document.getElementById('marketMedianRatio').innerText = `${data.median_ratio.toFixed(2)}%`;
                document.getElementById('marketTotalSymbols').innerText = data.n_symbols;
                document.getElementById('marketSevereRisk').innerText = data.summary ? data.summary.severe_risk_count : '-';
                document.getElementById('marketHighAlerts').innerText = data.summary ? data.summary.total_high_alerts : '-';

                // Populate Analyst Summary
                const analystSummary = document.getElementById('marketAnalystSummary');
                if (analystSummary) {
                    const severeCount = data.summary ? data.summary.severe_risk_count : 0;
                    const alertCount = data.summary ? data.summary.total_high_alerts : 0;
                    analystSummary.innerHTML = `
                        BIST genelinde son rapor tarihi olan <strong>${data.report_date}</strong> itibarıyla <strong>${data.n_symbols}</strong> hisse senedi takip edilmektedir.
                        Genel piyasa medyan fiili dolaşım oranı <strong>%${data.median_ratio.toFixed(2)}</strong> seviyesindedir.
                        <strong>${severeCount}</strong> adet hisse kritik %10 eşiğinin altında olup yüksek likidite ve volatilite riski barındırmaktadır.
                        Son raporda toplam <strong>${alertCount}</strong> adet yüksek öncelikli/kritik alarm tetiklenmiştir.
                    `;
                }

                // Populate Top Gainers
                const gainersBody = document.getElementById('topGainersBody');
                if (gainersBody) {
                    if (data.top_gainers && data.top_gainers.length > 0) {
                        gainersBody.innerHTML = '';
                        data.top_gainers.forEach(item => {
                            const tr = document.createElement('tr');
                            tr.innerHTML = `
                                <td><span class="clickable-code" onclick="loadSymbol('${item.code}')">${item.code}</span></td>
                                <td>${item.previous_ratio.toFixed(2)}%</td>
                                <td>${item.current_ratio.toFixed(2)}%</td>
                                <td style="color: var(--bullish); font-weight: 600;">+${item.delta.toFixed(2)}%</td>
                            `;
                            gainersBody.appendChild(tr);
                        });
                    } else {
                        gainersBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 1rem;">Veri bulunamadı (Karşılaştırma için en az iki rapor gereklidir).</td></tr>';
                    }
                }

                // Populate Top Losers
                const losersBody = document.getElementById('topLosersBody');
                if (losersBody) {
                    if (data.top_losers && data.top_losers.length > 0) {
                        losersBody.innerHTML = '';
                        data.top_losers.forEach(item => {
                            const tr = document.createElement('tr');
                            tr.innerHTML = `
                                <td><span class="clickable-code" onclick="loadSymbol('${item.code}')">${item.code}</span></td>
                                <td>${item.previous_ratio.toFixed(2)}%</td>
                                <td>${item.current_ratio.toFixed(2)}%</td>
                                <td style="color: var(--bearish); font-weight: 600;">${item.delta.toFixed(2)}%</td>
                            `;
                            losersBody.appendChild(tr);
                        });
                    } else {
                        losersBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 1rem;">Veri bulunamadı (Karşılaştırma için en az iki rapor gereklidir).</td></tr>';
                    }
                }
            } catch (err) {
                console.error("Failed to load leaderboard:", err);
            }
        });

        function showLoader(text) {
            const overlay = document.getElementById('loaderOverlay');
            document.getElementById('loaderText').innerText = text;
            overlay.classList.add('active');
        }

        function hideLoader() {
            document.getElementById('loaderOverlay').classList.remove('active');
        }

        function handleImageError() {
            hideLoader();
            const errorBox = document.getElementById('errorBox');
            errorBox.innerText = `Failed to generate or load the chart for "${activeCode}". Make sure the symbol exists in the archive.`;
            errorBox.style.display = 'block';
        }

        function loadMarketOverview() {
            activeCode = 'MARKET';
            document.getElementById('errorBox').style.display = 'none';
            document.getElementById('infoBar').style.display = 'none';
            document.getElementById('symbolEventsCard').style.display = 'none';
            document.getElementById('symbolChartsContainer').style.display = 'none';
            
            document.getElementById('marketInfoBar').style.display = 'grid';
            document.getElementById('marketInsightsContainer').style.display = 'grid';
            document.getElementById('marketChartsContainer').style.display = 'grid';
            document.getElementById('marketEventsCard').style.display = 'block';
            document.getElementById('sectorHeatmapCard').style.display = 'block';

            document.querySelectorAll('.leaderboard-item').forEach(item => item.classList.remove('active'));
            document.getElementById('item-market').classList.add('active');

            hideLoader();
            if (cachedMarketData) {
                createMarketCharts(cachedMarketData);
            }
            // Fetch sector heatmap data
            fetch('/api/market/sectors')
                .then(r => r.ok ? r.json() : [])
                .then(sectors => renderSectorHeatmap(sectors))
                .catch(err => console.error("Failed to load sectors heatmap:", err));
        }

        async function loadSymbol(code) {
            activeCode = code.toUpperCase();
            document.getElementById('errorBox').style.display = 'none';
            document.getElementById('marketEventsCard').style.display = 'none';
            document.getElementById('marketInfoBar').style.display = 'none';
            document.getElementById('marketInsightsContainer').style.display = 'none';
            document.getElementById('marketChartsContainer').style.display = 'none';
            document.getElementById('sectorHeatmapCard').style.display = 'none';
            
            // Clear comparison list
            compareSymbols = [];
            compareDataStore = {};
            renderComparisonChips();
            const lblSma = document.getElementById('lblSma');
            const lblEma = document.getElementById('lblEma');
            const lblThresholds = document.getElementById('lblThresholds');
            const sharesDiv = document.getElementById('sharesChartDiv');
            if (lblSma) lblSma.style.display = 'flex';
            if (lblEma) lblEma.style.display = 'flex';
            if (lblThresholds) lblThresholds.style.display = 'flex';
            if (sharesDiv) sharesDiv.style.display = 'block';

            document.getElementById('symbolEventsCard').style.display = 'block';
            document.getElementById('symbolChartsContainer').style.display = 'flex';

            document.querySelectorAll('.leaderboard-item').forEach(item => item.classList.remove('active'));
            const listItem = document.getElementById(`item-${activeCode}`);
            if (listItem) {
                listItem.classList.add('active');
                listItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }

            showLoader(`Generating Deep-Dive for ${activeCode}...`);

            try {
                const response = await fetch(`/api/symbol/${activeCode}`);
                if (!response.ok) {
                    hideLoader();
                    handleImageError();
                    return;
                }
                const data = await response.json();
                activeSymbolData = data;

                document.getElementById('infoSymbol').innerText = `${data.identity.code} / ${data.identity.name}`;
                document.getElementById('infoRatio').innerText = `${data.latest.ratio.toFixed(2)}%`;

                const risk = data.risk;
                let riskStr = 'NORMAL';
                let riskColor = 'var(--bullish)';
                if (risk.severe_low_float) {
                    riskStr = 'SEVERE RISK';
                    riskColor = 'var(--bearish)';
                } else if (risk.low_float) {
                    riskStr = 'LOW FLOAT';
                    riskColor = 'var(--warning)';
                }
                const riskVal = document.getElementById('infoRisk');
                riskVal.innerText = riskStr;
                riskVal.style.color = riskColor;

                const pct = data.percentile;
                document.getElementById('infoPercentile').innerText = pct ? `lowest ${pct.rank}/${pct.total} (${pct.percentile.toFixed(1)}%)` : '-';

                document.getElementById('infoBar').style.display = 'grid';

                // Populate symbol events table
                const symEventsBody = document.getElementById('symbolEventsBody');
                if (data.events && data.events.length > 0) {
                    symEventsBody.innerHTML = '';
                    data.events.forEach(event => {
                        const tr = document.createElement('tr');
                        const badgeClass = event.severity === 'high' ? 'badge-high' : 'badge-medium';

                        tr.innerHTML = `
                            <td>${event.report_date}</td>
                            <td>${getEventName(event.event_type)}</td>
                            <td>${formatEventValue(event)}</td>
                            <td><span class="badge ${badgeClass}">${event.severity}</span></td>
                        `;
                        symEventsBody.appendChild(tr);
                    });
                } else {
                    symEventsBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 1.5rem;">Bu hisse için kayıtlı olay/alarm bulunmamaktadır.</td></tr>';
                }

                hideLoader();
                createSymbolCharts(data.recent_changes, risk);

            } catch (err) {
                hideLoader();
                console.error("Failed to load symbol details:", err);
                handleImageError();
            }
        }

        const searchInput = document.getElementById('symbolSearch');
        const clearBtn = document.getElementById('searchClear');

        searchInput.addEventListener('input', (e) => {
            const val = e.target.value.trim().toUpperCase();
            clearBtn.style.display = val ? 'block' : 'none';

            const items = document.querySelectorAll('.leaderboard-list .leaderboard-item');
            items.forEach(item => {
                if (item.id === 'item-market') return;

                const code = item.querySelector('.item-code').innerText.toUpperCase();
                const name = item.querySelector('.item-name').innerText.toUpperCase();

                if (code.includes(val) || name.includes(val)) {
                    item.style.display = 'flex';
                } else {
                    item.style.display = 'none';
                }
            });
        });

        function clearSearch() {
            searchInput.value = '';
            clearBtn.style.display = 'none';
            searchInput.focus();

            const items = document.querySelectorAll('.leaderboard-list .leaderboard-item');
            items.forEach(item => {
                item.style.display = 'flex';
            });
        }

        function searchSymbol() {
            const val = searchInput.value.trim().toUpperCase();
            if (val) {
                loadSymbol(val);
            }
        }

        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                searchSymbol();
            }
        });

        // ----------------------------------------------------
        // COMPARISON ENGINE
        // ----------------------------------------------------
        async function addCompareSymbol() {
            const input = document.getElementById('compareSearchInput');
            const code = input.value.trim().toUpperCase();
            if (!code) return;
            input.value = '';

            if (code === activeCode) {
                alert("Bir hisseyi kendisiyle karşılaştıramazsınız.");
                return;
            }
            if (compareSymbols.includes(code)) {
                return;
            }
            if (compareSymbols.length >= 4) {
                alert("En fazla 4 hisseyi karşılaştırma listesine ekleyebilirsiniz.");
                return;
            }

            showLoader(`Loading comparison data for ${code}...`);
            try {
                const response = await fetch(`/api/symbol/${code}`);
                if (!response.ok) {
                    hideLoader();
                    alert(`Hisse verisi alınamadı: ${code}`);
                    return;
                }
                const data = await response.json();
                compareDataStore[code] = data;
                compareSymbols.push(code);
                
                hideLoader();
                renderComparisonChips();
                renderComparisonChart();
            } catch (err) {
                hideLoader();
                console.error("Comparison load failed:", err);
                alert(`Hisse verisi yüklenirken hata oluştu: ${code}`);
            }
        }

        function removeCompareSymbol(code) {
            compareSymbols = compareSymbols.filter(c => c !== code);
            delete compareDataStore[code];
            renderComparisonChips();
            renderComparisonChart();
        }

        function renderComparisonChips() {
            const container = document.getElementById('compareListContainer');
            const list = document.getElementById('compareList');
            list.innerHTML = '';

            if (compareSymbols.length === 0) {
                container.style.display = 'none';
                return;
            }

            container.style.display = 'flex';

            const mainChip = document.createElement('div');
            mainChip.style = 'background-color: rgba(38, 166, 154, 0.15); border: 1px solid #26a69a; color: #26a69a; padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; display: flex; align-items: center; gap: 0.25rem;';
            mainChip.innerHTML = `<span style="width: 8px; height: 8px; border-radius: 50%; background-color: #26a69a; display: inline-block;"></span> ${activeCode}`;
            list.appendChild(mainChip);

            compareSymbols.forEach((c, idx) => {
                const color = compareColors[idx % compareColors.length];
                const chip = document.createElement('div');
                chip.style = `background-color: rgba(255,255,255,0.05); border: 1px solid ${color}; color: ${color}; padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; display: flex; align-items: center; gap: 0.25rem;`;
                chip.innerHTML = `
                    <span style="width: 8px; height: 8px; border-radius: 50%; background-color: ${color}; display: inline-block;"></span>
                    ${c}
                    <span onclick="removeCompareSymbol('${c}')" style="cursor: pointer; margin-left: 0.25rem; font-weight: bold; font-size: 0.85rem;">&times;</span>
                `;
                list.appendChild(chip);
            });
        }

        function renderComparisonChart() {
            const lblSma = document.getElementById('lblSma');
            const lblEma = document.getElementById('lblEma');
            const lblThresholds = document.getElementById('lblThresholds');
            const sharesDiv = document.getElementById('sharesChartDiv');

            if (compareSymbols.length === 0) {
                if (lblSma) lblSma.style.display = 'flex';
                if (lblEma) lblEma.style.display = 'flex';
                if (lblThresholds) lblThresholds.style.display = 'flex';
                sharesDiv.style.display = 'block';
                if (activeCode && activeCode !== 'MARKET') {
                    loadSymbol(activeCode);
                }
                return;
            }

            if (lblSma) lblSma.style.display = 'none';
            if (lblEma) lblEma.style.display = 'none';
            if (lblThresholds) lblThresholds.style.display = 'none';
            sharesDiv.style.display = 'none';

            if (ratioChartInstance) {
                ratioChartInstance.remove();
                ratioChartInstance = null;
            }
            if (sharesChartInstance) {
                sharesChartInstance.remove();
                sharesChartInstance = null;
            }

            const ratioContainer = document.getElementById('ratioChartDiv');
            const chartOptions = {
                layout: {
                    background: { type: 'solid', color: '#181c27' },
                    textColor: '#d1d4dc',
                    fontFamily: "'Outfit', sans-serif",
                },
                grid: {
                    vertLines: { color: 'rgba(42, 46, 57, 0.15)', style: 3 },
                    horzLines: { color: 'rgba(42, 46, 57, 0.15)', style: 3 },
                },
                rightPriceScale: {
                    borderColor: 'rgba(42, 46, 57, 0.3)',
                },
                timeScale: {
                    borderColor: 'rgba(42, 46, 57, 0.3)',
                },
            };

            ratioChartInstance = LightweightCharts.createChart(ratioContainer, {
                ...chartOptions,
                width: ratioContainer.clientWidth || 800,
                height: 380,
            });

            if (!activeSymbolData) return;

            const mainDataSorted = [...activeSymbolData.recent_changes].reverse().map(item => {
                return {
                    time: parseDateToISO(item.report_date),
                    value: item.ratio
                };
            }).filter(d => d.time);

            const mainSeries = ratioChartInstance.addLineSeries({
                color: '#26a69a',
                lineWidth: 2.5,
                title: activeCode,
                priceFormat: {
                    type: 'custom',
                    formatter: price => price.toFixed(2) + '%',
                },
            });
            mainSeries.setData(mainDataSorted);

            compareSymbols.forEach((c, idx) => {
                const color = compareColors[idx % compareColors.length];
                const symData = compareDataStore[c];
                if (!symData) return;

                const sortedSymData = [...symData.recent_changes].reverse().map(item => {
                    return {
                        time: parseDateToISO(item.report_date),
                        value: item.ratio
                    };
                }).filter(d => d.time);

                const series = ratioChartInstance.addLineSeries({
                    color: color,
                    lineWidth: 2.0,
                    title: c,
                    priceFormat: {
                        type: 'custom',
                        formatter: price => price.toFixed(2) + '%',
                    },
                });
                series.setData(sortedSymData);
            });

            ratioChartInstance.timeScale().fitContent();
        }

        // ----------------------------------------------------
        // SECTOR HEATMAP ENGINE
        // ----------------------------------------------------
        function renderSectorHeatmap(sectors) {
            const grid = document.getElementById('sectorHeatmapGrid');
            grid.innerHTML = '';
            
            if (!sectors || sectors.length === 0) {
                grid.innerHTML = '<p style="text-align: center; color: var(--text-secondary); padding: 2rem; grid-column: 1 / -1;">Sektör verisi bulunamadı. Lütfen önce veri eşitlemesi yapın.</p>';
                return;
            }

            function formatVolume(val) {
                if (val >= 1e9) return (val / 1e9).toFixed(2) + ' Milyar';
                if (val >= 1e6) return (val / 1e6).toFixed(2) + ' Milyon';
                return val.toLocaleString();
            }

            sectors.forEach(sec => {
                const card = document.createElement('div');
                card.style = 'background-color: rgba(24, 28, 39, 0.3); border: 1px solid var(--border-color); border-radius: 8px; padding: 1rem; display: flex; flex-direction: column; gap: 0.75rem;';
                
                const header = document.createElement('div');
                header.style = 'display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255, 255, 255, 0.05); padding-bottom: 0.5rem;';
                
                const title = document.createElement('span');
                title.style = 'font-weight: 600; font-size: 0.85rem; color: var(--text-primary); text-transform: uppercase;';
                title.innerText = sec.sector === 'Bilinmeyen' ? 'DİĞER SEKTÖRLER' : sec.sector;
                
                const stats = document.createElement('span');
                stats.style = 'font-size: 0.8rem; font-weight: 600;';
                let medColor = 'var(--bullish)';
                if (sec.median_ratio < 10.0) medColor = 'var(--bearish)';
                else if (sec.median_ratio < 20.0) medColor = 'var(--warning)';
                
                stats.style.color = medColor;
                stats.innerText = `Medyan: %${sec.median_ratio.toFixed(2)}`;
                
                header.appendChild(title);
                header.appendChild(stats);
                card.appendChild(header);
                
                const symbolsDiv = document.createElement('div');
                symbolsDiv.style = 'display: flex; flex-wrap: wrap; gap: 0.4rem; align-content: flex-start;';
                
                sec.symbols.forEach(sym => {
                    const box = document.createElement('div');
                    let color = 'rgba(38, 166, 154, 0.1)';
                    let borderColor = 'rgba(38, 166, 154, 0.3)';
                    let textColor = '#d1d4dc';
                    if (sym.ratio < 10.0) {
                        color = 'rgba(239, 83, 80, 0.1)';
                        borderColor = 'rgba(239, 83, 80, 0.3)';
                        textColor = 'var(--bearish)';
                    } else if (sym.ratio < 20.0) {
                        color = 'rgba(245, 124, 0, 0.1)';
                        borderColor = 'rgba(245, 124, 0, 0.3)';
                        textColor = 'var(--warning)';
                    }
                    
                    box.style = `background-color: ${color}; border: 1px solid ${borderColor}; color: ${textColor}; padding: 0.3rem 0.45rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; cursor: pointer; transition: all 0.2s; position: relative;`;
                    box.title = `${sym.name}\nDolaşım Oranı: %${sym.ratio.toFixed(2)}\nDolaşımdaki Nominal Sermaye: ${formatVolume(sym.weight)} TRY`;
                    box.innerText = sym.code;
                    
                    box.onclick = (e) => {
                        e.stopPropagation();
                        loadSymbol(sym.code);
                    };
                    
                    box.onmouseover = () => {
                        box.style.transform = 'scale(1.08)';
                        box.style.boxShadow = '0 2px 8px rgba(0,0,0,0.5)';
                        box.style.borderColor = textColor;
                        box.style.backgroundColor = color.replace('0.1', '0.25');
                    };
                    box.onmouseout = () => {
                        box.style.transform = 'scale(1)';
                        box.style.boxShadow = 'none';
                        box.style.borderColor = borderColor;
                        box.style.backgroundColor = color;
                    };
                    
                    symbolsDiv.appendChild(box);
                });
                
                card.appendChild(symbolsDiv);
                grid.appendChild(card);
            });
        }

        // ----------------------------------------------------
        // SYSTEM SYNC / STATUS ENGINE
        // ----------------------------------------------------
        let isSyncing = false;
        let syncPollInterval = null;

        async function updateSyncStatusHeader() {
            try {
                const response = await fetch('/api/sync/status');
                if (!response.ok) return;
                const data = await response.json();
                
                const dot = document.getElementById('syncStatusDot');
                const text = document.getElementById('syncStatusText');
                const btn = document.getElementById('syncNowBtn');
                
                const state = data.sync_state;
                
                if (state.last_status === 'running') {
                    isSyncing = true;
                    dot.style.backgroundColor = 'var(--warning)';
                    dot.style.boxShadow = '0 0 8px var(--warning)';
                    dot.style.animation = 'pulse 1.5s infinite';
                    text.innerText = 'Veriler Eşitleniyor...';
                    btn.disabled = true;
                    btn.innerText = 'Eşitleniyor';
                    
                    if (!syncPollInterval) {
                        syncPollInterval = setInterval(updateSyncStatusHeader, 3000);
                    }
                } else {
                    isSyncing = false;
                    if (syncPollInterval) {
                        clearInterval(syncPollInterval);
                        syncPollInterval = null;
                        if (activeCode === 'MARKET') {
                            location.reload();
                        } else {
                            loadSymbol(activeCode);
                        }
                    }
                    
                    dot.style.animation = 'none';
                    dot.style.boxShadow = 'none';
                    
                    if (state.last_status === 'error') {
                        dot.style.backgroundColor = 'var(--bearish)';
                        text.innerText = 'Hata: Eşitleme başarısız';
                    } else {
                        dot.style.backgroundColor = 'var(--bullish)';
                        text.innerText = data.last_report_date ? `Son Rapor: ${parseDateLabel(data.last_report_date)}` : 'Veriler Hazır';
                    }
                    
                    if (data.cooldown_active) {
                        btn.disabled = true;
                        btn.innerText = `Eşitle (${data.cooldown_seconds_left}s)`;
                    } else {
                        btn.disabled = false;
                        btn.innerText = 'Eşitle';
                    }
                }
            } catch (err) {
                console.error("Failed to fetch sync status:", err);
            }
        }

        function parseDateLabel(isoDate) {
            if (!isoDate) return '';
            const parts = isoDate.split('-');
            if (parts.length === 3) {
                return `${parts[2]}.${parts[1]}.${parts[0]}`;
            }
            return isoDate;
        }

        async function triggerSync() {
            if (isSyncing) return;
            const btn = document.getElementById('syncNowBtn');
            btn.disabled = true;
            btn.innerText = 'İsteniyor...';
            
            try {
                const response = await fetch('/api/sync/run', { method: 'POST' });
                if (!response.ok) {
                    alert("Eşitleme isteği başarısız oldu.");
                    updateSyncStatusHeader();
                    return;
                }
                const data = await response.json();
                if (data.success) {
                    updateSyncStatusHeader();
                } else {
                    alert(data.message);
                    updateSyncStatusHeader();
                }
            } catch (err) {
                console.error("Failed to start sync:", err);
                alert("Eşitleme başlatılırken bağlantı hatası oluştu.");
                updateSyncStatusHeader();
            }
        }

        // Initialize status polling
        updateSyncStatusHeader();
        setInterval(updateSyncStatusHeader, 15000);
    </script>
</body>
</html>"""

    @app.get("/api/market")
    async def get_api_market() -> Any:
        latest_date = get_latest_report_date()
        if not latest_date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No archived free-float reports found.",
            )

        try:
            with store._connect() as conn:  # noqa: SLF001
                rows = conn.execute(
                    "SELECT code, name, ratio FROM freefloat_snapshots"
                    " WHERE report_date = ? ORDER BY ratio ASC",
                    (latest_date,),
                ).fetchall()
                event_rows = conn.execute(
                    """
                    SELECT event_type, COUNT(*) AS cnt FROM freefloat_events
                    WHERE report_date = ? AND severity = 'high'
                    GROUP BY event_type ORDER BY cnt DESC
                    """,
                    (latest_date,),
                ).fetchall()

                # Calculate severe risk count (<10%) and warning risk count (10%-20%)
                severe_risk_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM freefloat_snapshots WHERE report_date = ? AND ratio < 10.0",
                    (latest_date,),
                ).fetchone()
                severe_risk_count = (
                    int(severe_risk_row["cnt"]) if severe_risk_row else 0
                )

                warning_risk_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM freefloat_snapshots WHERE report_date = ? AND ratio >= 10.0 AND ratio < 20.0",
                    (latest_date,),
                ).fetchone()
                warning_risk_count = (
                    int(warning_risk_row["cnt"]) if warning_risk_row else 0
                )

                total_high_alerts_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM freefloat_events WHERE report_date = ? AND severity = 'high'",
                    (latest_date,),
                ).fetchone()
                total_high_alerts = (
                    int(total_high_alerts_row["cnt"]) if total_high_alerts_row else 0
                )

                # Determine previous report date to find top gainers and top losers
                prev_date_row = conn.execute(
                    "SELECT report_date FROM freefloat_reports WHERE report_date < ? ORDER BY report_date DESC LIMIT 1",
                    (latest_date,),
                ).fetchone()
                previous_date = prev_date_row["report_date"] if prev_date_row else None

                top_gainers = []
                top_losers = []
                if previous_date:
                    gainers_rows = conn.execute(
                        """
                        SELECT curr.code, curr.name, curr.ratio AS current_ratio, prev.ratio AS previous_ratio,
                               (curr.ratio - prev.ratio) AS delta
                        FROM freefloat_snapshots curr
                        JOIN freefloat_snapshots prev ON curr.code = prev.code AND prev.report_date = ?
                        WHERE curr.report_date = ? AND delta > 0
                        ORDER BY delta DESC LIMIT 5
                        """,
                        (previous_date, latest_date),
                    ).fetchall()
                    top_gainers = [
                        {
                            "code": r["code"],
                            "name": r["name"],
                            "current_ratio": float(r["current_ratio"]),
                            "previous_ratio": float(r["previous_ratio"]),
                            "delta": float(r["delta"]),
                        }
                        for r in gainers_rows
                    ]

                    losers_rows = conn.execute(
                        """
                        SELECT curr.code, curr.name, curr.ratio AS current_ratio, prev.ratio AS previous_ratio,
                               (curr.ratio - prev.ratio) AS delta
                        FROM freefloat_snapshots curr
                        JOIN freefloat_snapshots prev ON curr.code = prev.code AND prev.report_date = ?
                        WHERE curr.report_date = ? AND delta < 0
                        ORDER BY delta ASC LIMIT 5
                        """,
                        (previous_date, latest_date),
                    ).fetchall()
                    top_losers = [
                        {
                            "code": r["code"],
                            "name": r["name"],
                            "current_ratio": float(r["current_ratio"]),
                            "previous_ratio": float(r["previous_ratio"]),
                            "delta": float(r["delta"]),
                        }
                        for r in losers_rows
                    ]

            all_ratios = [float(r["ratio"]) for r in rows]
            n_symbols = len(all_ratios)
            if n_symbols == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No snapshot data for {latest_date}.",
                )

            import statistics

            median_ratio = statistics.median(all_ratios)

            leaderboard = [
                {"code": r["code"], "name": r["name"], "ratio": float(r["ratio"])}
                for r in rows
            ]
            event_summary = [
                {"event_type": r["event_type"], "count": int(r["cnt"])}
                for r in event_rows
            ]

            # Fetch latest market events representing changes
            all_events = store.symbol_events(limit=100)
            change_event_types = {
                "ratio_jump_up",
                "ratio_jump_down",
                "ratio_threshold_cross_down",
                "ratio_threshold_cross_up",
                "float_shares_jump_up",
                "float_shares_jump_down",
                "capital_change_detected",
            }
            dramatic_changes = [
                e for e in all_events if e["event_type"] in change_event_types
            ]

            return {
                "report_date": latest_date,
                "n_symbols": n_symbols,
                "median_ratio": round(median_ratio, 2),
                "leaderboard": leaderboard,
                "event_summary": event_summary,
                "dramatic_changes": dramatic_changes[:30],  # Limit to top 30
                "summary": {
                    "severe_risk_count": severe_risk_count,
                    "warning_risk_count": warning_risk_count,
                    "total_high_alerts": total_high_alerts,
                },
                "top_gainers": top_gainers,
                "top_losers": top_losers,
            }
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/api/symbol/{code}")
    async def get_api_symbol(code: str) -> Any:
        try:
            report = store.build_symbol_report(code.upper(), limit=1000)
            return report
        except Exception as e:
            from ..errors import NotFoundError

            if isinstance(e, NotFoundError):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=str(e),
                ) from e
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/api/market/sectors")
    async def get_api_market_sectors() -> Any:
        latest_date = get_latest_report_date()
        if not latest_date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No archived free-float reports found.",
            )
        try:
            return store.get_sector_heatmap(latest_date)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/api/sync/status")
    async def get_api_sync_status() -> Any:
        try:
            stats = store.archive_stats()
            sync_states = stats.get("sync_state", [])
            vap_state = next((s for s in sync_states if s["source"] == "vap"), None)

            if not vap_state:
                vap_state = {
                    "source": "vap",
                    "last_attempt_at": None,
                    "last_success_at": None,
                    "last_report_date": None,
                    "cooldown_until": None,
                    "last_status": "never_run",
                    "last_error": None,
                }

            cooldown_active = False
            cooldown_seconds_left = 0
            if vap_state.get("cooldown_until"):
                try:
                    cooldown_time = datetime.fromisoformat(vap_state["cooldown_until"])
                    now = datetime.now(UTC)
                    if cooldown_time > now:
                        cooldown_active = True
                        cooldown_seconds_left = int(
                            (cooldown_time - now).total_seconds()
                        )
                except Exception:
                    pass

            return {
                "sync_state": vap_state,
                "cooldown_active": cooldown_active,
                "cooldown_seconds_left": max(0, cooldown_seconds_left),
                "last_report_date": stats.get("last_report_date"),
                "reports_count": stats.get("reports"),
                "symbols_count": stats.get("symbols"),
            }
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    def _bg_sync() -> None:
        try:
            from ..layers import freefloat_archive

            logger.info("Background VAP free-float sync thread started")
            freefloat_archive.sync_archive(
                latest=True,
                since=None,
                until=None,
                max_days=None,
                resume=False,
                store=store,
            )
            logger.info("Background VAP free-float sync thread completed")
        except Exception:
            logger.exception("Background VAP free-float sync thread failed")

    @app.post("/api/sync/run")
    async def run_api_sync(background_tasks: BackgroundTasks) -> Any:
        try:
            logger.info("Starting VAP free-float sync via API")
            now = datetime.now(UTC)
            with store._connect() as conn:
                row = conn.execute(
                    "SELECT cooldown_until FROM sync_state WHERE source = ?",
                    ("vap",),
                ).fetchone()
                if row and row["cooldown_until"]:
                    cooldown_time = datetime.fromisoformat(row["cooldown_until"])
                    if cooldown_time > now:
                        seconds_left = int((cooldown_time - now).total_seconds())
                        logger.warning(
                            "Sync request rejected due to active cooldown",
                            extra={"seconds_left": seconds_left},
                        )
                        return {
                            "success": False,
                            "message": f"Sync cooldown active. Try again in {seconds_left} seconds.",
                            "cooldown_active": True,
                            "cooldown_seconds_left": seconds_left,
                        }

            background_tasks.add_task(_bg_sync)
            logger.info("Sync task queued successfully in background")
            return {"success": True, "message": "Sync started in background."}
        except Exception as e:
            logger.exception("Failed to start sync via API")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/img/market.png")
    async def get_img_market(request: Request) -> Any:
        latest_date = get_latest_report_date()
        if not latest_date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No archived free-float reports found.",
            )

        cache_key = f"market_{latest_date}"
        etag = f'"{cache_key}"'

        if_none_match = request.headers.get("if-none-match")
        if if_none_match == etag:
            logger.info(
                "Serving market image via 304 Not Modified",
                extra={"cache_key": cache_key},
            )
            return Response(status_code=status.HTTP_304_NOT_MODIFIED)

        headers = {
            "ETag": etag,
            "Cache-Control": "public, max-age=3600",
        }

        if cache_key in cache and cache[cache_key].exists():
            logger.info(
                "Serving market image from memory cache", extra={"cache_key": cache_key}
            )
            return FileResponse(
                cache[cache_key], media_type="image/png", headers=headers
            )

        out_path = Path(tmp_dir) / f"{cache_key}.png"
        try:
            logger.info(
                "Generating market image dashboard", extra={"cache_key": cache_key}
            )
            req = DashboardRequest(out=out_path, market=True)
            run_dashboard(req, store=store)
            cache[cache_key] = out_path
            return FileResponse(out_path, media_type="image/png", headers=headers)
        except Exception as e:
            from ..errors import NotFoundError

            logger.exception(
                "Failed to generate market image dashboard",
                extra={"cache_key": cache_key},
            )
            if isinstance(e, NotFoundError):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=str(e),
                ) from e
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    @app.get("/img/symbol/{code}.png")
    async def get_img_symbol(code: str, request: Request) -> Any:
        symbol = code.upper()
        latest_date = get_latest_report_date()
        if not latest_date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No reports found.",
            )

        cache_key = f"symbol_{symbol}_{latest_date}"
        etag = f'"{cache_key}"'

        if_none_match = request.headers.get("if-none-match")
        if if_none_match == etag:
            logger.info(
                "Serving symbol image via 304 Not Modified",
                extra={"symbol": symbol, "cache_key": cache_key},
            )
            return Response(status_code=status.HTTP_304_NOT_MODIFIED)

        headers = {
            "ETag": etag,
            "Cache-Control": "public, max-age=3600",
        }

        if cache_key in cache and cache[cache_key].exists():
            logger.info(
                "Serving symbol image from memory cache",
                extra={"symbol": symbol, "cache_key": cache_key},
            )
            return FileResponse(
                cache[cache_key], media_type="image/png", headers=headers
            )

        out_path = Path(tmp_dir) / f"{cache_key}.png"
        try:
            logger.info(
                "Generating symbol image dashboard",
                extra={"symbol": symbol, "cache_key": cache_key},
            )
            req = DashboardRequest(out=out_path, symbol=symbol)
            run_dashboard(req, store=store)
            cache[cache_key] = out_path
            return FileResponse(out_path, media_type="image/png", headers=headers)
        except Exception as e:
            from ..errors import NotFoundError

            logger.exception(
                "Failed to generate symbol image dashboard",
                extra={"symbol": symbol, "cache_key": cache_key},
            )
            if isinstance(e, NotFoundError):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=str(e),
                ) from e
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    return app
