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

from ..errors import TvcliError, UsageError
from . import indicators as ind
from . import ohlcv, signals

_THEME_BG = {"dark": "#131722", "light": "#ffffff"}
_THEME_FG = {"dark": "#d1d4dc", "light": "#131722"}
_OVERLAY_COLORS = ("#ffaa00", "#26a69a", "#ab47bc", "#ef5350", "#42a5f5")


_UP_COLOR = "#26a69a"
_DOWN_COLOR = "#ef5350"
_VALID_STYLES = ("candle", "line")


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
    style: str = "candle"
    volume: bool = True
    auto: bool = False


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


def _date_ticks(
    times: list[datetime], max_ticks: int = 10
) -> tuple[list[int], list[str]]:
    """Sparse (index, label) ticks for an index-based x-axis."""
    n = len(times)
    if n == 0:
        return [], []
    step = max(1, n // max_ticks)
    positions = list(range(0, n, step))
    labels = [times[i].strftime("%Y-%m-%d") for i in positions]
    return positions, labels


def _draw_candles(ax: Any, bars: list[ohlcv.OhlcvBar]) -> None:
    """Candlestick on an integer index axis: high-low wick + open-close body."""
    for x, bar in enumerate(bars):
        up = bar.close >= bar.open
        color = _UP_COLOR if up else _DOWN_COLOR
        ax.vlines(x, bar.low, bar.high, color=color, linewidth=0.8)
        lower = min(bar.open, bar.close)
        height = abs(bar.close - bar.open) or (bar.high - bar.low) * 0.001
        ax.bar(
            x,
            height,
            bottom=lower,
            width=0.7,
            color=color,
            edgecolor=color,
            linewidth=0.0,
        )


def _draw_volume(ax: Any, bars: list[ohlcv.OhlcvBar]) -> None:
    """Semi-transparent volume bars hugging the bottom of the price panel."""
    vol_ax = ax.twinx()
    vol_ax.set_facecolor("none")
    xs = list(range(len(bars)))
    volumes = [bar.volume for bar in bars]
    colors = [_UP_COLOR if bar.close >= bar.open else _DOWN_COLOR for bar in bars]
    vol_ax.bar(xs, volumes, width=0.7, color=colors, alpha=0.25, linewidth=0.0)
    peak = max(volumes) if volumes else 1.0
    # Stretch the axis so volume occupies only the bottom ~25% of the panel.
    vol_ax.set_ylim(0, peak * 4 if peak else 1.0)
    vol_ax.set_yticks([])
    vol_ax.set_zorder(0)
    ax.set_zorder(1)
    ax.patch.set_visible(False)


def render_analysis_png(
    bars: list[ohlcv.OhlcvBar],
    times: list[datetime],
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
    xs_idx = list(range(len(bars)))

    price_ax = ax_col[0]
    price_ax.set_facecolor(bg)
    # Volume first so it sits behind price (twinx z-order managed in helper).
    if request.volume and bars:
        _draw_volume(price_ax, bars)
    if request.style == "candle":
        _draw_candles(price_ax, bars)
    else:
        price_ax.plot(xs_idx, [b.close for b in bars], color=fg, lw=0.9, label="Close")

    for i, comp in enumerate(overlays):
        color = _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)]
        for name, series in comp.series.items():
            xs = [x for x, v in zip(xs_idx, series, strict=True) if v is not None]
            ys = [v for v in series if v is not None]
            style = "--" if name in {"upper", "lower"} else "-"
            label = comp.spec.raw if name in {comp.spec.kind, "mid"} else None
            price_ax.plot(xs, ys, color=color, lw=1.5, linestyle=style, label=label)
    price_ax.set_title(f"{request.symbol} — {request.interval}", color=fg, fontsize=12)
    handles, labels = price_ax.get_legend_handles_labels()
    if labels:
        price_ax.legend(loc="upper left", fontsize=8, facecolor=bg, labelcolor=fg)
    price_ax.grid(alpha=0.2)

    for panel_ax, comp in zip(ax_col[1:], panels, strict=True):
        panel_ax.set_facecolor(bg)
        if comp.spec.kind == "rsi":
            series = comp.series["rsi"]
            xs = [x for x, v in zip(xs_idx, series, strict=True) if v is not None]
            ys = [v for v in series if v is not None]
            panel_ax.plot(xs, ys, color="#ffaa00", lw=1.2)
            panel_ax.axhline(70, color=_DOWN_COLOR, lw=0.7, alpha=0.6)
            panel_ax.axhline(30, color=_UP_COLOR, lw=0.7, alpha=0.6)
            panel_ax.set_ylim(0, 100)
        elif comp.spec.kind == "macd":
            line = comp.series["macd"]
            signal = comp.series["signal"]
            hist = comp.series["histogram"]
            xs = [x for x, v in zip(xs_idx, line, strict=True) if v is not None]
            panel_ax.plot(
                xs, [v for v in line if v is not None], color="#42a5f5", lw=1.1
            )
            xs_s = [x for x, v in zip(xs_idx, signal, strict=True) if v is not None]
            panel_ax.plot(
                xs_s, [v for v in signal if v is not None], color="#ffaa00", lw=1.1
            )
            xs_h = [x for x, v in zip(xs_idx, hist, strict=True) if v is not None]
            ys_h = [v for v in hist if v is not None]
            panel_ax.bar(
                xs_h,
                ys_h,
                color=[_UP_COLOR if v >= 0 else _DOWN_COLOR for v in ys_h],
                width=0.8,
            )
        panel_ax.set_ylabel(comp.spec.raw, color=fg, fontsize=8)
        panel_ax.grid(alpha=0.2)
        panel_ax.tick_params(colors=fg, labelsize=7)

    positions, labels = _date_ticks(times)
    bottom_ax = ax_col[-1]
    bottom_ax.set_xticks(positions)
    bottom_ax.set_xticklabels(labels, rotation=30, ha="right")
    price_ax.tick_params(colors=fg, labelsize=7)
    fig.tight_layout()
    request.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(request.out), dpi=100, facecolor=bg)
    plt.close(fig)


def run_analysis(request: AnalyzeRequest) -> dict[str, Any]:
    if request.style not in _VALID_STYLES:
        raise UsageError(
            f"Unknown chart style '{request.style}'.",
            hint=f"Use one of: {', '.join(_VALID_STYLES)}.",
        )
    bars = list(fetch_bars_query(request))
    closes = [bar.close for bar in bars]
    times = [datetime.fromtimestamp(bar.time, tz=UTC) for bar in bars]

    signal_block: dict[str, Any] | None = None
    raw_specs = list(request.indicators)
    if request.auto:
        report = signals.analyze_signal(closes)
        signal_block = signals.signal_payload(report)
        # Only auto-fill indicators the user did not pin explicitly.
        if not raw_specs:
            raw_specs = list(report.selected_indicators)

    specs = [ind.parse_spec(raw) for raw in raw_specs]
    if not specs:
        # Default to a single 200-period weighted moving average.
        specs = [ind.parse_spec("wma:200")]

    computed = [ind.compute(spec, closes) for spec in specs]
    render_analysis_png(bars, times, computed, request)

    payload: dict[str, Any] = {
        "symbol": request.symbol,
        "interval": request.interval,
        "bars": len(bars),
        "style": request.style,
        "volume": request.volume,
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
    if signal_block is not None:
        payload["signal"] = signal_block
    return payload
