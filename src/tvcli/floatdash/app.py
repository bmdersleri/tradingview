# ruff: noqa: E501
"""Interactive free-float dashboard web server application."""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse

from ..layers import freefloat_archive
from ..layers.float_dashboard import DashboardRequest, run_dashboard


def create_app(store: freefloat_archive.ArchiveStore | None = None) -> FastAPI:
    app = FastAPI(title="tvcli float-dashboard")

    if store is None:
        store = freefloat_archive.ArchiveStore()

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
            justify-content: center;
            align-items: center;
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
    </style>
</head>
<body>
    <header>
        <div class="logo-container">
            <span class="logo-icon">📊</span>
            <span class="logo-title">BIST Free-Float Terminal</span>
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

            <!-- Dashboard Chart Card -->
            <div class="chart-card">
                <img id="chartImg" class="chart-img" src="/img/market.png" alt="Dashboard Chart" onload="hideLoader()" onerror="handleImageError()" />
                <div id="loaderOverlay" class="loader-overlay active">
                    <div class="spinner"></div>
                    <p id="loaderText">Loading BIST Market Overview...</p>
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
            document.getElementById('marketEventsCard').style.display = 'block';

            document.querySelectorAll('.leaderboard-item').forEach(item => item.classList.remove('active'));
            document.getElementById('item-market').classList.add('active');

            showLoader("Loading BIST Market Overview...");
            document.getElementById('chartImg').src = `/img/market.png?t=${Date.now()}`;
        }

        async function loadSymbol(code) {
            activeCode = code.toUpperCase();
            document.getElementById('errorBox').style.display = 'none';
            document.getElementById('marketEventsCard').style.display = 'none';
            document.getElementById('symbolEventsCard').style.display = 'block';

            document.querySelectorAll('.leaderboard-item').forEach(item => item.classList.remove('active'));
            const listItem = document.getElementById(`item-${activeCode}`);
            if (listItem) {
                listItem.classList.add('active');
                listItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }

            showLoader(`Generating Deep-Dive for ${activeCode}...`);
            document.getElementById('chartImg').src = `/img/symbol/${activeCode}.png?t=${Date.now()}`;

            try {
                const response = await fetch(`/api/symbol/${activeCode}`);
                if (!response.ok) return;
                const data = await response.json();

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
            } catch (err) {
                console.error("Failed to load symbol details:", err);
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
            report = store.build_symbol_report(code.upper())
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

    @app.get("/img/market.png")
    async def get_img_market() -> FileResponse:
        latest_date = get_latest_report_date()
        if not latest_date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No archived free-float reports found.",
            )

        cache_key = f"market_{latest_date}"
        if cache_key in cache and cache[cache_key].exists():
            return FileResponse(cache[cache_key], media_type="image/png")

        out_path = Path(tmp_dir) / f"{cache_key}.png"
        try:
            req = DashboardRequest(out=out_path, market=True)
            run_dashboard(req, store=store)
            cache[cache_key] = out_path
            return FileResponse(out_path, media_type="image/png")
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

    @app.get("/img/symbol/{code}.png")
    async def get_img_symbol(code: str) -> FileResponse:
        symbol = code.upper()
        latest_date = get_latest_report_date()
        if not latest_date:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No reports found.",
            )

        cache_key = f"symbol_{symbol}_{latest_date}"
        if cache_key in cache and cache[cache_key].exists():
            return FileResponse(cache[cache_key], media_type="image/png")

        out_path = Path(tmp_dir) / f"{cache_key}.png"
        try:
            req = DashboardRequest(out=out_path, symbol=symbol)
            run_dashboard(req, store=store)
            cache[cache_key] = out_path
            return FileResponse(out_path, media_type="image/png")
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

    return app
