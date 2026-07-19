"""Independent technical exit triggers -- a descriptive "what if you'd exited
here instead" comparison, not a recommendation. Split into two sides: STOP
triggers (bearish deterioration reads) and PROFIT triggers (bullish/target
reads), so app.py can offer "if you'd exited on whichever of these two came
first" instead of one flat list. Every trigger mirrors a number or lens this
app already uses elsewhere for the same concept, rather than inventing new
criteria: RSI_OVERBOUGHT (this module's own stop-side threshold, just
watching the cross the other direction), CHASE_EXTENSION_PCT
(mistake_diagnosis.py's "bought too extended" threshold, reused here as
"extended enough to take profit"), TARGET_TRIGGER_PCT/
MIN_REWARD_RISK_MULTIPLE (unified_backtest.py's own systematic-replay
target), daily_base.stage/_trend_ok_classic (this app's existing Weinstein
Stage Analysis and Minervini/O'Neil trend-template lenses), and the Gann/
Fibonacci retracement-and-extension levels computed off the pre-entry swing
(gann_fib_levels below -- also used by chart.py to draw the levels).

Every trigger label states its exact numeric condition -- not a vague
description -- since these are simulation checks, not narrative color.
"""
import daily_base
import candlestick_patterns
from mistake_diagnosis import CHASE_EXTENSION_PCT
from unified_backtest import MIN_REWARD_RISK_MULTIPLE, TARGET_TRIGGER_PCT

RSI_OVERBOUGHT = 70.0

ADX_WEAK_TREND = 25.0            # extra_indicators.adx's own "trending" bar (MOMENTUM_ADX_MIN in daily_base.py)
ATR_EXTENSION_MULTIPLE = 2.0     # price this many ATRs above its 20-day average -- mean-reversion stretch
TARGET_MOVE_PCT = MIN_REWARD_RISK_MULTIPLE * TARGET_TRIGGER_PCT  # the systematic-replay target as a flat % move

# Gann/Fibonacci levels, computed off the swing from the pre-entry structural
# low up to the entry price itself (GANN.rtf: "identify the swing" -> "apply
# the tools"). Retracement/extension math per that doc's Gann-wheel/Fibonacci
# confluence table (50% = invalidation, 61.8%/100%/161.8%/261.8% = targets).
GANN_SWING_LOOKBACK_DAYS = 40    # bars back from entry to look for the swing low
GANN_STOP_RETRACEMENT = 0.50
GANN_STOP_KEY = "gann_50pct_retracement_stop"
GANN_STOP_LABEL_SHORT = "50% retracement stop"
GANN_STOP_LABEL_FULL = "Closed below the 50% Gann/Fibonacci retracement of the pre-entry swing"
GANN_EXTENSIONS = [
    ("gann_618_extension_target", 0.618, "61.8% ext",
     "Closed above the 61.8% Fibonacci extension of the pre-entry swing"),
    ("gann_100pct_expansion_target", 1.0, "100% exp",
     "Closed above the 100% Gann expansion (equal-move target) of the pre-entry swing"),
    ("gann_1618_extension_target", 1.618, "161.8% ext",
     "Closed above the 161.8% Fibonacci extension of the pre-entry swing"),
    ("gann_2618_extension_target", 2.618, "261.8% ext",
     "Closed above the 261.8% Fibonacci extension of the pre-entry swing"),
]


def gann_fib_levels(w, i_entry, entry_price):
    """Swing low = lowest low in the GANN_SWING_LOOKBACK_DAYS bars up to and
    including entry. Swing range = entry_price - swing_low (the structural
    move that led to entry). Returns None if that range isn't positive (e.g.
    entry wasn't actually a breakout above the recent low). Shared by
    _detect() (crossover checks) and chart.py (drawing the levels)."""
    lo_idx = max(0, i_entry - GANN_SWING_LOOKBACK_DAYS)
    swing_low = float(w["low"].iloc[lo_idx:i_entry + 1].min())
    swing_range = entry_price - swing_low
    if swing_range <= 0:
        return None
    return {
        "swing_low": swing_low,
        "stop": {"key": GANN_STOP_KEY, "label": GANN_STOP_LABEL_SHORT,
                 "price": entry_price - GANN_STOP_RETRACEMENT * swing_range},
        "targets": [
            {"key": key, "label": short_label, "price": entry_price + pct * swing_range}
            for key, pct, short_label, _ in GANN_EXTENSIONS
        ],
    }


STOP_LOSS_TRIGGERS = [
    ("rsi_overbought_rollover", f"RSI(14) crossed back below {RSI_OVERBOUGHT:.0f} after being overbought"),
    ("macd_bearish_cross", "MACD crossed below its signal line"),
    ("rs_below_zero", "Relative strength (vs Nifty 500, 60-day) turned negative"),
    ("price_below_20dma", "Price closed below its 20-day average"),
    ("price_below_50dma", "Price closed below its 50-day average"),
    ("dma20_below_dma50", "20-day average crossed below the 50-day average"),
    ("adx_trend_weakening", f"ADX(14) crossed below {ADX_WEAK_TREND:.0f} after trending"),
    ("distribution_day", "Distribution day (down-close, rising volume)"),
    ("vstop_downtrend_flip", "Volatility-stop flipped to downtrend"),
    ("stage4_confirmed", "Weinstein Stage Analysis flipped to Stage 4 (declining)"),
    ("trend_template_broken", "Minervini/O'Neil trend template broken (close/50DMA/200DMA no longer stacked with 200DMA rising)"),
    (GANN_STOP_KEY, GANN_STOP_LABEL_FULL),
] + [(key, label) for key, (label, _) in candlestick_patterns.PATTERNS.items()]
PROFIT_TRIGGERS = [
    ("rsi_overbought_touch", f"RSI(14) crossed above {RSI_OVERBOUGHT:.0f} (overbought)"),
    ("price_extended_above_50dma", f"Closed {CHASE_EXTENSION_PCT:.0f}%+ above the 50-day average"),
    ("target_hit", f"Closed {TARGET_MOVE_PCT:.0f}%+ above entry (systematic-replay target)"),
    ("atr_extended_above_20dma", f"Closed {ATR_EXTENSION_MULTIPLE:.0f}+ ATRs above the 20-day average"),
] + [(key, full_label) for key, _, _, full_label in GANN_EXTENSIONS]
TRIGGERS = STOP_LOSS_TRIGGERS + PROFIT_TRIGGERS


def _all_after(series_bool, start_i):
    """Every index position >= start_i where series_bool is True."""
    s = series_bool.iloc[start_i:]
    return list(s[s].index)


def _detect(w, i_entry, entry_price):
    """{trigger_key: [every date the crossover fired, in order]}."""
    start = i_entry + 1
    if start >= len(w):
        return {key: [] for key, _ in TRIGGERS}

    close = w["close"]
    sma20 = close.rolling(20).mean()
    ema50 = w.get("ema50")
    rsi = w.get("rsi")
    macd_line, macd_signal = w.get("macd_line"), w.get("macd_signal")
    rs60 = w.get("rs_60")

    out = {}

    if rsi is not None:
        # Stateful scan, not a plain crossover mask: RSI can sit above/below
        # RSI_OVERBOUGHT for a stretch, and this only wants to fire once per
        # entry/exit from that state, not every bar it stays there.
        rollover_dates, touch_dates = [], []
        in_overbought = False
        for idx, val in rsi.iloc[start:].items():
            if val is None or val != val:
                continue
            if val >= RSI_OVERBOUGHT:
                if not in_overbought:
                    touch_dates.append(idx)
                in_overbought = True
            elif in_overbought:
                rollover_dates.append(idx)
                in_overbought = False
        out["rsi_overbought_rollover"] = rollover_dates
        out["rsi_overbought_touch"] = touch_dates
    else:
        out["rsi_overbought_rollover"] = []
        out["rsi_overbought_touch"] = []

    if macd_line is not None and macd_signal is not None:
        bearish_cross = (macd_line < macd_signal) & (macd_line.shift(1) >= macd_signal.shift(1))
        out["macd_bearish_cross"] = _all_after(bearish_cross, start)
    else:
        out["macd_bearish_cross"] = []

    if rs60 is not None:
        below_zero = (rs60 < 0) & (rs60.shift(1) >= 0)
        out["rs_below_zero"] = _all_after(below_zero, start)
    else:
        out["rs_below_zero"] = []

    below_20 = (close < sma20) & (close.shift(1) >= sma20.shift(1))
    out["price_below_20dma"] = _all_after(below_20, start)

    if ema50 is not None:
        below_50 = (close < ema50) & (close.shift(1) >= ema50.shift(1))
        out["price_below_50dma"] = _all_after(below_50, start)
        dma_cross = (sma20 < ema50) & (sma20.shift(1) >= ema50.shift(1))
        out["dma20_below_dma50"] = _all_after(dma_cross, start)
        extended = close >= ema50 * (1 + CHASE_EXTENSION_PCT / 100)
        extended_cross = extended & ~extended.shift(1).fillna(False)
        out["price_extended_above_50dma"] = _all_after(extended_cross, start)
    else:
        out["price_below_50dma"] = []
        out["dma20_below_dma50"] = []
        out["price_extended_above_50dma"] = []

    target_price = entry_price * (1 + MIN_REWARD_RISK_MULTIPLE * TARGET_TRIGGER_PCT / 100)
    target_cross = (close >= target_price) & (close.shift(1) < target_price)
    out["target_hit"] = _all_after(target_cross, start)

    adx = w.get("adx")
    if adx is not None:
        weakening = (adx < ADX_WEAK_TREND) & (adx.shift(1) >= ADX_WEAK_TREND)
        out["adx_trend_weakening"] = _all_after(weakening, start)
    else:
        out["adx_trend_weakening"] = []

    is_dist = w.get("is_distribution_day")
    out["distribution_day"] = _all_after(is_dist.fillna(False), start) if is_dist is not None else []

    vstop_up = w.get("vstop_uptrend")
    if vstop_up is not None:
        flip_down = (vstop_up == False) & (vstop_up.shift(1) == True)  # noqa: E712 -- vstop_up holds True/False/None, not a plain bool
        out["vstop_downtrend_flip"] = _all_after(flip_down.fillna(False), start)
    else:
        out["vstop_downtrend_flip"] = []

    atr_pct = w.get("atr_pct")
    if atr_pct is not None:
        atr_abs = atr_pct / 100 * close
        extended_atr = close >= sma20 + ATR_EXTENSION_MULTIPLE * atr_abs
        extended_atr_cross = extended_atr & ~extended_atr.shift(1).fillna(False)
        out["atr_extended_above_20dma"] = _all_after(extended_atr_cross, start)
    else:
        out["atr_extended_above_20dma"] = []

    for key, (_, pattern_fn) in candlestick_patterns.PATTERNS.items():
        out[key] = _all_after(pattern_fn(w).fillna(False), start)

    # Stage/trend-template flips: fresh transitions only (like the MACD/RSI
    # crossovers above), not every bar spent in the new state. Only scanned
    # from i_entry forward -- daily_base.stage/_trend_ok_classic are called
    # per-row, so this keeps the cost proportional to the holding period.
    stage_vals = [daily_base.stage(w, i) for i in range(i_entry, len(w))]
    trend_vals = [daily_base._trend_ok_classic(w, i) for i in range(i_entry, len(w))]
    dates_from_entry = w.index[i_entry:]
    stage4_dates, trend_broken_dates = [], []
    for i in range(1, len(stage_vals)):
        if stage_vals[i] == "Stage 4" and stage_vals[i - 1] != "Stage 4":
            stage4_dates.append(dates_from_entry[i])
        if not trend_vals[i] and trend_vals[i - 1]:
            trend_broken_dates.append(dates_from_entry[i])
    out["stage4_confirmed"] = stage4_dates
    out["trend_template_broken"] = trend_broken_dates

    levels = gann_fib_levels(w, i_entry, entry_price)
    if levels:
        stop_price = levels["stop"]["price"]
        stop_cross = (close <= stop_price) & (close.shift(1) > stop_price)
        out[levels["stop"]["key"]] = _all_after(stop_cross, start)
        for t in levels["targets"]:
            target_cross = (close >= t["price"]) & (close.shift(1) < t["price"])
            out[t["key"]] = _all_after(target_cross, start)
    else:
        out[GANN_STOP_KEY] = []
        for key, _, _, _ in GANN_EXTENSIONS:
            out[key] = []

    return out


def _occurrence(w, date, i_entry, entry_price, quantity, user_return_pct):
    price = round(float(w["close"].loc[date]), 2)
    i = w.index.get_loc(date)
    return_pct = round((price - entry_price) / entry_price * 100, 2)
    vs_pct = round(return_pct - user_return_pct, 2)
    return {
        "date": date, "price": price, "return_pct": return_pct,
        "days_after_entry": i - i_entry,
        "vs_your_return_pct": vs_pct,
        "vs_your_return_rupees": round(vs_pct / 100 * quantity * entry_price, 2),
    }


def portfolio_stats(diagnosed, symbol_frames):
    """Aggregate win rate across the WHOLE portfolio for each independent
    exit trigger: if every trade had been exited on that trigger's first
    firing after entry, what fraction of those trades would have been
    winners? Only trades where the trigger actually fired at least once
    count toward that trigger's stats -- this is a per-trigger backtest,
    comparable against the user's actual consolidated win rate
    (mirror_narrative.backtest_stats), not a per-trade table."""
    per_trigger = {key: {"label": label, "returns": []} for key, label in TRIGGERS}
    for d in diagnosed:
        w = symbol_frames.get(d["symbol"])
        if w is None:
            continue
        i_entry = w.index.searchsorted(d["entry_date"], side="right") - 1
        for row in evaluate_triggers(w, i_entry, d["entry_price"], d["quantity"], d["user_return_pct"]):
            if row["fired"]:
                per_trigger[row["key"]]["returns"].append(row["first"]["return_pct"])

    stats = {}
    for key, info in per_trigger.items():
        returns = info["returns"]
        n = len(returns)
        wins = sum(1 for r in returns if r > 0)
        stats[key] = {
            "label": info["label"], "n_trades": n,
            "win_rate": round(100 * wins / n, 1) if n else None,
            "avg_return_pct": round(sum(returns) / n, 1) if n else None,
        }
    return stats


def evaluate_triggers(w, i_entry, entry_price, quantity, user_return_pct):
    """Returns a list of dicts, one per trigger in TRIGGERS (in order),
    always present (fired=False, occurrences=[] rows included) so the
    comparison shows "never triggered" scenarios too, not just the ones
    that fired. Each dict's "occurrences" list has EVERY time that trigger
    fired between entry and the most recent bar -- "first" is a convenience
    alias for occurrences[0], kept for callers that only want the earliest."""
    raw = _detect(w, i_entry, entry_price)
    rows = []
    for key, label in TRIGGERS:
        dates = raw.get(key, [])
        occurrences = [_occurrence(w, d, i_entry, entry_price, quantity, user_return_pct) for d in dates]
        rows.append({
            "key": key, "label": label, "fired": bool(occurrences),
            "occurrences": occurrences,
            "first": occurrences[0] if occurrences else None,
            # Backward-compatible flat fields mirroring the first occurrence, for
            # any caller that only cares about "when did this first happen".
            "date": occurrences[0]["date"] if occurrences else None,
            "price": occurrences[0]["price"] if occurrences else None,
            "return_pct": occurrences[0]["return_pct"] if occurrences else None,
            "days_after_entry": occurrences[0]["days_after_entry"] if occurrences else None,
            "vs_your_return_pct": occurrences[0]["vs_your_return_pct"] if occurrences else None,
            "vs_your_return_rupees": occurrences[0]["vs_your_return_rupees"] if occurrences else None,
        })
    return rows


def combined_first_exit(w, i_entry, entry_price, quantity, user_return_pct, profit_key, stop_key):
    """Whichever of the chosen profit-taking or stop-loss trigger fired
    FIRST after entry -- the two-dropdown version of "if you'd exited on
    this instead". Returns that occurrence dict, or None if neither ever
    fired for this trade."""
    rows = {r["key"]: r for r in evaluate_triggers(w, i_entry, entry_price, quantity, user_return_pct)}
    candidates = [r["first"] for key in (profit_key, stop_key) if (r := rows.get(key)) and r["first"]]
    return min(candidates, key=lambda o: o["date"]) if candidates else None


def combined_portfolio_stats(diagnosed, symbol_frames, profit_key, stop_key):
    """Portfolio-wide win rate if every trade had been exited on whichever
    of the two chosen triggers (one profit, one stop) fired first -- same
    shape as one row of portfolio_stats(), for the two-dropdown comparison."""
    returns = []
    for d in diagnosed:
        w = symbol_frames.get(d["symbol"])
        if w is None:
            continue
        i_entry = w.index.searchsorted(d["entry_date"], side="right") - 1
        exit_occ = combined_first_exit(
            w, i_entry, d["entry_price"], d["quantity"], d["user_return_pct"], profit_key, stop_key
        )
        if exit_occ is not None:
            returns.append(exit_occ["return_pct"])
    n = len(returns)
    wins = sum(1 for r in returns if r > 0)
    return {
        "n_trades": n,
        "win_rate": round(100 * wins / n, 1) if n else None,
        "avg_return_pct": round(sum(returns) / n, 1) if n else None,
    }
