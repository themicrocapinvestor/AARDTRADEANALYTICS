"""The infographic per trade -- a multi-panel technical chart.

Caller picks up to two triggers -- capped at 2 because more caused
per-occurrence label collisions.
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


def _stage_segments(w):
    """Contiguous (start_date, end_date, stage_name) runs of daily_base.stage()
    across the FULL fetched history, not just the chart's visible window --
    so a stage that began before the window's 75-day lookback still reports
    its true duration instead of only the sliver that happens to be on screen."""
    stages = [stage_at(w, i) for i in range(len(w))]
    segments = []
    seg_start = 0
    for i in range(1, len(stages) + 1):
        if i == len(stages) or stages[i] != stages[seg_start]:
            if stages[seg_start] is not None:
                segments.append((w.index[seg_start], w.index[i - 1], stages[seg_start]))
            seg_start = i
    return segments


def trade_chart(w, diag, triggers, gann_levels=None):
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

    # --- Row 1: Stage shading + one label (with duration) per wide-enough stage segment ---
    for seg_start, seg_end, seg_name in _stage_segments(w):
        if seg_end < window.index[0] or seg_start > window.index[-1]:
            continue
        vis_start, vis_end = max(seg_start, window.index[0]), min(seg_end, window.index[-1])
        fig.add_vrect(x0=vis_start, x1=vis_end, fillcolor=STAGE_FILL.get(seg_name, "rgba(0,0,0,0)"),
                       line_width=0, layer="below", row=1, col=1)
        vis_bars = window.index.slice_indexer(vis_start, vis_end)
        if vis_bars.stop - vis_bars.start < STAGE_MIN_LABEL_BARS:
            continue
        weeks = round((seg_end - seg_start).days / 7)
        week_word = "week" if weeks == 1 else "weeks"
        mid = window.index[(vis_bars.start + vis_bars.stop) // 2]
        fig.add_annotation(
            x=mid, y=0.98, yref="y domain", yanchor="top", xanchor="center",
            text=f"{STAGE_LABEL.get(seg_name, seg_name)}<br><b>{weeks} {week_word}</b>", showarrow=False,
            font=dict(size=13, color=INK), bgcolor="rgba(27,30,39,0.75)",
            bordercolor=STAGE_FILL.get(seg_name, GRID).replace("0.10", "0.9"), borderwidth=1, borderpad=4,
            row=1, col=1,
        )

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
    """Replaces st.bar_chart, which clips long mistake labels under rotated ticks."""
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


_HEATMAP_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def monthly_heatmap(months):
    """Months with no trades render as blank grey cells rather than 0% (0%
    is a real, plottable outcome; "no data" is not the same thing)."""
    by_key = {m["sort_key"]: m for m in months}
    years = sorted({int(k[:4]) for k in by_key}, reverse=True)

    z, text, customdata = [], [], []
    for y in years:
        row_z, row_text, row_cd = [], [], []
        for mo in range(1, 13):
            m = by_key.get(f"{y}-{mo:02d}")
            if m is None:
                row_z.append(None)
                row_text.append("")
                row_cd.append("No closed trades")
            else:
                row_z.append(m["avg_return_pct"])
                row_text.append(f'{m["avg_return_pct"]:+.1f}%')
                row_cd.append(f'{m["n_trades"]} trade(s)')
        z.append(row_z)
        text.append(row_text)
        customdata.append(row_cd)

    fig = go.Figure(go.Heatmap(
        z=z, x=_HEATMAP_MONTH_NAMES, y=[str(y) for y in years],
        text=text, texttemplate="%{text}", textfont=dict(size=12, color=INK),
        customdata=customdata,
        hovertemplate="%{y} %{x}: %{text}<br>%{customdata}<extra></extra>",
        colorscale=[[0, LOSS], [0.5, SURFACE], [1, DISCIPLINE]],
        zmid=0, showscale=True,
        colorbar=dict(title="Return %", tickfont=dict(color=INK), title_font=dict(color=INK)),
        xgap=3, ygap=3,
    ))
    fig.update_layout(
        template="plotly_dark",
        height=max(180, 70 * len(years) + 60),
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(color=INK, family="IBM Plex Sans, sans-serif", size=13),
        xaxis=dict(side="top", showgrid=False),
        yaxis=dict(showgrid=False, autorange="reversed"),
    )
    return fig


def holding_period_chart(diagnosed):
    wins = [d["days_in_trade"] for d in diagnosed if d["pnl_rupees"] > 0]
    losses = [d["days_in_trade"] for d in diagnosed if d["pnl_rupees"] <= 0]
    avg_win = round(sum(wins) / len(wins), 1) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 1) if losses else 0.0

    fig = go.Figure(go.Bar(
        x=[avg_win, avg_loss], y=["Winning trades", "Losing trades"], orientation="h",
        marker_color=[DISCIPLINE, LOSS],
        text=[f"{avg_win:.1f} days", f"{avg_loss:.1f} days"], textposition="outside",
        customdata=[len(wins), len(losses)],
        hovertemplate="%{y}: %{x:.1f} days avg, %{customdata} trade(s)<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_dark",
        height=220,
        margin=dict(l=10, r=40, t=10, b=10),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(color=INK, family="IBM Plex Sans, sans-serif", size=13),
        xaxis=dict(title="Avg days held", gridcolor=GRID),
        yaxis=dict(autorange="reversed"),
        showlegend=False,
    )
    return fig


def equity_curve_chart(diagnosed, benchmark_daily=None, benchmark_label="NIFTY 500"):
    """Portfolio curve is a running SUM of each trade's user_return_pct
    (plain addition, not compounded) -- matches the convention used by
    backtest_stats/monthly_returns elsewhere in this app."""
    ordered = sorted(diagnosed, key=lambda d: d["exit_date"])
    dates = [d["exit_date"] for d in ordered]
    cum = []
    running = 0.0
    for d in ordered:
        running += d["user_return_pct"]
        cum.append(running)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=cum, mode="lines", name="Your portfolio", line=dict(color=DISCIPLINE, width=2, shape="hv"),
    ))

    if benchmark_daily is not None and not benchmark_daily.empty and dates:
        bench = benchmark_daily[benchmark_daily.index >= pd.Timestamp(dates[0])]
        bench = bench[bench.index <= pd.Timestamp(dates[-1])]
        if not bench.empty:
            base = bench["close"].iloc[0]
            bench_cum = (bench["close"] / base - 1) * 100
            fig.add_trace(go.Scatter(
                x=bench.index, y=bench_cum.values, mode="lines", name=benchmark_label,
                line=dict(color=MUTED, width=2, dash="dot"),
            ))

    fig.update_layout(
        template="plotly_dark",
        height=380,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(color=INK, family="IBM Plex Sans, sans-serif", size=13),
        xaxis=dict(gridcolor=GRID),
        yaxis=dict(gridcolor=GRID, title="Cumulative return %"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(size=12, color=INK), bgcolor="rgba(27,30,39,0.85)",
                    bordercolor=GRID, borderwidth=1),
        hovermode="x unified",
    )
    return fig
