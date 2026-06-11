# Phase Z Hardening and MCP Wrapper Design

Scope: finish the Phase Z deliverables from `IMPLEMENTATION_PLAN.md` and keep
the implementation aligned with the existing HTTP-first design.

Selected approach:

1. Add a `tvcli doctor` command that reports a structured pass/fail envelope for
   dependencies, Chromium, cache writability, session validity, and upstream
   reachability.
2. Add global retry flags on the root CLI and honor them only for retryable
   `TvcliError` subclasses.
3. Add `just audit` as a narrow secret-leak gate over fixtures and docs.
4. Ship `.claude/skills/tvcli/SKILL.md` as the agent-facing contract.
5. Add an optional MCP wrapper that reuses the same core query functions and
   registers tools for screener, TA, OHLCV, and chart flows.

Trade-off: the MCP wrapper is lazy-imported so the base install stays usable even
when the optional MCP dependency is absent. The wrapper becomes active when the
extra is installed.
