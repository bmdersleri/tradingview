# tvcli — TradingView CLI Toolkit: Technical Specification

**Version:** 1.0
**Status:** Approved for implementation
**Target environment:** Ubuntu 22.04+ headless server, Python 3.11+
**Primary consumer:** Claude Code (agentic CLI usage) and human operators

---

## 1. Overview

`tvcli` is a layered command-line toolkit that exposes TradingView capabilities to
AI coding agents (Claude Code) and humans. TradingView has **no official public
data API**, so `tvcli` composes four independent access layers, each with a
different reliability/capability trade-off:

| Layer | Transport | Capabilities | Reliability |
|-------|-----------|--------------|-------------|
| `data` | HTTPS (scanner endpoint) | Screening, 13k+ fields, all markets incl. BIST | High |
| `ta` | HTTPS (TA endpoint) | Indicator summaries, BUY/SELL recommendation | High |
| `ohlcv` | WebSocket (authenticated) | Historical bars, any symbol/interval | Medium |
| `chart` | Playwright (headless Chromium) | Chart screenshots (PNG) | Medium |
| `ui` | Playwright (headless Chromium) | Alerts, watchlists, Pine editor | Low (fragile) |
| `serve` | FastAPI (inbound) | Webhook receiver for Pine alerts → Telegram | High (official path) |

**Design philosophy:** HTTP-first, browser-last. The browser layers exist only
for actions that are impossible over HTTP. Every command supports `--json` for
machine consumption.

### 1.1 Goals

- G1: Single binary-like entry point (`tvcli`) installable via `pipx install -e .`
- G2: Every command emits stable, versioned JSON with `--json` flag
- G3: Deterministic exit codes for agentic error handling
- G4: Session persistence — authenticate once, reuse across all layers
- G5: SQLite response cache with TTL to respect rate limits
- G6: Ship a Claude Code skill (`SKILL.md`) documenting all commands
- G7: Optional MCP wrapper over the same core (Phase Z, not required for v1)

### 1.2 Non-goals

- NG1: Trade execution / broker integration (out of scope, separate project)
- NG2: Real-time streaming quotes (use exchange APIs directly for that)
- NG3: Bypassing CAPTCHA or anti-bot systems programmatically
- NG4: Multi-user / multi-account support (single TradingView account)

### 1.3 Compliance note

The `data`, `ta`, and `ohlcv` layers use unofficial endpoints. This tool is for
**personal use** with conservative rate limits (see §8). The `serve` layer
(webhooks) is the only officially supported automation path. The README must
carry a ToS disclaimer.

---

## 2. Repository layout

```
tvcli/
├── pyproject.toml              # PEP 621, hatchling backend
├── justfile                    # lint, test, fmt, install targets
├── README.md
├── SPEC.md                     # this file
├── IMPLEMENTATION_PLAN.md
├── .claude/
│   └── skills/
│       └── tvcli/
│           └── SKILL.md        # Claude Code skill (Phase Z)
├── src/
│   └── tvcli/
│       ├── __init__.py         # __version__
│       ├── __main__.py         # python -m tvcli
│       ├── cli.py              # Typer app, subcommand registration
│       ├── config.py           # paths, settings, env overrides
│       ├── output.py           # JSON envelope + Rich table rendering
│       ├── errors.py           # exception hierarchy + exit codes
│       ├── cache.py            # SQLite TTL cache
│       ├── ratelimit.py        # token-bucket limiter
│       ├── auth/
│       │   ├── __init__.py
│       │   ├── session.py      # cookie/session store, validation
│       │   └── login.py        # Playwright-assisted login flow
│       ├── layers/
│       │   ├── __init__.py
│       │   ├── screener.py     # wraps tradingview-screener
│       │   ├── ta.py           # wraps tradingview-ta
│       │   ├── ohlcv.py        # WebSocket history client
│       │   ├── chart.py        # Playwright screenshots
│       │   └── ui.py           # Playwright UI actions
│       ├── commands/
│       │   ├── __init__.py
│       │   ├── data.py         # tvcli data ...
│       │   ├── ta.py           # tvcli ta ...
│       │   ├── ohlcv.py        # tvcli ohlcv ...
│       │   ├── chart.py        # tvcli chart ...
│       │   ├── ui.py           # tvcli ui ...
│       │   ├── auth.py         # tvcli auth ...
│       │   └── serve.py        # tvcli serve ...
│       └── webhook/
│           ├── __init__.py
│           ├── app.py          # FastAPI application
│           └── sinks.py        # Telegram / file / stdout sinks
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_output.py
    │   ├── test_errors.py
    │   ├── test_cache.py
    │   ├── test_ratelimit.py
    │   └── test_config.py
    ├── integration/            # marked @pytest.mark.network, skipped in CI
    │   ├── test_screener_live.py
    │   ├── test_ta_live.py
    │   └── test_ohlcv_live.py
    └── fixtures/
        ├── screener_response.json
        ├── ta_response.json
        └── ohlcv_frames.json
```

---

## 3. Technology stack

| Concern | Choice | Pin |
|---------|--------|-----|
| CLI framework | Typer | `>=0.12` |
| Terminal rendering | Rich | `>=13` |
| HTTP client | httpx | `>=0.27` |
| Screener access | tradingview-screener | latest |
| TA summaries | tradingview-ta | latest |
| WebSocket | websocket-client | `>=1.7` |
| Browser automation | playwright | `>=1.44` (chromium only) |
| Webhook server | fastapi + uvicorn | `>=0.110` / `>=0.29` |
| Data validation | pydantic | `>=2.7` |
| Cache | sqlite3 (stdlib) | — |
| Testing | pytest, pytest-asyncio, respx | latest |
| Lint/format | ruff (lint + format) | `>=0.4` |
| Type check | mypy (strict on `src/`) | `>=1.10` |

Python: `requires-python = ">=3.11"`. All code in English. Conventional commits.

---

## 4. Global CLI conventions

### 4.1 Invocation

```
tvcli [GLOBAL OPTIONS] <group> <command> [ARGS] [OPTIONS]
```

Global options (available on every command):

| Flag | Default | Meaning |
|------|---------|---------|
| `--json` | off | Emit JSON envelope to stdout, suppress Rich output |
| `--no-cache` | off | Bypass SQLite cache for this call |
| `--quiet` / `-q` | off | Suppress progress/log output on stderr |
| `--config PATH` | `~/.config/tvcli/config.toml` | Override config file |

### 4.2 JSON envelope (stable contract, schema_version 1)

Every `--json` response uses this envelope. **This is the contract Claude Code
parses — never break it without bumping `schema_version`.**

```json
{
  "schema_version": 1,
  "ok": true,
  "command": "ta.get",
  "generated_at": "2026-06-11T10:30:00+00:00",
  "cache": {"hit": false, "ttl_seconds": 300},
  "data": { ... },
  "error": null
}
```

On failure:

```json
{
  "schema_version": 1,
  "ok": false,
  "command": "ta.get",
  "generated_at": "2026-06-11T10:30:00+00:00",
  "cache": null,
  "data": null,
  "error": {
    "code": "SYMBOL_NOT_FOUND",
    "message": "Symbol 'BIST:XYZ' not found on screener 'turkey'",
    "retryable": false,
    "hint": "Run `tvcli data search XYZ --json` to find the correct symbol."
  }
}
```

The `error.hint` field is mandatory and must contain an actionable next step —
this is what lets an agent self-recover.

### 4.3 Exit codes

| Code | Name | Meaning |
|------|------|---------|
| 0 | OK | Success |
| 1 | GENERIC | Unexpected/unclassified error |
| 2 | USAGE | Bad arguments (Typer default) |
| 3 | AUTH_REQUIRED | No/expired session; run `tvcli auth login` |
| 4 | NOT_FOUND | Symbol/resource not found |
| 5 | RATE_LIMITED | Upstream throttling; retryable |
| 6 | UPSTREAM_CHANGED | Endpoint/DOM contract broke; needs maintenance |
| 7 | NETWORK | Connectivity/timeout; retryable |
| 8 | BROWSER | Playwright failure (launch, selector timeout) |

Error codes in JSON (`error.code`) map 1:1 to these names plus more granular
variants (`SYMBOL_NOT_FOUND`, `SESSION_EXPIRED`, `CAPTCHA_DETECTED`, etc.).
`retryable: true` is set only for codes 5 and 7.

### 4.4 Symbol notation

All commands accept `EXCHANGE:SYMBOL` (e.g. `BIST:THYAO`, `NASDAQ:AAPL`,
`BINANCE:BTCUSDT`). A bare symbol triggers a search-based resolution attempt;
ambiguity returns exit 4 with candidate list in `error.hint`.

---

## 5. Command reference

### 5.1 `tvcli auth` — session management

| Command | Description |
|---------|-------------|
| `auth login [--headless/--headed] [--timeout 300]` | Launch Playwright login flow. Headed mode requires `$DISPLAY` (use `xvfb-run` or VNC on the server). On success, persists Playwright `storage_state` and extracts `sessionid` + `sessionid_sign` cookies to the session store. |
| `auth status` | Validate current session against TradingView (lightweight authenticated request). Output: `{"authenticated": true, "username": "...", "plan": "...", "expires_hint": "..."}` |
| `auth logout` | Delete stored session files. |
| `auth import-cookie --sessionid VALUE [--sessionid-sign VALUE]` | Manual cookie injection (user copies from own browser DevTools). This is the recommended path on a fully headless box — no browser login needed. |

**Session store:** `~/.config/tvcli/session.json` (mode 0600):

```json
{
  "sessionid": "...",
  "sessionid_sign": "...",
  "storage_state_path": "~/.config/tvcli/storage_state.json",
  "captured_at": "2026-06-11T10:00:00+00:00",
  "username": "..."
}
```

All authenticated layers (`ohlcv`, `chart`, `ui`) read from this single store.
`data` and `ta` work unauthenticated but attach the cookie when present
(unlocks more scanner fields / intervals).

### 5.2 `tvcli data` — screener layer

Wraps `tradingview-screener`. Markets: `america`, `turkey`, `crypto`, `forex`,
and all others the library supports.

| Command | Description |
|---------|-------------|
| `data screen --market turkey --select FIELDS --where EXPR [--order-by FIELD --desc] [--limit 50]` | Run a screener query. `--select` is comma-separated field names. `--where` accepts a mini-DSL (see below). |
| `data fields --market turkey [--search rsi]` | List/search available scanner fields with types. |
| `data search QUERY [--market M]` | Symbol search/resolution. Returns candidates with exchange, type, description. |
| `data quote SYMBOL...` | Snapshot quote(s): price, change, volume, market cap via scanner single-symbol query. |

**Filter DSL for `--where`:** semicolon-separated clauses, each
`field OP value` with OP ∈ `> >= < <= == != in between`. Examples:

```bash
tvcli data screen --market turkey \
  --select name,close,volume,RSI,market_cap_basic \
  --where "RSI<30;volume>1000000" \
  --order-by volume --desc --limit 20 --json
```

The DSL parser translates to `tradingview-screener` Query/Column objects.
Parse errors → exit 2 with the offending clause in `error.hint`.

**JSON `data` payload:**

```json
{
  "market": "turkey",
  "total_matches": 412,
  "returned": 20,
  "columns": ["name", "close", "volume", "RSI", "market_cap_basic"],
  "rows": [
    {"ticker": "BIST:THYAO", "name": "THYAO", "close": 312.5,
     "volume": 18234567, "RSI": 27.4, "market_cap_basic": 431000000000}
  ]
}
```

### 5.3 `tvcli ta` — technical analysis layer

Wraps `tradingview-ta`.

| Command | Description |
|---------|-------------|
| `ta get SYMBOL --interval 1d [--screener auto]` | Full TA snapshot: summary recommendation, oscillators, moving averages, all indicator values. Intervals: `1m 5m 15m 30m 1h 2h 4h 1d 1W 1M`. Screener auto-derived from exchange (BIST→turkey, NASDAQ/NYSE→america, BINANCE→crypto). |
| `ta multi SYMBOL... --interval 1d` | Batch TA for multiple symbols (single upstream call via `get_multiple_analysis`). |
| `ta matrix SYMBOL --intervals 1h,4h,1d` | One symbol across multiple intervals — multi-timeframe confluence table. |

**JSON `data` payload for `ta get`:**

```json
{
  "symbol": "BIST:THYAO",
  "interval": "1d",
  "summary": {"recommendation": "BUY", "buy": 14, "neutral": 9, "sell": 3},
  "oscillators": {"recommendation": "NEUTRAL", "buy": 4, "neutral": 6, "sell": 1},
  "moving_averages": {"recommendation": "STRONG_BUY", "buy": 10, "neutral": 3, "sell": 2},
  "indicators": {"RSI": 56.2, "MACD.macd": 1.23, "MACD.signal": 0.98,
                 "close": 312.5, "...": "all values passed through"}
}
```

### 5.4 `tvcli ohlcv` — historical bars (authenticated WebSocket)

Implements a minimal TradingView WebSocket history client (the `tvDatafeed`
approach, reimplemented in-repo for maintainability — do **not** depend on the
abandoned PyPI package; vendor the protocol logic in `layers/ohlcv.py`).

Protocol summary: connect to `wss://data.tradingview.com/socket.io/websocket`,
authenticate with `sessionid` token, create chart session, request
`create_series` for symbol/resolution/bar-count, parse `timescale_update`
frames, close cleanly. Frames use the `~m~<len>~m~<json>` wire format.

| Command | Description |
|---------|-------------|
| `ohlcv get SYMBOL --interval 1d --bars 500 [--format json\|csv]` | Fetch historical OHLCV. Intervals: `1 3 5 15 30 45 1h 2h 3h 4h 1d 1W 1M` (map to TradingView resolution strings). |
| `ohlcv export SYMBOL --interval 1d --bars 5000 --out path.csv` | Write CSV/Parquet to disk (Parquet if `--out` ends with `.parquet`, requires pandas+pyarrow as optional extra). |

**JSON `data` payload:**

```json
{
  "symbol": "BIST:THYAO",
  "interval": "1d",
  "bars": [
    {"time": 1718064000, "open": 310.0, "high": 315.5,
     "low": 308.0, "close": 312.5, "volume": 18234567}
  ],
  "count": 500
}
```

Requires auth (exit 3 if absent). Unauthenticated mode may be attempted with
the `unauthorized_user_token` but must degrade gracefully.

### 5.5 `tvcli chart` — screenshots

| Command | Description |
|---------|-------------|
| `chart shot SYMBOL --interval 1d [--studies RSI,MACD] [--theme dark] [--width 1600 --height 900] --out chart.png` | Open `https://www.tradingview.com/chart/?symbol=...` in headless Chromium with stored `storage_state`, set interval via keyboard shortcut / URL param, add studies via UI if requested, wait for chart canvas stable, screenshot the chart container element. |

JSON output: `{"path": "/abs/path/chart.png", "symbol": "...", "interval": "...", "bytes": 182345}`.

Implementation notes:
- Use a single persistent `BrowserContext` from `storage_state.json`.
- Wait strategy: `networkidle` + canvas paint heuristic (poll canvas pixel hash twice 500 ms apart until stable, max 15 s).
- On CAPTCHA/login-wall detection → exit 3 with `CAPTCHA_DETECTED` or `SESSION_EXPIRED`.

### 5.6 `tvcli ui` — UI actions (Playwright, fragile layer)

All `ui` commands share: persistent context, stealth args
(`--disable-blink-features=AutomationControlled`), selector registry in one
module (`layers/ui.py` → `SELECTORS` dict) so DOM breakage is fixed in one
place. Every selector failure → exit 6 (`UPSTREAM_CHANGED`) with the failing
selector name in `error.hint`.

| Command | Description |
|---------|-------------|
| `ui alert create SYMBOL --condition "Crossing" --value 320 [--message TEXT] [--webhook URL]` | Create a price alert via the alert dialog. v1 supports price-crossing conditions only. |
| `ui alert list [--json]` | Scrape the alerts panel into structured rows. |
| `ui alert delete --id ID \| --all` | Delete alert(s) by scraped ID. |
| `ui watchlist add SYMBOL... --list NAME` | Add symbols to a named watchlist. |
| `ui watchlist export --list NAME` | Export watchlist symbols as JSON array. |
| `ui pine push --file script.pine --name NAME [--save-only/--add-to-chart]` | Open Pine editor, replace content with file content, save. |

### 5.7 `tvcli serve` — webhook receiver (official automation path)

| Command | Description |
|---------|-------------|
| `serve webhook --port 8787 --secret TOKEN [--sink telegram\|file\|stdout] [--telegram-token T --telegram-chat-id C]` | Start FastAPI server. Endpoint: `POST /hook/{secret}`. Validates secret path segment, parses TradingView alert JSON (free-form passthrough if not JSON), dispatches to sink, appends to `~/.local/share/tvcli/alerts.jsonl`. |

Health endpoint: `GET /healthz` → `{"ok": true, "uptime_seconds": N}`.
Designed to run under systemd; ship `contrib/tvcli-webhook.service` unit file.

### 5.8 `tvcli cache` — maintenance

| Command | Description |
|---------|-------------|
| `cache stats` | Entry count, size, hit-rate counters. |
| `cache clear [--older-than 1d]` | Purge cache entries. |

---

## 6. Caching

SQLite at `~/.local/share/tvcli/cache.db`, single table:

```sql
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,          -- sha256(command + canonical args)
  payload TEXT NOT NULL,         -- JSON data section
  created_at INTEGER NOT NULL,
  ttl_seconds INTEGER NOT NULL,
  hits INTEGER DEFAULT 0
);
```

Default TTLs (overridable in config.toml):

| Layer | TTL |
|-------|-----|
| `data screen` / `data quote` | 120 s |
| `ta *` | 300 s |
| `ohlcv get` (closed bars only) | 3600 s |
| `data fields` / `data search` | 86400 s |

`--no-cache` bypasses read but still writes. The JSON envelope reports
`cache.hit` truthfully.

---

## 7. Configuration

`~/.config/tvcli/config.toml` (created on first run with defaults):

```toml
[general]
default_market = "turkey"
default_interval = "1d"

[ratelimit]
requests_per_minute = 30
ohlcv_per_minute = 6

[cache]
enabled = true
# per-layer ttl overrides, seconds
ta_ttl = 300

[browser]
headless = true
timeout_ms = 30000
chromium_args = ["--disable-blink-features=AutomationControlled"]

[telegram]
token = ""        # or env TVCLI_TELEGRAM_TOKEN
chat_id = ""      # or env TVCLI_TELEGRAM_CHAT_ID
```

Environment overrides: any key as `TVCLI_<SECTION>_<KEY>` (e.g.
`TVCLI_RATELIMIT_REQUESTS_PER_MINUTE=10`). Env wins over file.

---

## 8. Rate limiting

Token-bucket per layer (in-process, persisted timestamps in cache.db so cron
invocations share the budget). Defaults: 30 req/min HTTP layers, 6 req/min
WebSocket sessions, 2 concurrent browser contexts max. When the bucket is
empty: block up to 10 s, then fail with exit 5 (`RATE_LIMITED`,
`retryable: true`).

---

## 9. Logging

- Human mode: Rich progress/status to **stderr** (stdout reserved for data).
- `--json` mode: stderr logging only at WARNING+ unless `-v`.
- Rotating file log: `~/.local/share/tvcli/tvcli.log` (1 MB × 3), includes
  upstream request metadata (never cookie values).
- Secrets (sessionid, telegram token) must never appear in logs or JSON output.

---

## 10. Testing requirements

- **Unit tests** (no network): output envelope, error mapping, DSL parser,
  cache TTL logic, rate limiter, config precedence, OHLCV frame parser against
  recorded fixtures, webhook endpoint via `TestClient`.
- **Integration tests**: marked `@pytest.mark.network`, excluded by default
  (`-m "not network"` in justfile `test` target). Run manually via `just test-live`.
- **Coverage gate:** ≥80 % on `src/tvcli` excluding `layers/ui.py` and
  `layers/chart.py` (browser layers tested via selector-registry unit tests +
  manual smoke).
- Mock HTTP with `respx`; mock WebSocket with fixture frame replay.

---

## 11. Claude Code skill contract (Phase Z deliverable)

`.claude/skills/tvcli/SKILL.md` must contain: one-line purpose, command quick
reference table, the JSON envelope schema, exit-code table, three worked
pipelines (screen→ta→chart; multi-timeframe matrix; alert webhook setup), and
the recovery playbook (what to do on exit 3/5/6/7). Keep under 300 lines.

---

## 12. Acceptance criteria (v1 = end of Phase 4)

1. `pipx install -e .` then `tvcli --help` lists all groups.
2. `tvcli data screen --market turkey --select name,close,RSI --where "RSI<30" --json` returns valid envelope in <5 s (cache cold).
3. `tvcli ta matrix BIST:THYAO --intervals 1h,4h,1d --json` returns three-interval matrix.
4. With imported cookie: `tvcli ohlcv get BIST:THYAO --interval 1d --bars 500 --json` returns exactly 500 bars.
5. `tvcli chart shot BIST:THYAO --interval 1d --out /tmp/t.png` produces a non-blank PNG ≥50 KB.
6. `tvcli serve webhook` + `curl -X POST .../hook/SECRET -d '{"x":1}'` appends to alerts.jsonl and (if configured) sends Telegram message.
7. `just lint && just test` exits 0; coverage ≥80 %.
8. All failures produce the JSON error envelope with actionable `hint` when `--json` is set.
