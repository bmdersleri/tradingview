# Design Plan: TVCLI Free-Float Dashboard Visual & Functional Improvements

This plan outlines the visual modernization and functional enhancement of the `tvcli` free-float dashboards (both Single-Symbol Deep-Dive and Market Overview).

## 1. Objectives

- **Visual Excellence**: Elevate the visual style of the charts from basic matplotlib plots to a premium, modern design matching TradingView's styling.
- **Aesthetic Overhaul**: Replace raw solid lines with styled area/gradient fills, remove redundant axis spines, use custom grid styling, and refine fonts.
- **Better Space Utilization**: Rearrange the Market Overview layout into a 2-column grid instead of a 3-row vertical stack.
- **Data Enrichment**: Add rolling averages or trends to deep-dive ratio analysis.

---

## 2. Proposed Improvements

### 2.1. Single-Symbol Deep-Dive (Symbol mode)
- **Area Fill / Gradient**: Use `ax1.fill_between` with a low-opacity fill (`alpha=0.15`) under the ratio line to create a sleek area chart.
- **Line Style**: Increase line thickness (`linewidth=2.0`) and use anti-aliased, smooth styling.
- **Point Markers**: Add subtle data point markers (`marker="."`, `markersize=4`) so users can identify actual data points.
- **Subtle Grids**: Replace standard solid grid lines with dotted lines (`linestyle=":"`, `alpha=0.3`) for a less intrusive grid.
- **Spine Clean-up**: Remove the top and right spines (`ax.spines[...].set_visible(False)`) to make the panels look modern and airy.
- **Moving Average (SMA)**: Overlay a subtle, dashed line representing a rolling 20-period Simple Moving Average (SMA) of the ratio to highlight the trend.

### 2.2. Market Overview (Market mode)
- **2-Column Grid Layout**:
  - Instead of three vertical panels, reorganize the 1600x1000 viewport into a grid:
    - **Left Column** (60% width): Ratio Distribution Histogram (full height).
    - **Right Column** (40% width):
      - **Top Right**: Lowest-Float Leaderboard.
      - **Bottom Right**: High-Severity Events.
- **Leaderboard Visuals**:
  - Add value labels directly to the bars using `ax2.bar_label(...)` for instant readability.
  - Apply clean margins and rounded bar colors matching the severe/low-float thresholds.
- **Spine Clean-up**: Apply the same spine-removal and dotted-grid styling as the symbol mode.

---

## 3. Detailed Changes in `float_dashboard.py`

### 3.1. Subplot Mosaic or GridSpec
```python
# Market Overview Grid Layout
fig = plt.figure(figsize=(request.width / 100, request.height / 100), facecolor=bg)
gs = fig.add_gridspec(2, 2, width_ratios=[3, 2], height_ratios=[1, 1], hspace=0.3, wspace=0.25)
ax1 = fig.add_subplot(gs[:, 0]) # Ratio distribution (full height on left)
ax2 = fig.add_subplot(gs[0, 1]) # Leaderboard (top right)
ax3 = fig.add_subplot(gs[1, 1]) # Events (bottom right)
```

### 3.2. Styling Utilities
```python
def _apply_modern_styling(ax, bg, fg, grid, remove_x_ticks=False):
    ax.set_facecolor(bg)
    ax.tick_params(colors=fg, labelsize=7)
    for spine_name in ["top", "right"]:
        ax.spines[spine_name].set_visible(False)
    for spine_name in ["left", "bottom"]:
        ax.spines[spine_name].set_edgecolor(grid)
        ax.spines[spine_name].set_linewidth(0.8)
    ax.grid(True, color=grid, linestyle=":", linewidth=0.5, alpha=0.5)
```

---

## 4. Implementation Steps & Validation

1. **Modify `float_dashboard.py`**:
   - Implement the GridSpec layout for Market Overview.
   - Apply area fill, dotted grids, and spine removal in both deep-dive and overview modes.
   - Overlay a rolling SMA on the ratio chart.
2. **Review unit tests**:
   - Run existing dashboard tests to verify that data output contracts are preserved.
   - Add/update tests to ensure visual changes don't cause regressions.
3. **Run code validation**:
   - `just fmt` / `just lint`
   - `just test`
