# tvcli — Active Tasks & Status Tracking

## Completed Infrastructure Improvements
- [x] Migrate to SQLite WAL (Write-Ahead Logging) Mode
- [x] SQLite Connection Timeouts (`timeout=10.0`)
- [x] Process-Wide Single-Time Schema Verification Per Database
- [x] Reset stuck (`running`) tasks during server shutdowns/crashes
- [x] Exponential Backoff & 3 Retries on external requests (VAP Report Downloads)
- [x] HTTP ETag / Cache-Control support for Dashboard Images
- [x] GZip Compression in FastAPI responses (`GZipMiddleware`)
- [x] Structured JSON Logging to stderr
- [x] `justfile` Developer Shortcuts (`reset-db`, `serve-dev`, `test-fast`)
- [x] WebSocket Real-Time Dashboard Sync Integration
- [x] Database Backup & Restore commands (`tvcli db backup/restore`) using SQLite Backup API

## Pending Tasks
- [x] Add Developer Test Data Seeding Command (`tvcli dev seed-db`)
- [x] Interactive Indicator Controls in UI (SMA, EMA, WMA, RSI, MACD)
- [x] Alert Thresholds and Telegram Settings Panel in UI (Settings UI)
- [x] Chronological Alert History and Audit Log Table (Alert History)
- [x] KAP Public Disclosures Integration and timeline markers on charts (KAP News)
- [x] Sector Details and Comparison Page (Sector Analysis)

## Active Development Details
- **FastAPI Server Port**: 8789
- **Active Database Path**: `~/.local/share/tvcli/archive.sqlite3`
