# tvcli

`tvcli` is a TradingView CLI for agents and operators. Use `--json` for machine
consumption and keep human output on stderr/stdout separation intact.

## Quick Reference

| Command | Purpose | Notes |
| --- | --- | --- |
| `tvcli version` | Print the package version | Supports `--json` |
| `tvcli doctor` | Check deps, browser, cache, session, upstream | Returns pass/fail JSON |
| `tvcli data screen|fields|search|quote` | Screener queries | Use `--where` for the DSL |
| `tvcli ta get|multi|matrix` | TradingView TA summaries | `matrix` is multi-interval |
| `tvcli ohlcv get|export` | Historical bars | `export` writes CSV or Parquet |
| `tvcli chart shot` | Save chart PNG | Needs a saved TradingView session |
| `tvcli ui alert|watchlist|pine` | Browser UI workflows | Fragile; prefer HTTP layers when possible |
| `tvcli auth import-cookie|status|login|logout` | Session management | `status` validates the stored session |
| `tvcli serve webhook` | FastAPI webhook receiver | For Pine alerts and Telegram dispatch |
| `tvcli mcp serve` | MCP wrapper over core tools | Optional; requires the `mcp` extra |
| `tvcli cache stats|clear` | Inspect or clear the cache | Helpful for debugging stale responses |

Global flags:

- `--json`
- `--no-cache`
- `--quiet` / `-q`
- `--retries N`
- `--backoff SECONDS`
- `--config PATH`

## JSON Envelope

Every machine-readable response uses this shape:

```json
{
  "schema_version": 1,
  "ok": true,
  "command": "ta.get",
  "generated_at": "2026-06-11T10:30:00+00:00",
  "cache": { "hit": false, "ttl_seconds": 300 },
  "data": {},
  "error": null
}
```

Failures keep `schema_version` the same and set:

```json
{
  "ok": false,
  "error": {
    "code": "NETWORK",
    "message": "Unable to reach TradingView.",
    "retryable": true,
    "hint": "Check connectivity and retry."
  }
}
```

## Exit Codes

| Code | Name | Meaning |
| --- | --- | --- |
| `0` | `OK` | Success |
| `1` | `GENERIC` | Unexpected error |
| `2` | `USAGE` | Bad arguments |
| `3` | `AUTH_REQUIRED` | Missing or expired session |
| `4` | `NOT_FOUND` | Symbol or resource not found |
| `5` | `RATE_LIMITED` | Retryable upstream throttling |
| `6` | `UPSTREAM_CHANGED` | Selector or contract drift |
| `7` | `NETWORK` | Retryable connectivity issue |
| `8` | `BROWSER` | Playwright/browser failure |

Retry only `retryable: true` errors. Do not retry `6`.

## Worked Pipelines

### 1. screen -> ta -> chart

```bash
tvcli data screen --market turkey --select name,close,RSI --where "RSI<30" --json
tvcli ta get BIST:THYAO --interval 1d --json
tvcli chart shot BIST:THYAO --interval 1d --out /tmp/thy.png
```

Use this when you need a screened symbol, a fast TA snapshot, and a chart image
from the same session.

### 2. Multi-timeframe matrix

```bash
tvcli ta matrix BIST:THYAO --intervals 1h,4h,1d --json
```

Use this to compare one symbol across multiple intervals without changing
command shape.

### 3. Alert webhook setup

```bash
tvcli serve webhook --host 0.0.0.0 --port 8787 --secret TOKEN --sink telegram \
  --telegram-token "$TELEGRAM_BOT_TOKEN" --telegram-chat-id "$TELEGRAM_CHAT_ID"
```

Point TradingView alerts at:

`https://HOST:8787/hook/TOKEN`

## Recovery Playbook

- Exit `3`: import or refresh the TradingView session, then rerun.
- Exit `5`: back off and retry with `--retries` and a larger `--backoff`.
- Exit `6`: do not retry; report the selector or upstream contract that drifted.
- Exit `7`: check connectivity, proxy, and upstream reachability, then retry.

## Notes

- Keep stdout reserved for data and JSON envelopes.
- Treat `docs/SMOKE.md` as the browser-flow checklist.
- The MCP wrapper is optional; install the `mcp` extra if you need it.
