# BIST Free-Float Dashboard — Advanced Features Implementation Plan

This plan details the step-by-step roadmap for implementing interactive technical indicators, a settings management panel, an alert history audit log, and KAP announcement feed markers.

---

## Phase 1: Interactive Technical Indicators (WMA, BBands, RSI, MACD) [COMPLETED]

Integrate WMA 20, Bollinger Bands (20, 2), RSI 14, and MACD (12, 26, 9) indicators directly into the dashboard. Controls (checkboxes) and sub-charts must render and sync dynamically.

### Step 1.1: Complete JS Chart Rendering & Reset Logic (`createSymbolCharts`) [COMPLETED]
- Clear any existing indicator series (`wmaSeries`, `bbBasisSeries`, `bbUpperSeries`, `bbLowerSeries`, `rsiSeries`, `macdLineSeries`, `macdSignalSeries`, `macdHistSeries`) and sub-chart instances (`rsiChartInstance`, `macdChartInstance`) when drawing a new symbol to avoid canvas duplication or leaking memory.
- Add indicator checkboxes change listeners to call `updateSymbolCharts()` or `toggleIndicatorPanes()`.

### Step 1.2: Add Bollinger Bands & WMA 20 Overlays on Main Chart [COMPLETED]
- Compute WMA 20 using `calculateWMA(ratioData, 20)`.
- Compute Bollinger Bands (20, 2) using `calculateBollingerBands(ratioData, 20, 2)`.
- If the WMA/Bollinger Bands options are toggled, show the line overlays directly on `ratioChartInstance`.

### Step 1.3: Initialize and Sync RSI / MACD Sub-Charts [COMPLETED]
- When the RSI or MACD checkbox is enabled:
  - Create the sub-chart instances (`rsiChartInstance`, `macdChartInstance`) using `LightweightCharts.createChart` in `#rsiChartDiv` and `#macdChartDiv`.
  - Populate them with calculated RSI (`calculateRSI`) or MACD (`calculateMACD`) series.
  - Set CSS styles to display the divs (`display: block` or `display: none`).
- Synchronize all charts (Ratio Chart, Shares Chart, RSI, MACD) timescales using the `subscribeVisibleTimeRangeChange` event:
  - Scroll or zoom in one pane must automatically update the other panes.

---

## Phase 2: Interactive Alert Settings Panel (Settings UI) [COMPLETED]

Provide a secure and user-friendly interface to manage threshold settings and Telegram bot details.

### Step 2.1: Implement Settings API in FastAPI (`src/tvcli/floatdash/app.py`) [COMPLETED]
- **`GET /api/settings`**: Read settings from `config.toml` (XDG config directory) and return them as JSON. Sensitive keys (like Telegram token) should be partially masked for display.
- **`POST /api/settings/update`**: Accept JSON payload, validate types/formats, write updated values back to `config.toml`, and reload configuration in the running server.
- **`POST /api/settings/test`**: Trigger a test Telegram or webhook alert using the unsaved (or saved) credentials to verify connectivity.

### Step 2.2: Add Settings Tab/Modal to HTML UI [COMPLETED]
- Build a tabbed navigation system in the dashboard header: **"Dashboard"** and **"Settings (Ayarlar)"**.
- Style the Settings form using existing dark mode CSS tokens:
  - Fields for: Telegram Bot Token, Telegram Chat ID, Webhook URL, Severe Low Float Threshold (%), and Ratio Change Alarm Threshold (%).
  - Include a "Test Connection" button and a "Save Settings" button with clear success/error notifications.

---

## Phase 3: Alert History Log Table (Alert History) [COMPLETED]

Enable users to audit past alerts and filter them directly inside the web UI.

### Step 3.1: SQLite History Table & API Route [COMPLETED]
- Query the existing SQLite database (or log file) for sent alert history.
- **`GET /api/alerts/history`**: Return JSON list of past alerts sorted by date descending, supporting optional query parameters for filtering by symbol, severity (High, Info), and delivery channel (Telegram, Webhook).

### Step 3.2: Render Alert Logs Table in UI [COMPLETED]
- Add an **"Alerts (Alarm Geçmişi)"** tab to the dashboard.
- Create a responsive data table using HTML and standard CSS:
  - Columns: Timestamp, Symbol, Alarm Description, Ratio Value, Severity (rendered with badge colors), Status (Success / Failed).

---

## Phase 4: KAP (Public Disclosure Platform) Integration

Match free-float changes with their corresponding KAP disclosures, showing markers on the charts.

### Step 4.1: KAP RSS Scraper & API Endpoint
- Implement a parser to fetch KAP announcement feeds for BIST stocks (either from public RSS feeds or simulated API data).
- Save announcements into a new `kap_disclosures` table in the SQLite database, referencing `symbol` and `disclosure_date`.
- **`GET /api/symbol/{code}/kap`**: Retrieve announcements for a specific symbol to render on the client side.

### Step 4.2: Draw KAP Markers on Lightweight Charts
- Use the Lightweight Charts `setMarkers` API to add news/KAP pin markers on the ratio chart timeline on dates corresponding to public disclosures.
- Implement an interactive details tooltip/modal on marker click, displaying the subject and body of the KAP announcement.

---

## Verification & Acceptance Sequence

1. Run Ruff formatter and linter:
   ```bash
   uv run ruff format src/ tests/
   uv run ruff check src/ --output-format concise
   ```
2. Run unit tests to check if anything is broken:
   ```bash
   uv run pytest tests/ -q --tb=short -m "not slow" -n auto --dist=loadfile
   ```
3. Verify git workspace status:
   ```bash
   git diff --check
   ```
