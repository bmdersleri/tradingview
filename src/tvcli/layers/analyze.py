"""Fetch OHLCV history, compute indicators, and render a chart PNG.

Reuses :func:`tvcli.layers.ohlcv.fetch_history` for data and
:mod:`tvcli.layers.indicators` for the math. Rendering uses matplotlib with the
headless ``Agg`` backend so it works on a server with no display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..errors import TvcliError
from . import indicators as ind
from . import ohlcv

_THEME_BG = {"dark": "#131722", "light": "#ffffff"}
_THEME_FG = {"dark": "#d1d4dc", "light": "#131722"}
_OVERLAY_COLORS = ("#ffaa00", "#26a69a", "#ab47bc", "#ef5350", "#42a5f5")


@dataclass(frozen=True, slots=True)
class AnalyzeRequest:
    symbol: str
    interval: str
    out: Path
    bars: int = 500
    indicators: tuple[str, ...] = ()
    width: int = 1600
    height: int = 900
    theme: str = "dark"


def _load_pyplot() -> Any:
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover - matplotlib is a core dep
        raise TvcliError(
            "Charting requires matplotlib.",
            hint="Reinstall tvcli (`just install`) to pull the matplotlib dependency.",
        ) from exc
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def fetch_bars_query(request: AnalyzeRequest) -> tuple[ohlcv.OhlcvBar, ...]:
    """Indirection seam so command tests can monkeypatch the network fetch."""
    return ohlcv.fetch_history(
        ohlcv.OhlcvRequest(
            symbol=request.symbol, interval=request.interval, bars=request.bars
        )
    )


def render_analysis_png(
    times: list[datetime],
    closes: list[float],
    computed: list[ind.ComputedIndicator],
    request: AnalyzeRequest,
) -> None:
    plt = _load_pyplot()
    panels = [c for c in computed if not c.spec.is_overlay]
    overlays = [c for c in computed if c.spec.is_overlay]

    bg = _THEME_BG.get(request.theme, _THEME_BG["dark"])
    fg = _THEME_FG.get(request.theme, _THEME_FG["dark"])
    n_panels = 1 + len(panels)
    height_ratios = [3] + [1] * len(panels)

    fig, axes = plt.subplots(
        n_panels,
        1,
        figsize=(request.width / 100, request.height / 100),
        sharex=True,
        gridspec_kw={"height_ratios": height_ratios},
        squeeze=False,
    )
    ax_col = axes[:, 0]
    fig.patch.set_facecolor(bg)

    price_ax = ax_col[0]
    price_ax.set_facecolor(bg)
    price_ax.plot(times, closes, color=fg, lw=0.9, label="Close")
    for i, comp in enumerate(overlays):
        color = _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)]
        for name, series in comp.series.items():
            xs = [t for t, v in zip(times, series, strict=True) if v is not None]
            ys = [v for v in series if v is not None]
            style = "--" if name in {"upper", "lower"} else "-"
            label = comp.spec.raw if name in {comp.spec.kind, "mid"} else None
            price_ax.plot(xs, ys, color=color, lw=1.6, linestyle=style, label=label)
    price_ax.set_title(f"{request.symbol} — {request.interval}", color=fg, fontsize=12)
    price_ax.legend(loc="upper left", fontsize=8, facecolor=bg, labelcolor=fg)
    price_ax.grid(alpha=0.2)

    for panel_ax, comp in zip(ax_col[1:], panels, strict=True):
        panel_ax.set_facecolor(bg)
        if comp.spec.kind == "rsi":
            series = comp.series["rsi"]
            xs = [t for t, v in zip(times, series, strict=True) if v is not None]
            ys = [v for v in series if v is not None]
            panel_ax.plot(xs, ys, color="#ffaa00", lw=1.2)
            panel_ax.axhline(70, color="#ef5350", lw=0.7, alpha=0.6)
            panel_ax.axhline(30, color="#26a69a", lw=0.7, alpha=0.6)
            panel_ax.set_ylim(0, 100)
        elif comp.spec.kind == "macd":
            line = comp.series["macd"]
            signal = comp.series["signal"]
            hist = comp.series["histogram"]
            xs = [t for t, v in zip(times, line, strict=True) if v is not None]
            panel_ax.plot(
                xs, [v for v in line if v is not None], color="#42a5f5", lw=1.1
            )
            xs_s = [t for t, v in zip(times, signal, strict=True) if v is not None]
            panel_ax.plot(
                xs_s, [v for v in signal if v is not None], color="#ffaa00", lw=1.1
            )
            xs_h = [t for t, v in zip(times, hist, strict=True) if v is not None]
            ys_h = [v for v in hist if v is not None]
            panel_ax.bar(
                xs_h,
                ys_h,
                color=["#26a69a" if v >= 0 else "#ef5350" for v in ys_h],
                width=0.8,
            )
        panel_ax.set_ylabel(comp.spec.raw, color=fg, fontsize=8)
        panel_ax.grid(alpha=0.2)
        panel_ax.tick_params(colors=fg, labelsize=7)

    price_ax.tick_params(colors=fg, labelsize=7)
    fig.autofmt_xdate()
    fig.tight_layout()
    request.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(request.out), dpi=100, facecolor=bg)
    plt.close(fig)


def run_analysis(request: AnalyzeRequest) -> dict[str, Any]:
    specs = [ind.parse_spec(raw) for raw in request.indicators]
    if not specs:
        # Default to a single 200-period weighted moving average.
        specs = [ind.parse_spec("wma:200")]

    bars = fetch_bars_query(request)
    closes = [bar.close for bar in bars]
    times = [datetime.fromtimestamp(bar.time, tz=UTC) for bar in bars]
    computed = [ind.compute(spec, closes) for spec in specs]

    render_analysis_png(times, closes, computed, request)

    return {
        "symbol": request.symbol,
        "interval": request.interval,
        "bars": len(bars),
        "indicators": [
            {
                "spec": c.spec.raw,
                "kind": c.spec.kind,
                "period": c.spec.period,
                "last": c.last,
            }
            for c in computed
        ],
        "path": str(request.out.resolve()),
        "bytes": request.out.stat().st_size,
    }
