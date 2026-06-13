# Design Plan: TVCLI Free-Float Dashboard Visual & Functional Improvements

This document outlines the detailed design specifications and implementation plan to modernize the `tvcli` free-float dashboards (Single-Symbol Deep-Dive and BIST Market Overview) both visually and functionally.

## 1. Context & Background

The current dashboard is generated using matplotlib in `src/tvcli/layers/float_dashboard.py`. While functional, the charts look like standard, generic scientific plots. Since this tool serves financial/trading data analytics, we want to align its visual style with modern professional trading platforms like TradingView.

---

## 2. Visual Style & Palette Specifications

We will adopt a dark-first theme based on TradingView's official color palettes.

| UI Element | Dark Theme Color | Light Theme Color | Purpose |
| :--- | :--- | :--- | :--- |
| **Canvas Background** | `#131722` (TV Dark Blue) | `#ffffff` | Main window background |
| **Plot Background** | `#181c27` (Slightly lighter) | `#f8f9fa` | Individual panel face color |
| **Grid Lines** | `#2a2e39` (Low alpha) | `#e0e0e0` | Subtle dashed layout grids |
| **Text/Labels** | `#d1d4dc` (High contrast) | `#131722` | Titles, tick labels, axis labels |
| **Bullish/Positive** | `#26a69a` (Teal) | `#00897b` | Normal ratios, positive changes |
| **Bearish/Negative** | `#ef5350` (Red/Rose) | `#d32f2f` | Low-float warnings, negative changes |
| **Alert/Warning** | `#f57c00` (Orange) | `#f57c00` | High-severity events, threshold line |

---

## 3. Structural & Layout Improvements

### 3.1. Single-Symbol Deep-Dive (Symbol Mode)
The existing layout is a 3-row vertical stack. We will enhance it by:
1. **Area Chart Conversion**: Replacing the simple line chart in Panel 1 with a solid boundary line (`linewidth=2.0`) and a semi-transparent area fill underneath using `ax1.fill_between(xs, ratios, color=..., alpha=0.15)`.
2. **Overlaying Rolling Average**: Calculating and plotting a 20-period Simple Moving Average (SMA) of the ratio (dashed gold line) to show long-term trend direction.
3. **Data Point Markers**: Adding small circular markers (`marker="o"`, `markersize=3`, `markeredgewidth=0`) on actual data points.
4. **Dotted Grid**: Changing grids from solid to dotted lines (`linestyle=":"`, `alpha=0.4`) and hiding top/right spines.

### 3.2. BIST Market Overview (Market Mode)
The current overview is a vertical stack of three wide rows, which wastes horizontal space on a 1600x1000 viewport. We will rearrange this into a 2-column layout.

#### Layout Comparison (Before vs After)

```mermaid
graph TD
    subgraph Current Layout
        C1[Panel 1: Ratio Distribution - 100% Width]
        C2[Panel 2: Leaderboard - 100% Width]
        C3[Panel 3: High-Severity Events - 100% Width]
        C1 --> C2 --> C3
    end

    subgraph Proposed Grid Layout (2-Column)
        L1[Left Column: Ratio Distribution Histogram <br> Width: 60%, Height: 100%]
        R1[Right Column - Top: Lowest Float Leaderboard <br> Width: 40%, Height: 50%]
        R2[Right Column - Bottom: High-Severity Events <br> Width: 40%, Height: 50%]
    end
```

To implement this grid, we will replace `plt.subplots(3, 1)` with `GridSpec`:
```python
fig = plt.figure(figsize=(request.width / 100, request.height / 100), facecolor=bg)
gs = fig.add_gridspec(2, 2, width_ratios=[3, 2], height_ratios=[1, 1], hspace=0.3, wspace=0.25)
ax1 = fig.add_subplot(gs[:, 0])  # Histogram (spans all rows on left column)
ax2 = fig.add_subplot(gs[0, 1])  # Leaderboard (top right)
ax3 = fig.add_subplot(gs[1, 1])  # Events (bottom right)
```

---

## 4. Implementation Details

### 4.1. Helper Function for Spine and Grid Styling
To ensure consistency, we will create a helper function `_apply_theme_to_axis`:
```python
def _apply_theme_to_axis(ax, bg, fg, grid):
    ax.set_facecolor(bg)
    ax.tick_params(colors=fg, labelsize=7)
    
    # Hide top and right spines
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
        
    # Style remaining spines
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_edgecolor(grid)
        ax.spines[spine].set_linewidth(0.8)
        
    # Set subtle grid lines
    ax.grid(True, color=grid, linestyle=":", linewidth=0.5, alpha=0.4)
```

### 4.2. Enhancing Bar Labels
Using matplotlib's modern `ax.bar_label` API to show exact numbers at the end of the leaderboard bars:
```python
container = ax2.barh(ys, top_ratios, color=bar_colors, alpha=0.8)
ax2.bar_label(container, fmt="%.2f%%", padding=3, color=fg, fontsize=6)
```

---

## 5. Implementation Roadmap & Quality Assurance

1. **Phase 1: Code Modifications**:
   - Update `src/tvcli/layers/float_dashboard.py` to add `_apply_theme_to_axis`.
   - Update `_render_deep_dive` to add area fills, SMA line, and styling.
   - Update `_render_market_overview` to use the 2-column `GridSpec` and `bar_label`.
2. **Phase 2: Code Quality & Formatting**:
   - Run `just fmt` / `just lint` to verify that there are no style, syntax, or mypy type-checking issues.
3. **Phase 3: Automated Testing**:
   - Run `just test` to check for regressions.
   - Ensure coverage remains above 80% (aiming to maintain or improve current 86.28%).
