# Free-Float Archive and Per-Symbol Report Design

Scope: replace the current TTL-cache-only VAP free-float flow with a persistent
archive that stores all discovered daily reports, synchronizes them into a local
SQLite database, and generates rich per-symbol analytical reports.

## Goals

1. Persist VAP/MKK free-float reports outside the TTL cache.
2. Support full-history synchronization, not just the latest report.
3. Generate per-symbol reports from the local archive without re-downloading the
   same report.
4. Keep cache concerns and archive concerns separate.
5. Preserve the existing `data float` command for simple lookups while adding a
   durable analytical workflow.

## Non-goals

- No external database server.
- No attempt to infer causal business explanations for ratio changes.
- No background daemon in the first iteration; synchronization is explicit via
  CLI.

## Recommended Architecture

Use a separate SQLite database at `~/.local/share/tvcli/archive.sqlite3`.

Reasoning:

- `cache.sqlite3` remains ephemeral and TTL-based.
- Archive data needs migrations, indexes, and idempotent upserts.
- Analytical queries should not compete with cache maintenance semantics.

## Data Model

### 1. `freefloat_reports`

One row per published VAP report date.

Suggested columns:

- `report_date TEXT PRIMARY KEY` - ISO date, e.g. `2026-06-11`
- `source TEXT NOT NULL` - `"VAP / MKK"`
- `published_label TEXT NOT NULL` - original report label, e.g. `11.06.2026`
- `row_count INTEGER NOT NULL`
- `content_sha256 TEXT NOT NULL`
- `fetched_at TEXT NOT NULL`
- `synced_at TEXT NOT NULL`

Purpose:

- Tracks what dates exist locally.
- Makes sync idempotent.
- Detects same-date upstream revisions via content hash.

### 2. `freefloat_snapshots`

One row per symbol per report date.

Suggested columns:

- `report_date TEXT NOT NULL`
- `code TEXT NOT NULL`
- `isin TEXT NOT NULL`
- `name TEXT NOT NULL`
- `member_name TEXT NOT NULL`
- `float_shares REAL NOT NULL`
- `capital REAL NOT NULL`
- `ratio REAL NOT NULL`
- `source TEXT NOT NULL`
- `PRIMARY KEY (report_date, code)`

Indexes:

- `INDEX idx_freefloat_snapshots_code_date (code, report_date)`
- `INDEX idx_freefloat_snapshots_isin_date (isin, report_date)`

Purpose:

- Canonical time series store.
- Powers all per-symbol reports.

### 3. `freefloat_changes`

Materialized per-symbol deltas between a report and the previous available
report for the same symbol.

Suggested columns:

- `report_date TEXT NOT NULL`
- `code TEXT NOT NULL`
- `previous_report_date TEXT`
- `ratio_delta REAL`
- `ratio_delta_pct REAL`
- `float_shares_delta REAL`
- `float_shares_delta_pct REAL`
- `capital_delta REAL`
- `capital_delta_pct REAL`
- `PRIMARY KEY (report_date, code)`

Purpose:

- Avoid recomputing common deltas on every report query.
- Gives direct “what changed” output.

### 4. `freefloat_events`

Analytical events derived from snapshots and changes.

Suggested columns:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `report_date TEXT NOT NULL`
- `code TEXT NOT NULL`
- `event_type TEXT NOT NULL`
- `severity TEXT NOT NULL`
- `metric_value REAL`
- `threshold_value REAL`
- `payload_json TEXT NOT NULL`

Initial event types:

- `ratio_threshold_cross_up`
- `ratio_threshold_cross_down`
- `ratio_jump_up`
- `ratio_jump_down`
- `float_shares_jump_up`
- `float_shares_jump_down`
- `new_52w_high_ratio`
- `new_52w_low_ratio`
- `liquidity_risk_low_float`
- `capital_change_detected`

Purpose:

- Separates raw data from analytical interpretation.
- Makes alerting and report summaries cheap.

### 5. `freefloat_symbol_summary`

One materialized summary row per symbol, refreshed during sync.

Suggested columns:

- `code TEXT PRIMARY KEY`
- `isin TEXT NOT NULL`
- `name TEXT NOT NULL`
- `first_report_date TEXT NOT NULL`
- `last_report_date TEXT NOT NULL`
- `last_ratio REAL NOT NULL`
- `last_float_shares REAL NOT NULL`
- `last_capital REAL NOT NULL`
- `report_count INTEGER NOT NULL`
- `min_ratio REAL NOT NULL`
- `max_ratio REAL NOT NULL`
- `avg_ratio REAL NOT NULL`
- `ratio_volatility REAL NOT NULL`
- `last_change_direction TEXT NOT NULL`
- `risk_flags_json TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Purpose:

- Fast list and scan queries.
- Ready-to-use summary block for symbol reports.

## Synchronization Strategy

Add a dedicated archive layer, for example `src/tvcli/layers/freefloat_archive.py`.

### Sync modes

1. `latest`
   - Fetch latest published report.
   - Upsert snapshot rows.
   - Recompute changes/events/summary for affected symbols.

2. `backfill`
   - Walk backward by date from a requested start or end range.
   - Attempt report fetch.
   - Insert only published dates.
   - Skip `NotFoundError` dates quietly.

3. `full`
   - Bootstrap mode.
   - Walk backwards until a configured stop condition:
     - user-provided `--since`
     - or `--max-days`
     - or first already-synced contiguous boundary when `--resume` is active

### Sync rules

- Use the existing VAP adapter for network fetch and XLSX parse.
- Split network concerns from persistence concerns.
- Upsert `freefloat_reports` and `freefloat_snapshots` inside a transaction.
- Regenerate `freefloat_changes`, `freefloat_events`, and
  `freefloat_symbol_summary` for touched symbols in the same transaction when
  feasible; otherwise stage them in a second transaction after the raw insert.
- Persist timestamps in UTC ISO-8601.
- Archive writes must be idempotent.

### Revision handling

If the same `report_date` arrives with a different `content_sha256`:

- overwrite snapshot rows for that date
- recompute downstream changes/events/summary for affected symbols
- mark the report as re-synced with a fresh `synced_at`

## Reporting Contract

Add a report builder that reads only from `archive.sqlite3`.

### `tvcli data float report SYMBOL --json`

Expected payload sections:

- `symbol`
- `identity`
  - `code`
  - `isin`
  - `name`
- `latest`
  - `report_date`
  - `ratio`
  - `float_shares`
  - `capital`
- `summary`
  - `report_count`
  - `first_report_date`
  - `last_report_date`
  - `min_ratio`
  - `max_ratio`
  - `avg_ratio`
  - `ratio_volatility`
- `trend`
  - `direction`
  - `lookback_reports`
  - `ratio_change_total`
  - `ratio_change_pct_total`
  - `rolling_5_report_avg`
  - `rolling_20_report_avg`
- `recent_changes`
  - last N change rows
- `events`
  - recent/high-severity event rows
- `risk`
  - low-float flags
  - concentration/manipulation caution notes

### Human output

Readable sections:

1. Latest snapshot
2. Historical range summary
3. Recent changes table
4. Event timeline
5. Risk notes

The report should not hit the network. If the symbol is absent locally, return a
not-found error with a hint to run sync first.

## CLI Surface

Recommended additions under `tvcli data float`:

- `tvcli data float sync [--latest] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--max-days N] [--resume] [--json]`
- `tvcli data float report SYMBOL [--limit 20] [--json]`
- `tvcli data float history SYMBOL [--limit 100] [--json]`
- `tvcli data float events [SYMBOL] [--since YYYY-MM-DD] [--severity high] [--json]`
- `tvcli data float archive stats [--json]`

Backward compatibility:

- keep existing `tvcli data float SYMBOL`
- keep existing `tvcli data float --all`
- those commands can continue using the latest fetched report path for simple
  lookup, while the new archive commands use the persistent store

## Analysis Rules

Initial report/event math should stay explicit and deterministic.

### Thresholds

Default thresholds, configurable later:

- low free-float risk: `ratio < 20`
- severe low free-float risk: `ratio < 10`
- ratio jump event: absolute `ratio_delta >= 5`
- float share jump event: absolute percentage change `>= 10`
- new local extreme: highest/lowest ratio in trailing 252 report-days or all
  available reports when history is shorter

### Trend classification

Based on archive data only:

- `rising`
- `falling`
- `flat`
- `volatile`

Derived from the latest sequence of report-to-report ratio changes plus
volatility of the ratio series.

## Implementation Slices

### Slice 1

- archive DB path config
- archive schema bootstrap
- sync latest
- symbol report from local DB
- unit tests for schema, upsert, and report output

### Slice 2

- date-range backfill
- changes materialization
- events materialization
- archive stats command

### Slice 3

- summary table refresh
- history/events listing commands
- documentation and smoke coverage

## Testing Plan

### Unit

- schema bootstrap
- idempotent upsert
- same-date revision overwrite
- delta generation
- event generation
- summary refresh
- report builder for:
  - no history
  - single report
  - multi-report rising trend
  - volatile series
  - low-float risk

### Integration

- sync latest against live VAP with opt-in network mark
- backfill small date window
- report command after sync

### Regression

- existing `data float` single-symbol path still works
- existing `chart signal` free-float enrichment remains unchanged

## Recommendation

Implement this as a separate archive layer and new CLI commands, not by
expanding the TTL cache abstraction. That keeps the current fast lookup path
stable while adding the full-history analytical workflow you want.
