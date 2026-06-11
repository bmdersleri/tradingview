# tvcli — Implementation Plan

**Companion document:** `SPEC.md` (authoritative for all contracts; if this plan
and the spec conflict, the spec wins).
**Execution mode:** Autonomous. No approval gates between tasks within a phase.
Stop and report only at phase boundaries or on a red gate you cannot fix in 3
attempts.

---

## 0. Iron Laws (read before writing any code)

1. **Green gate:** `just lint && just test` must pass before every commit. Never
   commit on red.
2. **Conventional commits:** `feat:`, `fix:`, `test:`, `docs:`, `refactor:`,
   `chore:`, scoped where useful (`feat(ta): ...`). One logical change per commit.
3. **Surgical edits:** modify the minimum surface area. No drive-by refactors.
4. **JSON envelope is sacred:** never change the envelope shape (SPEC §4.2)
   without bumping `schema_version` and updating all tests + SKILL.md.
5. **stdout is for data, stderr is for humans.** Never print logs to stdout.
6. **No secrets in logs, commits, or test fixtures.** Fixtures must use
   sanitized/dummy session values.
7. **Vendored protocol code** (OHLCV WebSocket) lives in `layers/ohlcv.py` with
   a header comment documenting the wire format — no external tvDatafeed dep.
8. **Browser selectors only in `SELECTORS` registries** (`layers/ui.py`,
   `layers/chart.py`). Commands never contain raw selectors.
9. **Network tests are opt-in:** every test touching the internet gets
   `@pytest.mark.network` and a fixture-based unit twin.
10. **Each phase ends with:** green gates → version bump in `__init__.py`
    (0.1.0 → 0.2.0 → ...) → tag `v0.N.0` → short phase report (what shipped,
    what was deferred, known risks).

---

## Phase 0 — Scaffold & Foundations (target: 0.1.0)

### Tasks

- **P0.1** Initialize repo structure exactly as SPEC §2. `pyproject.toml` with
  PEP 621 metadata, hatchling, console script `tvcli = "tvcli.cli:app"`,
  dependency groups: core, `[browser]` extra (playwright), `[serve]` extra
  (fastapi, uvicorn), `[parquet]` extra (pandas, pyarrow), `[dev]` (pytest,
  pytest-asyncio, respx, ruff, mypy, pytest-cov).
- **P0.2** `justfile` targets:
  ```
  install:    pip install -e ".[dev,browser,serve]" && playwright install chromium
  lint:       ruff check src tests && ruff format --check src tests && mypy src
  fmt:        ruff format src tests && ruff check --fix src tests
  test:       pytest -m "not network" --cov=tvcli --cov-fail-under=80
  test-live:  pytest -m network -v
  ```
- **P0.3** `config.py`: XDG paths, TOML loader, env override resolution
  (`TVCLI_SECTION_KEY`), default config materialization on first run.
- **P0.4** `errors.py`: `TvcliError` hierarchy → exit-code mapping per SPEC
  §4.3; `error.code`, `retryable`, `hint` fields mandatory on every subclass.
- **P0.5** `output.py`: envelope builder (SPEC §4.2), Rich table renderer,
  single `emit(result, json_mode)` entry point used by every command.
- **P0.6** `cache.py`: SQLite TTL cache per SPEC §6 (get/set/purge/stats,
  hit counters).
- **P0.7** `ratelimit.py`: token bucket with SQLite-persisted timestamps
  (shared budget across processes).
- **P0.8** `cli.py`: Typer app, global flags (`--json`, `--no-cache`, `-q`,
  `--config`), group registration stubs, `tvcli version`, `tvcli cache stats|clear`.
- **P0.9** Unit tests for P0.3–P0.7. CI-ready: everything offline.

### Acceptance
`tvcli --help`, `tvcli version --json`, `tvcli cache stats --json` work;
gates green; coverage ≥80 % of implemented modules.

### Commits (indicative)
`chore: scaffold project structure and tooling` · `feat(core): config loading
with env overrides` · `feat(core): error hierarchy and exit codes` ·
`feat(core): json envelope and rich output` · `feat(core): sqlite ttl cache` ·
`feat(core): token-bucket rate limiter` · `test(core): foundation unit suite`

---

## Phase 1 — Screener + TA (target: 0.2.0, first useful release)

### Tasks

- **P1.1** Add deps `tradingview-screener`, `tradingview-ta`, `httpx`.
- **P1.2** `layers/screener.py`: thin adapter over tradingview-screener —
  build query from (market, select, where-DSL, order, limit); normalize rows
  to `{"ticker": "EXCHANGE:SYMBOL", ...fields}`.
- **P1.3** Where-DSL parser (SPEC §5.2): tokenize `field OP value` clauses,
  map to library Column expressions, rich parse errors (exit 2, offending
  clause in hint). Pure function, exhaustively unit-tested.
- **P1.4** `commands/data.py`: `screen`, `fields`, `search`, `quote` per SPEC
  §5.2, wired through cache + ratelimit + envelope.
- **P1.5** `layers/ta.py`: adapter over tradingview-ta; screener auto-derivation
  map (BIST→turkey, NASDAQ/NYSE/AMEX→america, BINANCE/BYBIT/KUCOIN→crypto,
  FX_IDC/OANDA→forex; unknown → exit 4 with hint to pass `--screener`).
- **P1.6** `commands/ta.py`: `get`, `multi` (single upstream batch call),
  `matrix` per SPEC §5.3.
- **P1.7** Fixtures: record one real screener response and one TA response
  (sanitized) into `tests/fixtures/`; unit tests run against fixtures via
  respx / monkeypatched library calls. Live twins under `@network`.
- **P1.8** README quick-start section with 5 copy-paste examples.

### Acceptance
SPEC §12 items 2 and 3 pass live (`just test-live`); offline suite green.

---

## Phase 2 — Auth + OHLCV + Chart screenshots (target: 0.3.0)

### Tasks

- **P2.1** `auth/session.py`: session store (0600 perms), load/save/validate,
  `SessionRequired` error (exit 3).
- **P2.2** `commands/auth.py`: `import-cookie` (primary headless path),
  `status` (validated via an authenticated scanner call returning username),
  `logout`.
- **P2.3** `auth/login.py`: Playwright login flow (`--headed` needs DISPLAY;
  document `xvfb-run tvcli auth login --headed` in README). Detect success by
  cookie presence; extract sessionid/sessionid_sign; save storage_state. CAPTCHA
  → exit 3 `CAPTCHA_DETECTED`, hint = use `auth import-cookie`.
- **P2.4** `layers/ohlcv.py`: vendored WebSocket history client per SPEC §5.4.
  Wire format `~m~<len>~m~<json>`; flow: connect → `set_auth_token` →
  `chart_create_session` → `resolve_symbol` → `create_series` → collect
  `timescale_update` → `series_completed` → close. Resolution mapping table.
  Frame parser as pure functions (fixture-testable).
- **P2.5** `commands/ohlcv.py`: `get` (json/csv to stdout), `export`
  (csv/parquet via optional extra; clear error if pandas missing).
- **P2.6** `layers/chart.py`: persistent context from storage_state, navigation,
  canvas-stability wait (pixel-hash polling per SPEC §5.5), element screenshot,
  `SELECTORS` registry, login-wall/captcha detection.
- **P2.7** `commands/chart.py`: `shot` per SPEC §5.5.
- **P2.8** Fixtures: recorded WebSocket frame sequence → parser unit tests.
  `@network`+`@browser` markers for live smoke tests.

### Acceptance
SPEC §12 items 4 and 5 pass live with an imported cookie; offline gates green
(browser modules exempt from coverage per SPEC §10).

---

## Phase 3 — UI actions (target: 0.4.0)

### Tasks

- **P3.1** `layers/ui.py`: shared browser session helper (reuse chart layer
  context factory), `SELECTORS` registry with semantic names
  (`ALERT_DIALOG_OPEN_BTN`, `ALERT_CONDITION_SELECT`, ...), every interaction
  wrapped so a selector timeout raises `UpstreamChanged` (exit 6) naming the
  selector key.
- **P3.2** `commands/ui.py alert create|list|delete` per SPEC §5.6 (v1: price
  crossing conditions only; document limitation).
- **P3.3** `watchlist add|export`.
- **P3.4** `pine push` (editor open → select-all → paste file content → save;
  verify via toast/title).
- **P3.5** Selector-registry unit tests (registry completeness, error mapping);
  manual smoke checklist in `docs/SMOKE.md` for the browser flows.

### Acceptance
Manual smoke checklist passes on the target server (xvfb headless); selector
failures produce exit 6 with named selector; gates green.

---

## Phase 4 — Webhook server + Telegram (target: 0.5.0 = v1 candidate)

### Tasks

- **P4.1** `webhook/app.py`: FastAPI app per SPEC §5.7 — `POST /hook/{secret}`
  (constant-time secret compare), `GET /healthz`, JSON-or-raw body handling,
  append-only `alerts.jsonl` writer.
- **P4.2** `webhook/sinks.py`: `stdout`, `file`, `telegram` (httpx POST to Bot
  API, formatted message with symbol/price/message fields when parseable).
- **P4.3** `commands/serve.py`: uvicorn runner with flags per SPEC §5.7.
- **P4.4** `contrib/tvcli-webhook.service` systemd unit + README section
  (install, enable, TradingView alert webhook URL format).
- **P4.5** Tests: FastAPI `TestClient` — auth rejection, JSON passthrough,
  raw-body passthrough, jsonl append, telegram sink with respx mock.
- **P4.6** Run full SPEC §12 acceptance list; fix anything red.

### Acceptance
All SPEC §12 criteria pass. Tag `v0.5.0`.

---

## Phase Z — Hardening & Agent Packaging (target: 1.0.0)

### Tasks

- **PZ.1** Write `.claude/skills/tvcli/SKILL.md` per SPEC §11 (<300 lines):
  quick-reference table, envelope schema, exit codes, three worked pipelines,
  recovery playbook (exit 3 → import-cookie; exit 5/7 → backoff retry;
  exit 6 → report selector name, do not retry).
- **PZ.2** Retry ergonomics: `--retries N --backoff SECONDS` global flags
  honoring `retryable` errors only.
- **PZ.3** `tvcli doctor`: environment self-check (deps, chromium installed,
  session validity, cache writable, upstream reachability) with pass/fail JSON.
- **PZ.4** Log rotation verification, secret-leak audit (grep gates in a
  `just audit` target: no `sessionid=` patterns in logs/fixtures).
- **PZ.5** README finalization: architecture diagram (Mermaid), ToS disclaimer,
  cron examples, Claude Code usage section.
- **PZ.6** Optional (time-permitting, do not block 1.0): FastMCP wrapper
  exposing `data screen`, `ta get/matrix`, `ohlcv get`, `chart shot` as MCP
  tools reusing the same layer functions.

### Acceptance
`tvcli doctor --json` all-green on the target server; SKILL.md loads in Claude
Code and a fresh agent session can execute the three worked pipelines
unassisted. Tag `v1.0.0`.

---

## Risk register & contingencies

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Scanner/TA endpoint contract change | Medium | Adapters isolated in `layers/`; integration tests detect drift; exit 6 semantics |
| WebSocket protocol change | Medium | Vendored parser with fixtures; failure → exit 6, fall back to `ta`-layer close prices documented in SKILL.md |
| Cloudflare/CAPTCHA on browser layers | Medium-High | `import-cookie` as primary auth; stealth args; persistent context; degrade to HTTP layers |
| TradingView account flag/ban | Low (with limits) | Conservative ratelimits (SPEC §8), cache-first design, personal-use scope |
| tradingview-ta abandonment | Low | Indicator values also available via scanner fields → `data` layer can substitute; note in code comments |

## Definition of Done (project)

- All SPEC §12 acceptance criteria green on the production server.
- `just lint && just test` green, coverage ≥80 %.
- SKILL.md verified with a cold Claude Code session.
- Git history is clean conventional commits; tags v0.1.0 … v1.0.0 present.
