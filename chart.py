"""The infographic per trade -- a big, readable multi-panel technical chart
(OHLC price bars + 20/50/150/200-day averages + volume with its own 50-day
average + Stage shading, Relative Strength, MACD, RSI), sharing one x-axis.

The caller (app.py) picks up to two triggers from exit_triggers.TRIGGERS via
a dropdown -- keeping this to two is what makes per-occurrence arrow labels
readable instead of the wall-of-labels collision problem a fixed 6+ triggers
produced. Every occurrence still gets a marker dot (on the price panel, plus
a small ring on the relevant indicator panel), but only occurrences at least
LABEL_MIN_GAP_DAYS apart get a floating arrow label -- dense repeats (e.g. a
choppy stretch of distribution days) still show as dots without stacking
text on top of each other. Optional Gann/Fibonacci levels (from
exit_triggers.gann_fib_levels) draw as dashed horizontal reference lines
from entry to the most recent bar.
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from daily_base import stage as stage_at

INK = "#E9E4D6"
MUTED = "#78808F"
LOSS = "#E1573F"
DISCIPLINE = "#4FBFA6"
SURFACE = "#1B1E27"
GRID = "#2C303B"
VIOLET = "#9D7FE0"
AMBER = "#F2C14E"

# Up to two triggers are ever drawn at once (see module docstring) -- one
# color/arrow-direction slot per pick, not a per-trigger-key mapping.
# Deliberately NOT AMBER/VIOLET -- those are already the 200-day/150-day
# average line colors (below); reusing them for trigger markers made a
# trigger dot look like it belonged to a moving-average line at a glance.
TRIGGER_SLOT_COLOR = ["#F2789A", "#4EC3E0"]
TRIGGER_SLOT_AY = [-56, 56]
LABEL_MIN_GAP_DAYS = 5  # minimum trading-day gap between two floating labels for the same trigger

GANN_STOP_COLOR = LOSS
GANN_TARGET_COLORS = [DISCIPLINE, "#3FA98C", "#2E8B74", "#1F6B5A"]


def _marker_row(key):
    """Which indicator panel gets a small reference ring, in addition to the
    dot always shown on the price panel. 1=Price, 3=RS, 4=MACD, 5=RSI."""
    if key.startswith("rsi"):
        return 5
    if key.startswith("macd"):
        return 4
    if key.startswith("rs_"):
        return 3
    return 1


def _thin_for_labels(occurrences, min_gap_days):
    """Every occurrence gets a dot regardless, but only occurrences at least
    min_gap_days apart get a floating text label -- avoids stacking labels
    when a trigger fires repeatedly in a short, choppy stretch."""
    labeled, last_date = [], None
    for occ in occurrences:
        if last_date is None or (occ["date"] - last_date).days >= min_gap_days:
            labeled.append(occ)
            last_date = occ["date"]
    return labeled

STAGE_FILL = {
    "Stage 1": "rgba(74,135,243,0.10)",   # basing -- blue
    "Stage 2": "rgba(79,191,166,0.10)",   # advancing -- teal/green
    "Stage 3": "rgba(242,193,78,0.10)",   # topping -- amber
    "Stage 4": "rgba(225,87,63,0.10)",    # declining -- red
}
STAGE_LABEL = {"Stage 1": "Stage 1 · Basing", "Stage 2": "Stage 2 · Advancing",
               "Stage 3": "Stage 3 · Topping", "Stage 4": "Stage 4 · Declining"}
STAGE_MIN_LABEL_BARS = 15  # skip labelling a stage segment shorter than this -- avoids the
                            # squashed, overlapping-text look when stage flips quickly


def _stage_segments(w, window_index):
    """Contiguous (start_date, end_date, stage_name) runs of daily_base.stage()
    within the visible window -- used to shade the price panel and drop one
    label per (wide enough) stage change, instead of relabelling every bar."""
    positions = w.index.get_indexer(window_index)
    stages = [stage_at(w, int(p)) for p in positions]
    segments = []
    seg_start = 0
    for i in range(1, len(stages) + 1):
        if i == len(stages) or stages[i] != stages[seg_start]:
            if stages[seg_start] is not None:
                segments.append((window_index[seg_start], window_index[i - 1], stages[seg_start]))
            seg_start = i
    return segments


def trade_chart(w, diag, triggers, gann_levels=None):
    """w: the symbol's prepared indicator frame. diag: one diagnosed trade
    dict. triggers: up to two rows from exit_triggers.evaluate_triggers(...)
    output (each with a "key", "label", and "occurrences" list) -- the ones
    the user picked to mark on the chart. gann_levels: optional
    exit_triggers.gann_fib_levels(...) output, drawn as dashed reference
    lines from entry to the latest bar."""
    entry_date, exit_date = diag["entry_date"], diag["exit_date"]

    window_start = max(entry_date - pd.Timedelta(days=75), w.index[0])
    window_end = w.index[-1]  # always run to the latest fetched bar
    window = w.loc[(w.index >= window_start) & (w.index <= window_end)]
    sma20 = window["close"].rolling(20, min_periods=1).mean()
    vol_sma50 = w["volume"].rolling(50, min_periods=1).mean().loc[window.index]

    fig = make_subplots(
        rows=5, cols=1, shared_xaxes=True,
        row_heights=[0.46, 0.10, 0.13, 0.155, 0.155],
        vertical_spacing=0.035,
        subplot_titles=("Price", "Volume", "Relative Strength vs Nifty 500", "MACD", "RSI"),
    )
    fig.update_annotations(font_size=16)  # the row titles Plotly auto-adds

    # --- Row 1: Stage shading + one label per wide-enough stage segment ---
    for seg_start, seg_end, seg_name in _stage_segments(w, window.index):
        fig.add_vrect(x0=seg_start, x1=seg_end, fillcolor=STAGE_FILL.get(seg_name, "rgba(0,0,0,0)"),
                       line_width=0, layer="below", row=1, col=1)
        seg_bars = window.index.slice_indexer(seg_start, seg_end)
        if seg_bars.stop - seg_bars.start < STAGE_MIN_LABEL_BARS:
            continue
        mid = window.index[(seg_bars.start + seg_bars.stop) // 2]
        fig.add_annotation(x=mid, y=0.98, yref="y domain", yanchor="top", xanchor="center",
                            text=STAGE_LABEL.get(seg_name, seg_name), showarrow=False,
                            font=dict(size=13, color=INK), bgcolor="rgba(27,30,39,0.75)",
                            row=1, col=1)

    # --- Row 1: price as OHLC bars (not candles -- cleaner at this density) + averages ---
    fig.add_trace(go.Ohlc(x=window.index, open=window["open"], high=window["high"],
                           low=window["low"], close=window["close"], name="Price",
                           increasing_line_color=DISCIPLINE, decreasing_line_color=LOSS),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=window.index, y=sma20, name="20-day avg",
                              line=dict(color=DISCIPLINE, width=1, dash="dot")), row=1, col=1)
    if "ema50" in window.columns:
        fig.add_trace(go.Scatter(x=window.index, y=window["ema50"], name="50-day avg",
                                  line=dict(color=MUTED, width=1, dash="dot")), row=1, col=1)
    if "ema150" in window.columns:
        fig.add_trace(go.Scatter(x=window.index, y=window["ema150"], name="150-day avg",
                                  line=dict(color=VIOLET, width=1, dash="dot")), row=1, col=1)
    if "ema200" in window.columns:
        fig.add_trace(go.Scatter(x=window.index, y=window["ema200"], name="200-day avg",
                                  line=dict(color=AMBER, width=1, dash="dot")), row=1, col=1)

    fig.add_trace(go.Scatter(x=[entry_date], y=[diag["entry_price"]], mode="markers",
                              name="Your entry",
                              marker=dict(color=DISCIPLINE, size=13, symbol="triangle-up")), row=1, col=1)
    fig.add_trace(go.Scatter(x=[exit_date], y=[diag["exit_price"]], mode="markers",
                              name="Your exit",
                              marker=dict(color=LOSS, size=13, symbol="triangle-down")), row=1, col=1)
    # Arrow annotations instead of trace text -- "ay" gives a real pixel
    # offset so the label clears the OHLC bar at the entry/exit date instead
    # of sitting flush against it.
    fig.add_annotation(x=entry_date, y=diag["entry_price"], text="Entry", showarrow=True,
                        arrowhead=0, arrowcolor=DISCIPLINE, ax=0, ay=-32,
                        font=dict(size=13, color=DISCIPLINE), bgcolor="rgba(27,30,39,0.75)",
                        row=1, col=1)
    fig.add_annotation(x=exit_date, y=diag["exit_price"], text="Your exit", showarrow=True,
                        arrowhead=0, arrowcolor=LOSS, ax=0, ay=32,
                        font=dict(size=13, color=LOSS), bgcolor="rgba(27,30,39,0.75)",
                        row=1, col=1)

    # --- Row 2: volume + its own 50-day average ---
    vol_colors = [DISCIPLINE if c >= o else LOSS for o, c in zip(window["open"], window["close"])]
    fig.add_trace(go.Bar(x=window.index, y=window["volume"], name="Volume",
                          marker_color=vol_colors, showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=window.index, y=vol_sma50, name="50-day avg volume",
                              line=dict(color=AMBER, width=1.2), showlegend=False), row=2, col=1)

    # --- Row 3: RS (60-day, vs Nifty 500) ---
    if "rs_60" in window.columns:
        fig.add_trace(go.Scatter(x=window.index, y=window["rs_60"] * 100, name="RS (60d)",
                                  line=dict(color=DISCIPLINE, width=1.3), showlegend=False), row=3, col=1)
        fig.add_hline(y=0, line_dash="dot", line_color=MUTED, row=3, col=1)

    # --- Row 4: MACD ---
    if "macd_line" in window.columns:
        fig.add_trace(go.Scatter(x=window.index, y=window["macd_line"], name="MACD",
                                  line=dict(color=INK, width=1.2), showlegend=False), row=4, col=1)
        fig.add_trace(go.Scatter(x=window.index, y=window["macd_signal"], name="Signal",
                                  line=dict(color=LOSS, width=1, dash="dot"), showlegend=False), row=4, col=1)

    # --- Row 5: RSI ---
    if "rsi" in window.columns:
        fig.add_trace(go.Scatter(x=window.index, y=window["rsi"], name="RSI",
                                  line=dict(color=INK, width=1.2), showlegend=False), row=5, col=1)
        fig.add_hline(y=70, line_dash="dot", line_color=LOSS, row=5, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color=DISCIPLINE, row=5, col=1)

    # Every occurrence gets a dot (price panel + a ring on the relevant
    # indicator panel). Only occurrences LABEL_MIN_GAP_DAYS apart get a
    # floating arrow label -- with at most two triggers selected (the
    # dropdown above the chart), this keeps labels both readable (real
    # arrows pointing at a real date, not a summary box) and collision-free
    # (dense repeats fall back to dots-only). Full per-occurrence detail is
    # always in app.py's trigger table below the chart regardless.
    for slot, t in enumerate(triggers[:2]):
        color = TRIGGER_SLOT_COLOR[slot]
        ay = TRIGGER_SLOT_AY[slot]
        marker_row = _marker_row(t["key"])
        occs_in_window = [occ for occ in t["occurrences"] if occ["date"] in window.index]
        labeled_dates = {occ["date"] for occ in _thin_for_labels(occs_in_window, LABEL_MIN_GAP_DAYS)}
        for occ in occs_in_window:
            if marker_row != 1:
                if marker_row == 3 and "rs_60" in window.columns:
                    y = float(window["rs_60"].loc[occ["date"]]) * 100
                elif marker_row == 4 and "macd_line" in window.columns:
                    y = float(window["macd_line"].loc[occ["date"]])
                elif marker_row == 5 and "rsi" in window.columns:
                    y = float(window["rsi"].loc[occ["date"]])
                else:
                    y = None
                if y is not None:
                    fig.add_trace(go.Scatter(x=[occ["date"]], y=[y], mode="markers", name=t["label"],
                                              marker=dict(color=color, size=9, symbol="circle-open", line=dict(width=2)),
                                              showlegend=False, hovertext=t["label"]), row=marker_row, col=1)
            fig.add_trace(go.Scatter(
                x=[occ["date"]], y=[occ["price"]], mode="markers", name=t["label"],
                marker=dict(color=color, size=7, symbol="circle"),
                showlegend=False, hoverinfo="text", hovertext=f'{t["label"]} {occ["return_pct"]:+.0f}%',
            ), row=1, col=1)
            if occ["date"] in labeled_dates:
                fig.add_annotation(x=occ["date"], y=occ["price"], text=f'{occ["return_pct"]:+.0f}%',
                                    showarrow=True, arrowhead=2, arrowsize=1, arrowwidth=1.3,
                                    arrowcolor=color, ax=0, ay=ay,
                                    font=dict(size=11, color=color), bgcolor="rgba(27,30,39,0.85)",
                                    bordercolor=color, borderwidth=1, row=1, col=1)
        if occs_in_window:
            # Bottom-left, not top-left -- the top-left corner of the price
            # panel is where stage-segment labels land (see _stage_segments
            # below); anchoring both there produced overlapping/concatenated
            # text whenever an early stage segment's midpoint fell near the
            # left edge of the visible window.
            fig.add_annotation(x=0.01, y=0.02 + 0.045 * slot, xref="x domain", yref="y domain",
                                xanchor="left", yanchor="bottom", showarrow=False,
                                text=f'<span style="color:{color}">●</span> {t["label"]}',
                                font=dict(size=11, color=INK), bgcolor="rgba(27,30,39,0.75)",
                                row=1, col=1)

    if gann_levels:
        end_date = window.index[-1]
        start_date = max(entry_date, window.index[0])
        stop = gann_levels["stop"]
        fig.add_trace(go.Scatter(x=[start_date, end_date], y=[stop["price"]] * 2, mode="lines",
                                  name=stop["label"], line=dict(color=GANN_STOP_COLOR, width=1, dash="dash"),
                                  showlegend=False, hoverinfo="skip"), row=1, col=1)
        fig.add_annotation(x=end_date, y=stop["price"], text=stop["label"], showarrow=False,
                            xanchor="left", font=dict(size=10, color=GANN_STOP_COLOR), row=1, col=1)
        for target, color in zip(gann_levels["targets"], GANN_TARGET_COLORS):
            fig.add_trace(go.Scatter(x=[start_date, end_date], y=[target["price"]] * 2, mode="lines",
                                      name=target["label"], line=dict(color=color, width=1, dash="dash"),
                                      showlegend=False, hoverinfo="skip"), row=1, col=1)
            fig.add_annotation(x=end_date, y=target["price"], text=target["label"], showarrow=False,
                                xanchor="left", font=dict(size=10, color=color), row=1, col=1)

    fig.update_layout(
        template="plotly_dark",
        height=1300,
        margin=dict(l=10, r=10, t=40, b=10),
        # y=1.02 sits just above the plot's own paper area, which the app's
        # light/dark mode toggle otherwise leaves uncovered (whatever's
        # behind the chart shows through there) -- an explicit bgcolor here
        # guarantees the legend always has its own dark backing rectangle
        # for the light-colored text to sit on, regardless of page theme.
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                     font=dict(size=12, color=INK), bgcolor="rgba(27,30,39,0.85)",
                     bordercolor=GRID, borderwidth=1),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(color=INK, family="IBM Plex Sans, sans-serif", size=13),
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(gridcolor=GRID, tickfont=dict(size=12))
    fig.update_yaxes(gridcolor=GRID, tickfont=dict(size=12))
    return fig


def mistake_frequency_chart(freq):
    """Horizontal bar chart, full un-truncated labels -- replaces
    st.bar_chart, which clips long mistake labels under rotated ticks."""
    labels = list(freq.keys())
    values = list(freq.values())
    fig = go.Figure(go.Bar(x=values, y=labels, orientation="h", marker_color=LOSS))
    fig.update_layout(
        template="plotly_dark",
        height=max(220, 46 * len(labels)),
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(color=INK, family="IBM Plex Sans, sans-serif", size=13),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(autorange="reversed"),
    )
    return fig
