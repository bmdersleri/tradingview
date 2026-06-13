"""Render free-float dashboard PNGs (single-symbol deep-dive or market overview).

Follows the matplotlib-Agg pattern established in ``analyze.py``.  All data
reads are local-first against the archive; no network traffic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..errors import NotFoundError, TvcliError, UsageError
from . import freefloat_archive

_THEME_BG = {"dark": "#131722", "light": "#ffffff"}
_THEME_FG = {"dark": "#d1d4dc", "light": "#131722"}
_GRID_COLOR = {"dark": "#2a2e39", "light": "#e0e0e0"}
_BULL = "#26a69a"
_BEAR = "#ef5350"
_WARN_YELLOW = "#ffaa00"
_LOW_FLOAT_LINE = 20.0
_SEVERE_LINE = 10.0


@dataclass(frozen=True, slots=True)
class DashboardRequest:
    out: Path
    symbol: str | None = None
    market: bool = False
    report_date: date | None = None
    limit: int = 120
    top: int = 15
    width: int = 1600
    height: int = 1000
    theme: str = "dark"


def _load_pyplot() -> Any:
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover
        raise TvcliError(
            "Dashboard requires matplotlib.",
            hint="Reinstall tvcli (`just install`) to pull the matplotlib dependency.",
        ) from exc
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _render_deep_dive(
    request: DashboardRequest,
    store: freefloat_archive.ArchiveStore,
    plt: Any,
) -> dict[str, Any]:
    assert request.symbol is not None
    report = store.build_symbol_report(request.symbol, limit=request.limit)

    bg = _THEME_BG.get(request.theme, _THEME_BG["dark"])
    fg = _THEME_FG.get(request.theme, _THEME_FG["dark"])
    grid = _GRID_COLOR.get(request.theme, _GRID_COLOR["dark"])

    history = report["recent_changes"]  # newest-first list from symbol_history
    rows = list(reversed(history))  # oldest-first for plotting
    dates = [r["report_date"] for r in rows]
    ratios = [float(r["ratio"]) for r in rows]
    shares = [float(r["float_shares"]) for r in rows]
    deltas = [
        float(r["ratio_delta"]) if r["ratio_delta"] is not None else 0.0 for r in rows
    ]
    n = len(dates)
    xs = list(range(n))

    # Tick positions
    max_ticks = 8
    step = max(1, n // max_ticks)
    tick_pos = list(range(0, n, step))
    tick_lbl = [str(dates[i]) for i in tick_pos]

    code = report["identity"]["code"]
    name = report["identity"]["name"]
    latest_ratio = report["latest"]["ratio"]
    pct = report.get("percentile") or {}
    rank = pct.get("rank")
    total = pct.get("total")
    risk = report["risk"]
    risk_tag = (
        " ⚠ SEVERE"
        if risk["severe_low_float"]
        else (" ⚠ LOW" if risk["low_float"] else "")
    )
    pct_str = f"  pct: lowest {rank}/{total}" if rank and total else ""
    title = f"{code} — {name}  float {latest_ratio:.2f}%{risk_tag}{pct_str}"

    fig, axes = plt.subplots(
        3,
        1,
        figsize=(request.width / 100, request.height / 100),
        facecolor=bg,
        gridspec_kw={"height_ratios": [3, 1, 1], "hspace": 0.05},
    )

    fig.suptitle(title, color=fg, fontsize=11, x=0.01, ha="left")

    # ── Panel 1: ratio history ───────────────────────────────────────────────
    ax1 = axes[0]
    ax1.set_facecolor(bg)
    ax1.plot(xs, ratios, color=_BULL if not risk["low_float"] else _BEAR, linewidth=1.5)
    ax1.axhline(
        _LOW_FLOAT_LINE, color=_WARN_YELLOW, linewidth=0.8, linestyle="--", alpha=0.7
    )
    ax1.axhline(_SEVERE_LINE, color=_BEAR, linewidth=0.8, linestyle="--", alpha=0.7)
    if risk["low_float"]:
        ax1.axhspan(0, _LOW_FLOAT_LINE, color=_BEAR, alpha=0.06)

    # Event markers
    event_dates = {
        e["report_date"] for e in report["events"] if e["severity"] == "high"
    }
    for i, d in enumerate(dates):
        if d in event_dates:
            ax1.axvline(i, color=_WARN_YELLOW, linewidth=0.7, alpha=0.5)
            ax1.plot(i, ratios[i], marker="D", color=_WARN_YELLOW, markersize=4)

    ax1.set_ylabel("ratio %", color=fg, fontsize=8)
    ax1.tick_params(colors=fg, labelsize=7, bottom=False, labelbottom=False)
    ax1.set_xticks([])
    ax1.yaxis.set_tick_params(labelsize=7)
    for spine in ax1.spines.values():
        spine.set_edgecolor(grid)
    ax1.grid(True, color=grid, linewidth=0.5)

    # ── Panel 2: float shares ────────────────────────────────────────────────
    ax2 = axes[1]
    ax2.set_facecolor(bg)
    ax2.bar(xs, shares, color=_BULL, alpha=0.6, width=0.8)
    ax2.set_ylabel("shares", color=fg, fontsize=8)
    ax2.tick_params(colors=fg, labelsize=7, bottom=False, labelbottom=False)
    ax2.set_xticks([])
    for spine in ax2.spines.values():
        spine.set_edgecolor(grid)
    ax2.grid(True, color=grid, linewidth=0.5, axis="y")

    # ── Panel 3: ratio delta ─────────────────────────────────────────────────
    ax3 = axes[2]
    ax3.set_facecolor(bg)
    colors_d = [_BULL if v >= 0 else _BEAR for v in deltas]
    ax3.bar(xs, deltas, color=colors_d, alpha=0.8, width=0.8)
    ax3.axhline(0, color=fg, linewidth=0.5)
    ax3.axhline(5.0, color=_WARN_YELLOW, linewidth=0.6, linestyle="--", alpha=0.6)
    ax3.axhline(-5.0, color=_WARN_YELLOW, linewidth=0.6, linestyle="--", alpha=0.6)
    ax3.set_ylabel("Δratio", color=fg, fontsize=8)
    ax3.set_xticks(tick_pos)
    ax3.set_xticklabels(tick_lbl, rotation=30, ha="right", color=fg, fontsize=6)
    ax3.tick_params(colors=fg, labelsize=6)
    for spine in ax3.spines.values():
        spine.set_edgecolor(grid)
    ax3.grid(True, color=grid, linewidth=0.5, axis="y")

    request.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(request.out), dpi=100, facecolor=bg, bbox_inches="tight")
    plt.close(fig)

    return {
        "mode": "symbol",
        "symbol": code,
        "name": name,
        "report_date": report["latest"]["report_date"],
        "latest_ratio": latest_ratio,
        "risk": risk,
        "percentile": pct,
        "path": str(request.out.resolve()),
        "bytes": request.out.stat().st_size,
    }


def _render_market_overview(
    request: DashboardRequest,
    store: freefloat_archive.ArchiveStore,
    plt: Any,
) -> dict[str, Any]:
    bg = _THEME_BG.get(request.theme, _THEME_BG["dark"])
    fg = _THEME_FG.get(request.theme, _THEME_FG["dark"])
    grid = _GRID_COLOR.get(request.theme, _GRID_COLOR["dark"])

    # Get latest report date from stats
    stats = store.archive_stats()
    latest_date = stats.get("last_report_date")
    if not latest_date:
        raise NotFoundError(
            "No archived free-float reports found.",
            hint="Run `tvcli data float-sync --latest` first.",
        )

    # Fetch all snapshots on the latest date
    with store._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT code, name, ratio FROM freefloat_snapshots"
            " WHERE report_date = ? ORDER BY ratio ASC",
            (latest_date,),
        ).fetchall()
        # High-severity events on latest date
        event_rows = conn.execute(
            """
            SELECT event_type, COUNT(*) AS cnt FROM freefloat_events
            WHERE report_date = ? AND severity = 'high'
            GROUP BY event_type ORDER BY cnt DESC
            """,
            (latest_date,),
        ).fetchall()

    all_ratios = [float(r["ratio"]) for r in rows]
    all_codes = [str(r["code"]) for r in rows]
    n_symbols = len(all_ratios)

    if n_symbols == 0:
        raise NotFoundError(
            f"No snapshot data for {latest_date}.",
            hint="Run `tvcli data float-sync` first.",
        )

    import statistics

    median_ratio = statistics.median(all_ratios)

    # Lowest-float leaderboard
    top_n = min(request.top, n_symbols)
    top_codes = all_codes[:top_n]
    top_ratios = all_ratios[:top_n]

    event_types = [str(r["event_type"]) for r in event_rows]
    event_counts = [int(r["cnt"]) for r in event_rows]

    # Layout: 3 panels (histogram, leaderboard, events strip)
    n_event_rows = max(1, len(event_types))
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(request.width / 100, request.height / 100),
        facecolor=bg,
        gridspec_kw={"height_ratios": [3, 3, max(1, n_event_rows)], "hspace": 0.35},
    )
    title = f"BIST Free-Float Overview — {latest_date} ({n_symbols} symbols)"
    fig.suptitle(title, color=fg, fontsize=11, x=0.01, ha="left")

    # ── Panel 1: ratio distribution histogram ───────────────────────────────
    ax1 = axes[0]
    ax1.set_facecolor(bg)
    ax1.hist(all_ratios, bins=40, color=_BULL, alpha=0.7, edgecolor=bg)
    ax1.axvline(median_ratio, color=_WARN_YELLOW, linewidth=1.2, linestyle="--")
    ax1.text(
        median_ratio + 0.5,
        ax1.get_ylim()[1] * 0.9 if ax1.get_ylim()[1] > 0 else 1,
        f"median {median_ratio:.1f}%",
        color=_WARN_YELLOW,
        fontsize=7,
    )
    ax1.set_xlabel("free-float ratio %", color=fg, fontsize=8)
    ax1.set_ylabel("symbols", color=fg, fontsize=8)
    ax1.tick_params(colors=fg, labelsize=7)
    for spine in ax1.spines.values():
        spine.set_edgecolor(grid)
    ax1.grid(True, color=grid, linewidth=0.5, axis="y")

    # ── Panel 2: lowest-float leaderboard (horizontal bars) ─────────────────
    ax2 = axes[1]
    ax2.set_facecolor(bg)
    ys = list(range(top_n))
    bar_colors = [
        _BEAR if r < _SEVERE_LINE else (_WARN_YELLOW if r < _LOW_FLOAT_LINE else _BULL)
        for r in top_ratios
    ]
    ax2.barh(ys, top_ratios, color=bar_colors, alpha=0.8)
    ax2.set_yticks(ys)
    ax2.set_yticklabels(top_codes, fontsize=7, color=fg)
    ax2.invert_yaxis()
    ax2.set_xlabel("ratio %", color=fg, fontsize=8)
    ax2.set_title(
        f"Lowest float (top {top_n})", color=fg, fontsize=9, loc="left", pad=4
    )
    ax2.tick_params(colors=fg, labelsize=7)
    for spine in ax2.spines.values():
        spine.set_edgecolor(grid)
    ax2.grid(True, color=grid, linewidth=0.5, axis="x")

    # ── Panel 3: high-severity event counts ─────────────────────────────────
    ax3 = axes[2]
    ax3.set_facecolor(bg)
    if event_types:
        eys = list(range(len(event_types)))
        ax3.barh(eys, event_counts, color=_WARN_YELLOW, alpha=0.8)
        ax3.set_yticks(eys)
        ax3.set_yticklabels(event_types, fontsize=7, color=fg)
        ax3.invert_yaxis()
    else:
        ax3.text(
            0.5,
            0.5,
            "no high-severity events",
            ha="center",
            va="center",
            color=fg,
            fontsize=9,
        )
        ax3.set_xticks([])
        ax3.set_yticks([])
    ax3.set_title(
        "High-severity events (latest report)", color=fg, fontsize=9, loc="left", pad=4
    )
    ax3.tick_params(colors=fg, labelsize=7)
    for spine in ax3.spines.values():
        spine.set_edgecolor(grid)
    ax3.grid(True, color=grid, linewidth=0.5, axis="x")

    request.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(request.out), dpi=100, facecolor=bg, bbox_inches="tight")
    plt.close(fig)

    return {
        "mode": "market",
        "symbol": None,
        "report_date": latest_date,
        "n_symbols": n_symbols,
        "median_ratio": round(median_ratio, 2),
        "leaderboard": [
            {"code": c, "ratio": r} for c, r in zip(top_codes, top_ratios, strict=True)
        ],
        "event_summary": [
            {"event_type": t, "count": c}
            for t, c in zip(event_types, event_counts, strict=True)
        ],
        "path": str(request.out.resolve()),
        "bytes": request.out.stat().st_size,
    }


def run_dashboard(
    request: DashboardRequest,
    store: freefloat_archive.ArchiveStore | None = None,
) -> dict[str, Any]:
    """Render a free-float dashboard PNG. Returns the result payload."""
    if request.symbol and request.market:
        raise UsageError("Provide either a SYMBOL or --market, not both.")
    if not request.symbol and not request.market:
        raise UsageError("Provide a SYMBOL for deep-dive or --market for overview.")

    plt = _load_pyplot()
    if store is None:
        store = freefloat_archive.ArchiveStore()

    if request.symbol:
        return _render_deep_dive(request, store, plt)
    return _render_market_overview(request, store, plt)
