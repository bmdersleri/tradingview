# Free-Float Analytics Suite v2 Design

Scope: five analytical improvements on top of the existing free-float archive
(shipped in `2026-06-12-freefloat-archive-design.md`): bug fix, trend vote,
percentile/liquidity, integrity verification, and a visual dashboard.

## Context

The archive backfill was running and exposing real data quality issues:

- **ENPRA (ENPARA BANK)** has a flat free-float ratio (0.12%) across all 140+
  reports. The 52-week extrema logic emitted *both* `new_52w_high_ratio` and
  `new_52w_low_ratio` on every single report — because `ratio >= max(recent)`
  and `ratio <= min(recent)` are simultaneously true when `max == min`.
- Three additional signal opportunities were identified: directional trend from
  the ratio trajectory, cross-sectional percentile context, position-sizing hint.

## Parts

### Part A — Degenerate 52-week double-event fix

**File:** `freefloat_archive.py`, `_build_events`.

Changed the extrema window from *all history including current* to *strictly
prior reports*. Made high/low mutually exclusive with `elif`. Empty prior window
(first observation) emits neither event.

Result: flat series (ENPRA) emit zero extrema events. Rising/falling series emit
at most one (the one that set a new bar). The fix is retroactive — `_rebuild_symbol`
replays all events on every sync, so the archive self-corrects on the next
`float-sync` for each affected symbol.

**Key invariant:** `new_52w_high_ratio` and `new_52w_low_ratio` are now mutually
exclusive and require a strictly new extreme against the prior window, not just
"equal to the current value."

### Part B — Free-float trend vote

**Files:** `signals.py`, `commands/chart.py`.

`vote_free_float_trend(ratios)` computes net change over the last 20 archived
ratio readings (oldest→newest). Strength is proportional to % move relative to
mean, capped at 1.0. Flat or insufficient history → vote 0, strength 0.

Added to `_REGIME_WEIGHTS` at low weight (0.3–0.4 depending on regime) — it is
a lagging context voice, not a price-derived driver.

`apply_float_trend(report, ratios)` appends the vote and re-derives
score/label/confidence. Unlike `apply_liquidity`/`apply_event_risk` (pure
confidence damps), this is a real voter — it can shift a borderline hold.

**Command boundary** (`chart.py`): `_float_ratios_for(symbol)` pulls
`ArchiveStore().symbol_history(code, limit=20)` → oldest-first floats.
Called in both `signal_query` and `analyze_query` (when `--auto` produced a
signal block). BIST-guarded, local-only, never blocks the signal.

### Part C — Percentile + liquidity score + position-sizing hint

**Files:** `freefloat_archive.py` (`ratio_percentile`), `freefloat.py`
(`liquidity_score`), surfaced in `float-report` payload.

`ArchiveStore.ratio_percentile(code, report_date=None)` pure SQL rank:
`SELECT COUNT(*) WHERE report_date = ? AND ratio < ?` → `{rank, total,
percentile, lower_count}`. Non-BIST and absent symbols raise `NotFoundError`.

`freefloat.liquidity_score(record, last_price=None)` buckets the ratio
(`deep ≥ 20%, thin 10–20%, severe < 10%`) and computes tradable free-float cap
(`float_shares × last_price`) with a conservative max-order-size hint (1% of
free cap). `last_price` optional — cap/hint are null when omitted.

`build_symbol_report` payload gains `percentile` and `liquidity` top-level keys.
`float-dashboard` deep-dive renders both in the title bar.

### Part D — Archive integrity (`float-verify`)

**Files:** `freefloat_archive.py` (`missing_business_days`), `commands/data.py`.

`missing_business_days(since, until)` iterates weekdays, calls
`has_report_date`/`is_known_empty` per day. Returns only dates with neither a
stored report nor a `freefloat_missing` stamp — true gaps the backfill hasn't
touched.

`data float-verify --since --until` reports: business_days, stored,
known_empty, gaps list, gap_count, coverage_pct. Local-only, no network.

### Part E — Visual dashboard

**New file:** `layers/float_dashboard.py`.

Follows `analyze.py` matplotlib-Agg precedent: `_load_pyplot()` sets
`matplotlib.use("Agg")`, all rendering happens in process, `fig.savefig(path,
dpi=100, facecolor=bg, bbox_inches="tight")`.

Two modes dispatched by `run_dashboard(DashboardRequest)`:

**Deep dive** (`symbol`): reads `build_symbol_report` → 3 stacked panels
(height_ratios=[3,1,1]):
- Ratio history line + dashed 20%/10% threshold lines + red span below 20 if
  low_float + diamond markers at high-severity event dates.
- Float shares bar chart.
- Ratio delta bars (green positive / red negative) + ±5 threshold lines.
Title: `CODE — NAME  float X.XX% ⚠ SEVERE  pct: lowest R/T`.

**Market overview** (`market`): queries `archive_stats()` for latest date, then
raw SQL on `freefloat_snapshots` → 3 panels:
- Ratio distribution histogram + median line.
- Horizontal lowest-float leaderboard (top N by ascending ratio).
- High-severity event counts by type from `freefloat_events`.

Payload in both modes: `{mode, symbol|null, report_date, path, bytes, ...summary}`.

## Data model — no schema changes

All new features read existing tables. Two new read-only `ArchiveStore` methods
(`ratio_percentile`, `missing_business_days`) add query surface without touching
the schema.

## Verification

```bash
just fmt && just lint && just test   # 155 tests, 86% coverage

# Part A: ENPRA should show no 52w extrema events after resync
tvcli data float-sync --latest --json
tvcli data float-events ENPRA --json

# Part B: trend vote present in signal
tvcli chart signal BIST:THYAO --json | python3 -c \
  "import sys,json; v={x['indicator'] for x in json.load(sys.stdin)['data']['votes']}; print('free_float_trend' in v)"

# Part C: percentile + liquidity in report
tvcli data float-report ENPRA --json | python3 -c \
  "import sys,json; d=json.load(sys.stdin)['data']; print(d.get('percentile'), d.get('liquidity'))"

# Part D: coverage over the backfilled range
tvcli data float-verify --since 2024-01-01 --until 2026-06-11 --json

# Part E: both dashboard modes render real PNGs
tvcli data float-dashboard THYAO --out /tmp/thyao.png --json
tvcli data float-dashboard --market --out /tmp/bist.png --json
file /tmp/thyao.png /tmp/bist.png   # → PNG image data
```

## Decisions

- **No schema migrations.** All new features read existing tables — the archive
  schema is stable.
- **No new dependencies.** matplotlib already present (from `chart analyze`);
  `statistics` stdlib.
- **Retroactive event fix.** `_rebuild_symbol` replays on every sync; no
  one-time migration script needed.
- **Low trend-vote weight.** 0.3–0.4 across all regimes — free-float trajectory
  is lagging context, not a price-derived driver.
- **`last_price` optional in liquidity_score.** Keeps `float-report` network-free;
  price wiring is follow-up work.
- **Market overview uses raw SQL** (not `build_symbol_report` per symbol) to
  avoid loading all 650+ symbols into Python objects.

## Note

Decision-support only, not financial advice. Free-float is a static liquidity
metric — the trend vote is a low-weighted, lagging context voice, never a price
prediction. VAP/MKK is open public data; reads are local-first against the
archive. The only networked path (`float-sync`) reuses the existing conservative
throttle.
