# tvcli

`tvcli` is a TradingView CLI toolkit planned for phased implementation.

## Status

Phase Z hardening complete. The authoritative design lives in `SPEC.md` and
`IMPLEMENTATION_PLAN.md`.

## Usage

The intended install path is:

```bash
just install
```

Quick-start examples:

```bash
tvcli version --json
tvcli doctor --json
tvcli data screen --market turkey --select name,close,volume --where "RSI<30" --limit 20 --json
tvcli data fields --market turkey --search rsi --json
tvcli ta get BIST:THYAO --interval 1d --json
tvcli ta matrix BIST:THYAO --intervals 1h,4h,1d --json
tvcli serve webhook --port 8787 --secret TOKEN --sink stdout
tvcli mcp serve
```

The `data`, `ta`, `auth`, `ohlcv`, `chart`, `ui`, and `serve` surfaces are wired
into the CLI; browser-backed commands require a saved TradingView session. Use
`--retries N --backoff SECONDS` for retryable upstream failures.

## Architecture

```mermaid
flowchart LR
    CLI[tvcli CLI] --> DATA[data / ta / ohlcv]
    CLI --> BROWSER[chart / ui / auth]
    CLI --> SERVE[serve webhook]
    CLI --> DOCTOR[doctor]
    CLI --> MCP[mcp serve]
    SERVE --> TG[Telegram / JSONL sinks]
    DATA --> TV[TradingView HTTP / WebSocket layers]
    BROWSER --> PW[Playwright]
```

## Cron Examples

```cron
0 * * * * /usr/bin/tvcli ta matrix BIST:THYAO --intervals 1h,4h,1d --json >> /var/log/tvcli-ta.jsonl
15 9 * * 1-5 /usr/bin/tvcli data screen --market turkey --select name,close,RSI --where "RSI<30" --json >> /var/log/tvcli-screen.jsonl
# Keep the local BIST free-float archive current (reports lag one business day):
30 19 * * 1-5 /usr/bin/tvcli data float-sync --latest --json >> /var/log/tvcli-float.jsonl
```

## Free-float archive (BIST fiili dolaşım)

`tvcli` keeps a persistent local archive of VAP/MKK free-float ratios at
`~/.local/share/tvcli/archive.sqlite3`. Reads are local-first: `data float`,
`chart signal`, and `chart analyze --auto` use the archive and only hit VAP on a
miss (writing the result through).

One-time historical backfill (resumable, gentle inter-request throttle — safe to
interrupt and re-run with `--resume`):

```bash
nohup tvcli data float-sync --since 2024-01-01 --resume --rate-seconds 20 --json \
  >> backfill.log 2>&1 &
tvcli data float-stats --json   # watch coverage grow
```

Per-symbol analytics from the archive (no network):

```bash
tvcli data float-report THYAO --json     # trend, deltas, risk events
tvcli data float-history THYAO --json
tvcli data float-events --severity high --json
```

## Claude Code

The agent-facing command contract lives in `.claude/skills/tvcli/SKILL.md`.
Keep it aligned with the JSON envelope, exit codes, and recovery behavior when
the CLI changes.

## Disclaimer

This project relies on unofficial TradingView endpoints for some features. Use it for personal workflows with conservative rate limits. Do not use it to bypass CAPTCHA, anti-bot controls, or TradingView terms.
