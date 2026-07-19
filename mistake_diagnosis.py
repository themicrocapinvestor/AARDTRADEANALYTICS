"""Scores each closed round-trip trade against daily_base.stage()/momentum_score(),
extra_indicators.score_at(), and relative_strength, evaluated at the trade's actual
entry/exit dates instead of "today". Also replays a systematic exit
(unified_backtest's own trailing-stop/target/max-holding rules) forward from the
same entry to get a counterfactual: what would have happened if discipline, not
feeling, had decided the exit.

Symbols that don't resolve to a plain NSE cash-equity instrument token are
skipped with a reported count -- the Minervini/Stage/base machinery is
equity-specific and would be noise applied to a gold ETF's price series.
"""
import datetime as dt

import pandas as pd

from daily_base import (
    compute_indicators, stage, momentum_score, TREND_SMA_SLOW, BASE_MIN_DAYS,
    MIN_RVOL_PCT, MOMENTUM_ADX_MIN,
)
from extra_indicators import compute_extra_indicators, RSI_BAND
from relative_strength import compute_rs_all, RS_LOOKBACK_SHORT_DAYS, RS_LOOKBACK_LONG_DAYS, RS_LOOKBACK_MOMENTUM_DAYS
from unified_backtest import (
    STOP_TRAIL_PCT, TARGET_TRIGGER_PCT, MIN_REWARD_RISK_MULTIPLE, MAX_HOLDING_DAYS,
    trail_stop_for,
)

MIN_HISTORY_DAYS = TREND_SMA_SLOW + BASE_MIN_DAYS  # same warm-up floor detect_daily_setup_at uses

# Mistake thresholds: reuses existing app constants where one already exists
# (STOP_TRAIL_PCT etc.) rather than inventing parallel ones.
CHASE_EXTENSION_PCT = 15.0      # entry more than this % above the 50-day SMA -- buying stretched, not at a base
PANIC_EXIT_MIN_GAP_PCT = 8.0    # user's realized return this many points below what the systematic
                                  # trailing-stop run had banked by the same exit date -- "left it on the table"
BAGHOLD_MIN_DAYS_IN_STAGE34 = 15  # calendar days held while Stage was 3/4 before the user's actual exit --
                                    # below this, "held a day too long" isn't a real pattern yet
CLUMSY_SCORE_TAG_MIN = 60.0     # Clumsy Score (see _condition_breakdown) above this at entry -- more than
                                  # half the 13 underlying good-trade conditions were absent -- earns its own tag

# Post-exit ("aftermath") thresholds: separate tag namespace (aftermath_tags,
# not tags) since these describe the stock's subsequent behavior, not a
# decision mistake -- a disciplined exit can still be followed by a big run.
POST_EXIT_CHECKPOINT_DAYS = (5, 10, 20, 30, 60, 90)
POST_EXIT_MIN_BARS_FOR_VERDICT = 5     # fewer bars than this after exit -- too recent to have a story yet
SOLD_TOO_EARLY_MIN_RUN_PCT = 15.0      # stock ran at least this much further past the user's exit price
EXIT_VINDICATED_MIN_DROP_PCT = 15.0    # stock fell at least this much below the user's exit price afterward
DEAD_MONEY_BAND_PCT = 5.0              # |30-trading-day post-exit return| below this -- exit was a non-event


def resolve_token(symbol, symbol_token_map, instruments):
    """Falls back to any NSE-segment row for the symbol (covers
    instrument_type values other than plain 'EQ', e.g. some ETFs)."""
    token = symbol_token_map.get(symbol)
    if token:
        return token
    for row in instruments:
        if row.get("segment") == "NSE" and row.get("tradingsymbol") == symbol:
            return row["instrument_token"]
    return None


def required_lookback_days(trades, today=None):
    """Has to cover the earliest trade's entry date plus warm-up room."""
    today = today or dt.date.today()
    if not trades:
        return MIN_HISTORY_DAYS + 30
    earliest_entry = min(t["entry_date"] for t in trades).date()
    span_days = (today - earliest_entry).days
    return span_days + MIN_HISTORY_DAYS + 30  # +30: small buffer for weekends/holidays in the warm-up window


def prepare_symbol_frame(daily, benchmark_daily):
    """Returns None if there isn't enough history."""
    if daily is None or len(daily) < MIN_HISTORY_DAYS:
        return None
    rs = compute_rs_all(daily, benchmark_daily)
    w = compute_indicators(
        daily,
        rs_60=rs[RS_LOOKBACK_SHORT_DAYS], rs_123=rs[RS_LOOKBACK_LONG_DAYS], rs_30=rs[RS_LOOKBACK_MOMENTUM_DAYS],
    )
    return compute_extra_indicators(w)


def _index_at_or_before(w, date):
    """A defensive nearest-prior lookup handles any date drift between the
    tradebook and Kite's daily bar calendar. Returns None if `date` is
    before the frame starts."""
    idx = w.index.searchsorted(date, side="right") - 1
    if idx < 0:
        return None
    return int(idx)


def _bool_or_none(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    return bool(val)


def _condition_breakdown(w, i, rs_60, rs_123):
    """The 13 individual pass/fail reads behind extra_indicators.score_at's
    8-condition Score and daily_base.momentum_score's 5-condition Momentum
    Score. clumsy_score is 100 - (hits / evaluable * 100) -- the share of
    evaluable conditions that were absent at entry, the opposite direction
    from the scores it's derived from."""
    row = w.iloc[i]
    stage_now = stage(w, i)
    rs_ok = bool(rs_60 is not None and rs_60 > 0)
    momentum = momentum_score(w, i)

    conditions = {
        # the composite score's 8 conditions (Minervini trend template + MACD/RSI/Stage/RS/Volume)
        "close_above_50d_ema": _bool_or_none(row.get("close") > row["ema50"] if not pd.isna(row.get("ema50")) else None),
        "ema50_above_ema150": _bool_or_none(row["ema50"] > row["ema150"] if not pd.isna(row.get("ema50")) and not pd.isna(row.get("ema150")) else None),
        "ema150_above_ema200": _bool_or_none(row["ema150"] > row["ema200"] if not pd.isna(row.get("ema150")) and not pd.isna(row.get("ema200")) else None),
        "macd_above_signal": _bool_or_none(row["macd_line"] > row["macd_signal"] if not pd.isna(row.get("macd_line")) else None),
        "rsi_in_healthy_band": _bool_or_none(RSI_BAND[0] <= row["rsi"] <= RSI_BAND[1] if not pd.isna(row.get("rsi")) else None),
        "stage_is_2": _bool_or_none(stage_now == "Stage 2" if stage_now is not None else None),
        "rs_positive": rs_ok if rs_60 is not None else None,
        "volume_above_20d_avg": _bool_or_none(row["volume"] > row["vol_sma20"] if not pd.isna(row.get("vol_sma20")) else None),
    }
    if momentum is not None:
        conditions.update({
            "45d_return_positive": momentum["return_pct"] > 0,
            "rvol_above_130pct": (momentum["rvol_pct"] or 0) > MIN_RVOL_PCT if momentum["rvol_pct"] is not None else None,
            "adx_trending": (momentum["adx"] or 0) > MOMENTUM_ADX_MIN if momentum["adx"] is not None else None,
            "obv_confirming_uptrend": momentum["obv_trend_up"],
            "rs_30d_positive": (momentum["rs_30_pct"] or 0) > 0 if momentum["rs_30_pct"] is not None else None,
        })

    evaluated = {k: v for k, v in conditions.items() if v is not None}
    clumsy_score = round(100 - (sum(evaluated.values()) / len(evaluated) * 100), 1) if evaluated else None
    return conditions, clumsy_score


def _entry_context(w, i):
    row = w.iloc[i]
    stage_now = stage(w, i)
    sma_fast = row.get("sma_fast")
    extension_pct = None
    if sma_fast is not None and not pd.isna(sma_fast) and sma_fast > 0:
        extension_pct = (row["close"] / sma_fast - 1) * 100
    rs_60 = row.get("rs_60")
    rs_123 = row.get("rs_123")
    rs_60 = float(rs_60) if rs_60 is not None and not pd.isna(rs_60) else None
    rs_123 = float(rs_123) if rs_123 is not None and not pd.isna(rs_123) else None
    conditions, clumsy_score = _condition_breakdown(w, i, rs_60, rs_123)
    return {
        "stage": stage_now,
        "conditions": conditions,
        "clumsy_score": clumsy_score,
        "extension_above_sma50_pct": round(float(extension_pct), 2) if extension_pct is not None else None,
        "rs_60_pct": round(rs_60, 4) if rs_60 is not None else None,
        "rs_123_pct": round(rs_123, 4) if rs_123 is not None else None,
        "atr_pct": round(float(row["atr_pct"]), 2) if not pd.isna(row.get("atr_pct")) else None,
    }


def _exit_context(w, i):
    row = w.iloc[i]
    return {"stage": stage(w, i), "close": round(float(row["close"]), 2)}


def _stage_run_length_before(w, i, target_stages=("Stage 3", "Stage 4")):
    """Trading-day count, not calendar days -- close enough for a threshold check."""
    n = 0
    j = i
    while j >= 0 and stage(w, j) in target_stages:
        n += 1
        j -= 1
    return n


def systematic_replay(w, i_entry, entry_price):
    """Uses unified_backtest's rules but the simple version (no pyramiding,
    no holding-extension), since this is a single-trade counterfactual, not
    a shared-capital simulation. Returns None if there isn't a next bar to
    walk from."""
    if i_entry + 1 >= len(w):
        return None
    highest_close = entry_price
    initial_stop = entry_price * (1 - STOP_TRAIL_PCT / 100)
    target_price = entry_price * (1 + MIN_REWARD_RISK_MULTIPLE * TARGET_TRIGGER_PCT / 100)
    trail_stop = initial_stop
    entry_date = w.index[i_entry]

    for i in range(i_entry + 1, len(w)):
        row = w.iloc[i]
        close = float(row["close"])
        if close > highest_close:
            highest_close = close
        trail_stop = max(trail_stop, trail_stop_for(entry_price, highest_close))
        days_held = (w.index[i] - entry_date).days

        if close < trail_stop:
            return _systematic_result(w, i, trail_stop, entry_price, f"Trail stop ({STOP_TRAIL_PCT:.0f}%)")
        if close >= target_price:
            target_pct = MIN_REWARD_RISK_MULTIPLE * TARGET_TRIGGER_PCT
            return _systematic_result(w, i, close, entry_price, f"Booked (>= {target_pct:.0f}% target)")
        if days_held >= MAX_HOLDING_DAYS:
            return _systematic_result(w, i, close, entry_price, f"Max holding period ({MAX_HOLDING_DAYS}d)")

    # Ran out of history without a triggered exit -- mark to the last bar.
    last = w.iloc[-1]
    return _systematic_result(w, len(w) - 1, float(last["close"]), entry_price, "Open (mark-to-last)")


def _systematic_result(w, i, exit_price, entry_price, reason):
    return {
        "exit_date": w.index[i],
        "exit_price": round(exit_price, 2),
        "reason": reason,
        "return_pct": round((exit_price - entry_price) / entry_price * 100, 2),
    }


def _aftermath_context(w, i_exit, exit_price):
    """What happened to the stock AFTER the user's own exit, independent of
    the systematic-replay comparison -- a disciplined exit can still be
    followed by a huge run or a crash, so this is tracked separately from
    the decision-quality `tags`. checkpoint offsets are trading-day counts,
    not calendar days. aftermath_tags: 'sold_too_early'/'exit_vindicated'
    can both fire together on a whipsaw."""
    last_idx = len(w) - 1
    bars_after = last_idx - i_exit
    checkpoints = {
        d: round((float(w["close"].iloc[i_exit + d]) - exit_price) / exit_price * 100, 2)
        for d in POST_EXIT_CHECKPOINT_DAYS if i_exit + d <= last_idx
    }
    if bars_after < POST_EXIT_MIN_BARS_FOR_VERDICT:
        return {"bars_after_exit": bars_after, "checkpoints": checkpoints,
                "max_run_pct": None, "max_drawdown_pct": None, "aftermath_tags": ["too_recent"],
                "peak": None, "trough": None, "trend_break": None}

    post_close = w["close"].iloc[i_exit + 1: last_idx + 1]
    peak_pos = int(post_close.values.argmax())
    trough_pos = int(post_close.values.argmin())
    peak_i, trough_i = i_exit + 1 + peak_pos, i_exit + 1 + trough_pos
    peak_price, trough_price = float(post_close.iloc[peak_pos]), float(post_close.iloc[trough_pos])
    max_run_pct = round((peak_price - exit_price) / exit_price * 100, 2)
    max_drawdown_pct = round((trough_price - exit_price) / exit_price * 100, 2)

    peak = {"date": w.index[peak_i], "price": round(peak_price, 2),
            "rsi": round(float(w["rsi"].iloc[peak_i]), 1) if "rsi" in w.columns and not pd.isna(w["rsi"].iloc[peak_i]) else None}
    trough = {"date": w.index[trough_i], "price": round(trough_price, 2)}

    # "The break": the first close after the post-exit peak that falls below
    # the stock's own trailing 20-day average -- a plain, chart-reader-legible
    # trend-loss signal (not an internal score condition, just a moving
    # average), used to answer "what would have been a good exit trigger"
    # concretely with a real date and price rather than a jargon score.
    sma20 = w["close"].rolling(20).mean()
    trend_break = None
    for j in range(peak_i + 1, len(w)):
        c, s = w["close"].iloc[j], sma20.iloc[j]
        if not pd.isna(s) and c < s:
            trend_break = {
                "date": w.index[j], "price": round(float(c), 2),
                "days_after_peak": j - peak_i,
                "pct_off_peak": round((float(c) - peak_price) / peak_price * 100, 2),
            }
            break

    aftermath_tags = []
    if max_run_pct >= SOLD_TOO_EARLY_MIN_RUN_PCT:
        aftermath_tags.append("sold_too_early")
    if max_drawdown_pct <= -EXIT_VINDICATED_MIN_DROP_PCT:
        aftermath_tags.append("exit_vindicated")
    checkpoint_30 = checkpoints.get(30)
    if not aftermath_tags and checkpoint_30 is not None and abs(checkpoint_30) < DEAD_MONEY_BAND_PCT:
        aftermath_tags.append("dead_money")
    if not aftermath_tags:
        aftermath_tags.append("no_clear_verdict")

    return {
        "bars_after_exit": bars_after, "checkpoints": checkpoints,
        "max_run_pct": max_run_pct, "max_drawdown_pct": max_drawdown_pct,
        "aftermath_tags": aftermath_tags,
        "peak": peak, "trough": trough, "trend_break": trend_break,
    }


def diagnose_trade(trade, w):
    """Returns None if the entry date falls before the frame has enough
    warm-up history. impact_rupees: positive = rupees left on the table vs.
    discipline, negative = the user's actual exit outperformed the
    systematic replay -- their instinct was right."""
    i_entry = _index_at_or_before(w, trade["entry_date"])
    i_exit = _index_at_or_before(w, trade["exit_date"])
    if i_entry is None or i_exit is None or i_entry < MIN_HISTORY_DAYS - 1:
        return None

    entry_ctx = _entry_context(w, i_entry)
    exit_ctx = _exit_context(w, i_exit)
    systematic = systematic_replay(w, i_entry, trade["entry_price"])

    tags = []

    if entry_ctx["extension_above_sma50_pct"] is not None and entry_ctx["extension_above_sma50_pct"] > CHASE_EXTENSION_PCT:
        tags.append("chased_extended")
    if entry_ctx["stage"] not in (None, "Stage 2"):
        tags.append("wrong_stage_entry")
    if entry_ctx["rs_60_pct"] is not None and entry_ctx["rs_60_pct"] <= 0:
        tags.append("weak_rs_entry")
    if entry_ctx["clumsy_score"] is not None and entry_ctx["clumsy_score"] >= CLUMSY_SCORE_TAG_MIN:
        tags.append("clumsy_entry")

    user_loss_pct = -trade["return_pct"] if trade["return_pct"] < 0 else 0
    if user_loss_pct > STOP_TRAIL_PCT + 3:  # a few points of slack over the systematic stop distance
        tags.append("no_stop_discipline")

    impact_rupees = 0.0
    if systematic is not None:
        delta_pct = systematic["return_pct"] - trade["return_pct"]
        impact_rupees = round(delta_pct / 100 * trade["quantity"] * trade["entry_price"], 2)
        # Only call it "panic exit" if the user got out EARLY (before the systematic
        # exit date) leaving a real gap on the table -- a late exit that also
        # underperformed is a different mistake (bag-holding), not fear.
        if (trade["exit_date"] < systematic["exit_date"] and delta_pct > PANIC_EXIT_MIN_GAP_PCT
                and "Trail stop" not in (systematic["reason"] or "")):
            tags.append("panic_exit")

    stage34_run = _stage_run_length_before(w, i_exit)
    if exit_ctx["stage"] in ("Stage 3", "Stage 4") and stage34_run >= BAGHOLD_MIN_DAYS_IN_STAGE34:
        tags.append("bagheld_past_breakdown")

    if not tags:
        tags.append("clean")

    aftermath = _aftermath_context(w, i_exit, trade["exit_price"])

    return {
        "symbol": trade["symbol"],
        "entry_date": trade["entry_date"], "exit_date": trade["exit_date"],
        "entry_price": trade["entry_price"], "exit_price": trade["exit_price"],
        "quantity": trade["quantity"], "user_return_pct": trade["return_pct"],
        "pnl_rupees": trade["pnl_rupees"], "days_in_trade": trade["days_in_trade"],
        "entry_context": entry_ctx, "exit_context": exit_ctx,
        "systematic": systematic, "tags": tags, "impact_rupees": impact_rupees,
        "aftermath": aftermath,
    }


def diagnose_all(trades, symbol_frames):
    """Returns (diagnosed, skipped) -- skipped trades are reported rather
    than silently dropped."""
    diagnosed, skipped = [], []
    for trade in trades:
        w = symbol_frames.get(trade["symbol"])
        if w is None:
            skipped.append((trade["symbol"], "no candle history available"))
            continue
        result = diagnose_trade(trade, w)
        if result is None:
            skipped.append((trade["symbol"], "entry date predates available warm-up history"))
            continue
        diagnosed.append(result)
    return diagnosed, skipped
