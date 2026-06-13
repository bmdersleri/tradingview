# tvcli — Future Implementation Plan

This document outlines the detailed, step-by-step roadmap for implementing advanced technical indicators, settings management, alert histories, KAP announcement feeds, and sector analysis modules into the BIST Free-Float Dashboard.

---

## Phase 1: Interactive Technical Indicators on the Web UI

This phase brings advanced charting capabilities directly to the web dashboard, enabling SMA, EMA, WMA, Bollinger Bands, RSI, and MACD overlays.

### Tasks
- **T1.1: Extend UI API (`app.py` / `/api/ohlcv/indicators`)**
  - Create a FastAPI endpoint to compute indicator series (SMA, EMA, WMA, BBands, RSI, MACD) dynamically for a given symbol and configuration parameters.
  - Integrate with the existing `src/tvcli/layers/indicators.py` module to compute averages and bands.
- **T1.2: Add Technical Indicator Controls to Dashboard UI**
  - Embed toggle buttons and configuration inputs (e.g., period dropdowns) on the main dashboard chart area.
  - Enable toggling overlays (MA lines, Bollinger Bands) and indicator panes (RSI, MACD) below the main price pane.
- **T1.3: Render Indicators on Lightweight Charts**
  - Update JavaScript charting code in `app.py` to dynamically create line series for MAs, area series for Bollinger Bands, and standalone bottom charts for RSI and MACD.
  - Implement dynamic updates when changing active indicator toggles or symbols.

---

## Phase 2: Interactive Alert Settings Panel

Allows the user to view, edit, and test alert thresholds and Telegram bot parameters directly from a secure Settings tab in the web interface.

### Tasks
- **T2.1: Implement Settings API**
  - Add `GET /api/settings` to load the active `config.toml` structure (Telegram token, Chat ID, Webhook URL, thresholds).
  - Add `POST /api/settings/update` to validate and write new configuration parameters back to `config.toml`.
  - Add `POST /api/settings/test` to trigger a test Telegram/Webhook message.
- **T2.2: Build Settings UI Form**
  - Create a sleek "Ayarlar / Settings" modal or tab in the HTML/CSS template.
  - Build inputs for Telegram tokens, chat IDs, webhook targets, and numeric sliders for ratio jump/drop alert thresholds.
  - Integrate visual success/failure feedback on test alarms.

---

## Phase 3: Alert History Log Table

Provides a chronological logging table inside the web interface to audit previously triggered alerts.

### Tasks
- **T3.1: Add Alert Log Table & API Query**
  - Create `GET /api/alerts/history` to query logged alerts from the SQLite database.
  - Enable sorting and filtering by symbol, severity, and status (sent, failed, pending).
- **T3.2: Build Alert History UI Component**
  - Add a "Alarmlar / Alerts" tab to the dashboard.
  - Render a responsive data table containing: Date, Symbol, Change details, Severity, Channel, and Delivery Status.

---

## Phase 4: KAP (Public Disclosure Platform) Integration

Matches free-float changes with their corresponding KAP official announcements and prints them on the chart timeline.

### Tasks
- **T4.1: Create KAP News Scraper & Storage**
  - Implement a crawler/parser in `src/tvcli/layers/kap.py` or fetch news feed matching stock codes.
  - Store announcements in a `freefloat_news` SQLite table linked by symbol and publication date.
- **T4.2: Embed News Markers on Lightweight Charts**
  - Use Lightweight Charts "markers" API to pin small news icons on dates where KAP announcements occurred.
  - Show interactive tooltips containing the disclosure details/text when clicking a marker.

---

## Phase 5: Sector Detail & Comparative Analysis

Provides deep-dives into specific BIST sectors with comparative metrics and volatility.

### Tasks
- **T5.1: Implement Sector API**
  - Create `GET /api/sectors/{sector}` to return sector-wide statistics (average ratio, total nominal capital, top low-float risks, volatility index).
- **T5.2: Build Sector Detail UI**
  - Enable clicking a sector block on the Heatmap to load a dedicated sectoral detail dashboard.
  - Render a sector-specific line chart showing average free-float ratio trend and a comparison grid of constituent stocks.
