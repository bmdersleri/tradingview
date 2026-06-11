# Repository Guidelines

## Project Structure & Module Organization

This repository currently contains planning documents for `tvcli`, a Python 3.11+ TradingView CLI toolkit. Treat `SPEC.md` as authoritative and `IMPLEMENTATION_PLAN.md` as the phase plan. Intended layout:

- `src/tvcli/`: package, Typer entry point, config, output, errors, cache, rate limiting, layers, commands, and webhook server.
- `src/tvcli/layers/`: isolated upstream integrations such as screener, TA, OHLCV, chart, and UI automation.
- `src/tvcli/commands/`: CLI command groups. Keep command modules thin; put reusable logic in layers or core modules.
- `tests/unit/`, `tests/integration/`, `tests/fixtures/`: offline tests, opt-in live tests, and sanitized fixture data.
- `.claude/skills/tvcli/SKILL.md`: agent-facing command reference planned for the packaging phase.

## Build, Test, and Development Commands

Use the existing `justfile` targets:

- `just install`: install editable package with dev, browser, and serve extras, then install Chromium.
- `just lint`: run `ruff check`, `ruff format --check`, and strict `mypy` on `src`.
- `just fmt`: format and auto-fix with Ruff.
- `just test`: run offline pytest suite with coverage, excluding `network` tests.
- `just test-live`: run opt-in integration tests marked `network`.

Before adding CLI tools, read `/home/haytek/ai-tooling/AI_TOOLING.md` and prefer existing project-native tooling.

## Coding Style & Naming Conventions

Use English for code, comments, CLI output, and docs. Follow Ruff formatting and lint rules. Keep stdout machine-readable; logs and progress belong on stderr. Every CLI command must support stable `--json` output using `SPEC.md`. Error classes must include an actionable `hint`, retryability, and mapped exit code.

## Testing Guidelines

Use `pytest`, `pytest-asyncio`, `respx`, and fixtures for offline coverage. Network or browser tests must be marked, for example `@pytest.mark.network` or `@pytest.mark.browser`, and should have fixture-backed unit coverage. Keep fixture secrets sanitized.

## Commit & Pull Request Guidelines

The commit history follows Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, or `chore:`, with scopes where helpful, such as `feat(ta): add matrix command`.

Pull requests should summarize the behavioral change, list validation commands run, link related issues or phases, and include screenshots only for chart/UI-visible changes. Do not merge with `just lint` or `just test` failing.

## Security & Configuration Tips

Never commit TradingView cookies, session tokens, API keys, logs containing secrets, or unsanitized fixtures. Store user config under XDG paths as specified, respect conservative rate limits, and avoid programmatic CAPTCHA bypasses.
