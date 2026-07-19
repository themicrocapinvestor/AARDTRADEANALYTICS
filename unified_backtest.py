"""One engine for both "backtest" and "portfolio simulation" -- daily-cadence
sibling of kite-weekly-screener's unified_backtest.py, same trade-management
rules, just walking daily bars instead of weekly ones.

Built around a simple, single-trader workflow: scan daily, take at most
MAX_TRADES_PER_WEEK new positions a week, size every position at a flat
POSITION_SIZE_PCT_DEFAULT % of current equity (no pyramiding -- one fill,
full size, at entry), and manage the exit with three rules:

  - **Trailing stop**: 15% below the highest daily close seen since entry
    (STOP_TRAIL_PCT). A ratchet -- it only ever moves up, never down.
    Breakeven protection falls out of it automatically once a stock is up
    more than STOP_TRAIL_PCT% (at that point the trail is already at or
    above the entry price). No slippage haircut is applied on top of the
    stop level (STOP_SLIPPAGE_PCT=0) -- a close-triggered stop order on
    liquid NSE names doesn't reliably lose several extra percent beyond the
    stop price itself, so charging that was overstating every stop-out.
  - **Profit target**: a fixed 30% price move (TARGET_TRIGGER_PCT x
    MIN_REWARD_RISK_MULTIPLE), deliberately NOT derived from STOP_TRAIL_PCT
    -- widening the trailing stop for more breathing room shouldn't also
    force a bigger move before profit gets booked. Since the actual risk
    unit (STOP_TRAIL_PCT=15%) no longer matches the fixed 30% target
    distance, this exit is labelled by its price move ("Booked (>= 30%
    target)"), not as "3R", since it no longer reliably prices out to
    exactly 3 x the real risk being taken.
  - **Max holding period**: MAX_HOLDING_DAYS (90) calendar days after entry,
    a position still open is force-closed at that day's close, regardless
    of the trailing stop or target -- capital stuck sideways gets freed up
    rather than tying up a slot indefinitely.

Entry happens at the next day's open after a BREAKOUT signal on the prior
day's close (see daily_base.detect_daily_setup_at). Same-day candidates are
still ranked and capped at the top extra_indicators.TOP_PICKS_MAX by Score
(the live Scanner's own "Top Picks" ranking), but a second, calendar-week
cap (MAX_TRADES_PER_WEEK) now also limits how many NEW positions can open
across an entire week regardless of how many days signal -- once 3 have
been taken in the current ISO week, no more open until the next one. Still
requires the RS-vs-NIFTY-500 filter and a minimum reward:risk at signal
time (both unchanged from the live scanner). A flat TRANSACTION_COST_PCT
is deducted on both the entry fill and every exit fill, approximating
STT/brokerage/exchange charges.
"""
import datetime as dt
import json
import os

import pandas as pd

from daily_base import (
    detect_daily_setup_at, compute_indicators, momentum_score,
    TREND_SMA_SLOW, BASE_MIN_DAYS,
)
from relative_strength import compute_rs_all, compute_beta, RS_LOOKBACK_SHORT_DAYS, RS_LOOKBACK_LONG_DAYS, RS_LOOKBACK_MOMENTUM_DAYS
from extra_indicators import compute_extra_indicators, score_at, top_picks, TOP_PICKS_MAX

STARTING_CAPITAL_DEFAULT = 1_000_000
MIN_DAYS_FOR_SIGNAL = TREND_SMA_SLOW + BASE_MIN_DAYS

STOP_TRAIL_PCT = 15.0             # both the initial stop distance and the trailing-stop cushion off peak
STOP_SLIPPAGE_PCT = 0.0           # no extra haircut below the trail stop level once it's breached -- was 5.0,
                                    # removed as unrealistically punitive for liquid NSE names (see docstring)
TARGET_TRIGGER_PCT = 10.0         # fixed reference distance for the profit target -- intentionally NOT
                                    # STOP_TRAIL_PCT, so widening the trailing stop doesn't also widen the target
MIN_REWARD_RISK_MULTIPLE = 3.0    # full-exit target: entry x (1 + this x TARGET_TRIGGER_PCT/100)
MIN_REWARD_RISK = 3.0             # entry filter -- base_breakout candidates need >= this at signal time

POSITION_SIZE_PCT_DEFAULT = 3.0   # flat % of current equity deployed per trade, single fill at entry --
POSITION_SIZE_PCT_MIN = 1.0       # no pyramiding/adds; a trader taking at most MAX_TRADES_PER_WEEK
POSITION_SIZE_PCT_MAX = 5.0       # entries/week at this size naturally caps total capital deployed
                                    # without needing a separate hard concurrent-position ceiling

MAX_TRADES_PER_WEEK = 3            # across the whole shared-capital simulation, at most this many NEW
                                     # positions may open in any single ISO calendar week, regardless of
                                     # how many candidates signal -- on top of (not instead of) the
                                     # existing top-TOP_PICKS_MAX-per-day Score ranking
TRANSACTION_COST_PCT = 0.1         # flat round-number approximation of STT + brokerage + exchange/DP
                                     # charges, deducted on every fill (entry and exit) -- not a precise
                                     # brokerage model, just enough to stop expectancy being cost-free
MIN_SCORE = 80.0               # candidates below this Score are never eligible, even if they'd
                                     # otherwise be a top-4 pick for the day -- a weak day now yields
                                     # fewer than 4 trades rather than forcing 4 mediocre ones
                                     # (the RVol > 130% entry filter lives in daily_base.exclusions'
                                     # -- applies automatically here too since this module's
                                     # candidate scan calls detect_daily_setup_at, same as the live Scanner)
MAX_HOLDING_DAYS = 90                # force-close any position still open this long after entry, at that
                                     # day's close -- caught neither by the trailing stop nor the profit
                                     # target, i.e. capital stuck going sideways rather than working --
                                     # UNLESS the extension rule below grants it one more stretch
HOLDING_EXTENSION_DAYS = 30          # if a position hits MAX_HOLDING_DAYS still showing strength (below),
                                     # it gets this many extra days before being force-closed regardless
HOLDING_EXTENSION_MIN_SCORE = 75.0   # extension is granted once, only if BOTH Score AND Momentum
                                     # Score are >= this at the moment MAX_HOLDING_DAYS is hit -- "still
                                     # working" rather than just "hasn't been stopped out yet"


def format_fills(fills):
    """Human-readable one-line summary of a position's exit fills."""
    return "; ".join(
        f"{f['shares']:.0f} sh @ {round(f['price'], 2)} ({f['reason']})" for f in fills
    )


def trail_stop_for(entry_price, highest_close_since_entry):
    """The shared trailing-stop formula, also used by app.py's Dashboard
    to show a live "Trail Stop (10%)" line for a logged entry -- kept here so
    both places compute it identically."""
    peak = max(entry_price, highest_close_since_entry)
    return peak * (1 - STOP_TRAIL_PCT / 100)


def _load_symbol_data(cache_dir, symbols, benchmark_daily):
    """Per symbol: daily indicator frame (daily_base.compute_indicators,
    further augmented with extra_indicators.compute_extra_indicators so the score
    Score can be evaluated at any date during the walk). No resampling --
    the cached bars are already daily."""
    data = {}
    if not os.path.isdir(cache_dir):
        return data
    for fn in os.listdir(cache_dir):
        if not fn.endswith(".json"):
            continue
        symbol = fn[:-5]
        if symbols is not None and symbol not in symbols:
            continue
        with open(os.path.join(cache_dir, fn)) as f:
            rows = json.load(f)
        if not rows:
            continue
        daily = pd.DataFrame(rows)
        daily["date"] = pd.to_datetime(daily["date"]).dt.tz_localize(None)
        daily.set_index("date", inplace=True)
        if len(daily) < MIN_DAYS_FOR_SIGNAL + 10:
            continue
        rs = compute_rs_all(daily, benchmark_daily) if benchmark_daily is not None else {
            RS_LOOKBACK_SHORT_DAYS: None, RS_LOOKBACK_LONG_DAYS: None, RS_LOOKBACK_MOMENTUM_DAYS: None,
        }
        rs_60_series, rs_123_series, rs_30_series = (
            rs[RS_LOOKBACK_SHORT_DAYS], rs[RS_LOOKBACK_LONG_DAYS], rs[RS_LOOKBACK_MOMENTUM_DAYS],
        )
        beta_series = compute_beta(daily, benchmark_daily) if benchmark_daily is not None else None
        w_ind = compute_indicators(daily, rs_60=rs_60_series, rs_123=rs_123_series, rs_30=rs_30_series)
        data[symbol] = compute_extra_indicators(w_ind, beta=beta_series)
    return data


def _open_position(symbol, i, date, entry_price, sig, position_size_pct, equity_now):
    """Single fill, full size, no pyramiding: shares are sized to deploy
    position_size_pct% of current equity at entry_price, full stop. The stop
    distance (STOP_TRAIL_PCT) and reward:risk target are still computed off
    the raw entry_price -- those are price-structure levels, not a function
    of position size. average_entry is grossed up by TRANSACTION_COST_PCT to
    fold entry costs into every downstream P&L calc without extra bookkeeping."""
    initial_stop = entry_price * (1 - STOP_TRAIL_PCT / 100)
    shares = round(position_size_pct / 100 * equity_now / entry_price)
    if shares <= 0:
        return None, 0

    cost_basis_price = entry_price * (1 + TRANSACTION_COST_PCT / 100)
    cost = shares * cost_basis_price
    risk_rupees = shares * (entry_price - initial_stop)
    return {
        "symbol": symbol, "entry_date": date, "entry_price": entry_price,
        "entry_idx": i, "initial_stop": initial_stop,
        "original_risk_rupees": risk_rupees,
        "total_bought_shares": shares, "remaining_shares": shares,
        "average_entry": cost_basis_price,
        "fills_in": [(date, entry_price, shares)], "fills_out": [],
        "trail_stop": initial_stop, "highest_price": entry_price,
        "last_close": entry_price, "holding_extended": False,
        "level": sig.get("level"),
        "signal_date": sig["as_of"], "entry_type": sig.get("entry_type", "base_breakout"),
        "rs_60_pct_at_entry": sig.get("rs_60_pct"), "rs_123_pct_at_entry": sig.get("rs_123_pct"),
    }, cost


def _process_exits(state, w, i, date):
    """Trigger stays on the daily CLOSE (not the intraday low/high) -- the
    weekly sibling app found low-based triggering wildly oversensitive to
    ordinary bar-to-bar noise, stopping out ~a third of all trades within
    their very first bar; the same reasoning applies here. What DOES change
    with OHLC: once a close-based stop-out fires, the fill price is the
    trail stop level itself minus a fixed STOP_SLIPPAGE_PCT, not the day's
    close -- a stock that gaps down hard would otherwise get "filled" at
    that ugly close (which can be far below the actual stop on a bad gap
    day), overstating the loss a live stop-loss order would have actually
    taken."""
    row = w.iloc[i]
    close = float(row["close"])
    state["last_close"] = close

    if close > state["highest_price"]:
        state["highest_price"] = close
    state["trail_stop"] = max(state["trail_stop"], trail_stop_for(state["entry_price"], state["highest_price"]))

    target_price = state["entry_price"] * (1 + MIN_REWARD_RISK_MULTIPLE * TARGET_TRIGGER_PCT / 100)

    days_held = (date - state["entry_date"]).days

    sold_shares, reason, closing, fill_price = 0, None, False, None
    if close < state["trail_stop"]:
        fill_price = state["trail_stop"] * (1 - STOP_SLIPPAGE_PCT / 100)
        sold_shares, reason, closing = state["remaining_shares"], f"Trail stop ({STOP_TRAIL_PCT:.0f}%)", True
    elif close >= target_price:
        fill_price = close
        target_pct = MIN_REWARD_RISK_MULTIPLE * TARGET_TRIGGER_PCT
        sold_shares, reason, closing = state["remaining_shares"], f"Booked (>= {target_pct:.0f}% target)", True
    elif days_held >= MAX_HOLDING_DAYS and not state["holding_extended"]:
        # First time hitting the max-holding boundary -- grant one 30-day
        # extension if the position is still showing strength (both scores
        # >= HOLDING_EXTENSION_MIN_SCORE), otherwise force-close as before.
        score_info = score_at(w, i)
        momentum = momentum_score(w, i)
        composite_pct = score_info["pct"] if score_info else None
        momentum_pct = momentum.get("score_pct") if momentum else None
        if (composite_pct is not None and composite_pct >= HOLDING_EXTENSION_MIN_SCORE
                and momentum_pct is not None and momentum_pct >= HOLDING_EXTENSION_MIN_SCORE):
            state["holding_extended"] = True
        else:
            fill_price = close
            sold_shares, reason, closing = state["remaining_shares"], f"Max holding period ({MAX_HOLDING_DAYS}d)", True
    elif state["holding_extended"] and days_held >= MAX_HOLDING_DAYS + HOLDING_EXTENSION_DAYS:
        fill_price = close
        extended_days = MAX_HOLDING_DAYS + HOLDING_EXTENSION_DAYS
        sold_shares, reason, closing = state["remaining_shares"], f"Max holding period (extended, {extended_days}d)", True

    proceeds = 0.0
    if sold_shares > 0:
        # Net of TRANSACTION_COST_PCT -- this is what the trader actually
        # receives per share, and average_entry (see _open_position) was
        # grossed up the same way, so pnl/R-multiple math downstream just
        # works on these two numbers without any separate cost line item.
        net_fill_price = fill_price * (1 - TRANSACTION_COST_PCT / 100)
        proceeds = sold_shares * net_fill_price
        state["remaining_shares"] -= sold_shares
        state["fills_out"].append({"date": date, "price": net_fill_price, "shares": sold_shares, "reason": reason})

    return proceeds, closing


def _finalize_trade(state, exit_date, open_at_end=False):
    fills_in = state["fills_in"]
    fills_out = state["fills_out"]
    total_shares = state["total_bought_shares"]
    average_entry = state["average_entry"]
    capital_deployed = sum(p * s for _, p, s in fills_in)

    last_fill = fills_out[-1] if fills_out else None
    weighted_exit = (sum(f["price"] * f["shares"] for f in fills_out) / total_shares) if fills_out and total_shares else None

    pnl_rupees = sum(f["shares"] * (f["price"] - average_entry) for f in fills_out)
    total_return_pct = round((weighted_exit - average_entry) / average_entry * 100, 2) if weighted_exit else None
    r_multiple = round(pnl_rupees / state["original_risk_rupees"], 2) if state["original_risk_rupees"] else None
    days_in_trade = (exit_date - state["entry_date"]).days

    return {
        "symbol": state["symbol"],
        "entry_date": state["entry_date"].date().isoformat(),
        "entry_price": round(state["entry_price"], 2),
        "initial_stop": round(state["initial_stop"], 2),
        "average_entry": round(average_entry, 2),
        "exit_date": exit_date.date().isoformat(),
        "exit_price": round(last_fill["price"], 2) if last_fill else None,
        "exit_reason": "Open (mark-to-last)" if open_at_end else (last_fill["reason"] if last_fill else None),
        "days_in_trade": days_in_trade,
        "total_return_pct": total_return_pct,
        "r_multiple": r_multiple,
        "outcome": "Open (mark-to-last)" if open_at_end else ("WIN" if (total_return_pct or 0) > 0 else "LOSS"),
        "capital_deployed": round(capital_deployed, 2),
        "rs_filter_pass": "Yes",
        "score_pct": state.get("score_pct"),
        "entry_type": state.get("entry_type"),
        "signal_date": state.get("signal_date"),
        "fills": format_fills(fills_out),
    }


def summarize(trade_log, starting_capital, equity_curve):
    """n counts every trade (closed or still-open). win_rate/expectancy/avg_r
    are computed over CLOSED trades only ("WIN"/"LOSS" outcomes) -- a
    still-open position hasn't been given the chance to hit either exit
    condition yet, so counting it as an automatic non-win would understate
    the real win rate (an open position sitting on a gain is not a loss)."""
    n = len(trade_log)
    if n == 0:
        return {
            "n": 0, "n_closed": 0, "n_open": 0, "win_rate": 0, "avg_days_in_trade": 0,
            "avg_r_winners": None, "avg_r_losers": None,
            "expectancy": 0, "max_drawdown_pct": 0, "total_return_pct": 0,
            "starting_capital": starting_capital, "ending_equity": starting_capital,
        }
    closed = [t for t in trade_log if t["outcome"] in ("WIN", "LOSS")]
    n_closed = len(closed)
    wins = [t for t in closed if t["outcome"] == "WIN"]
    losses = [t for t in closed if t["outcome"] == "LOSS"]
    win_rate = round(100 * len(wins) / n_closed, 1) if n_closed else 0
    avg_days = round(sum(t["days_in_trade"] for t in trade_log) / n, 1)
    avg_r_win = round(sum(t["r_multiple"] for t in wins if t["r_multiple"] is not None) / len(wins), 2) if wins else None
    avg_r_loss = round(sum(t["r_multiple"] for t in losses if t["r_multiple"] is not None) / len(losses), 2) if losses else None
    win_frac = len(wins) / n_closed if n_closed else 0
    expectancy = round(win_frac * (avg_r_win or 0) + (1 - win_frac) * (avg_r_loss or 0), 2) if n_closed else 0

    peak, max_dd = float("-inf"), 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)

    ending_equity = equity_curve[-1][1] if equity_curve else starting_capital
    total_return_pct = round((ending_equity - starting_capital) / starting_capital * 100, 2) if starting_capital else 0

    return {
        "n": n, "n_closed": n_closed, "n_open": n - n_closed,
        "win_rate": win_rate, "avg_days_in_trade": avg_days,
        "avg_r_winners": avg_r_win, "avg_r_losers": avg_r_loss, "expectancy": expectancy,
        "max_drawdown_pct": round(max_dd, 2), "total_return_pct": total_return_pct,
        "starting_capital": starting_capital, "ending_equity": round(ending_equity, 2),
    }


def run_backtest(cache_dir, symbols=None, benchmark_daily=None,
                  starting_capital=STARTING_CAPITAL_DEFAULT, position_size_pct=POSITION_SIZE_PCT_DEFAULT,
                  on_progress=None):
    """Runs the unified daily-cadence, capped-selection simulation over every
    cached stock together against one shared capital pool. Every entry is a
    single fill sized at position_size_pct% of equity (no pyramiding), and
    at most MAX_TRADES_PER_WEEK new positions may open in any one ISO
    calendar week, on top of the existing top-TOP_PICKS_MAX-per-day Score
    Score ranking. Returns (summary_dict, trade_log) -- trade_log has one
    dict per closed (or still-open, marked-to-last) position with the full
    column set described in unified_backtest's module docstring / the app's
    Backtest tab.

    on_progress: optional callback(i, total) invoked once per date in the
    chronological walk -- lets the caller drive a progress bar and signal an
    early stop by returning True (see candle_kite/app.py's Stop-button
    pattern elsewhere in the app)."""
    daily_data = _load_symbol_data(cache_dir, symbols, benchmark_daily)
    if not daily_data:
        return summarize([], starting_capital, []), []

    all_dates = sorted(set(d for w in daily_data.values() for d in w.index))

    cash = starting_capital
    open_positions = {}
    trade_log = []
    trades_skipped_no_capital = 0
    trades_skipped_weekly_cap = 0
    equity_curve = []
    trades_this_week = {}  # (iso_year, iso_week) -> count of NEW positions opened

    for i, date in enumerate(all_dates):
        if on_progress is not None and on_progress(i, len(all_dates)):
            break

        # 1. Exits for every currently open position with a bar this date.
        for symbol in list(open_positions.keys()):
            w = daily_data[symbol]
            if date not in w.index:
                continue
            state = open_positions[symbol]
            idx = w.index.get_loc(date)
            proceeds, fully_closed = _process_exits(state, w, idx, date)
            cash += proceeds
            if fully_closed:
                trade_log.append(_finalize_trade(state, date))
                del open_positions[symbol]

        # 2. New candidates -> composite-score rank -> cap at TOP_PICKS_MAX.
        candidates = []
        for symbol, w in daily_data.items():
            if symbol in open_positions or date not in w.index:
                continue
            idx = w.index.get_loc(date)
            if idx < MIN_DAYS_FOR_SIGNAL:
                continue
            sig = detect_daily_setup_at(w, idx - 1, symbol=symbol)
            if not sig or sig["status"] != "BREAKOUT":
                continue
            entry_price = float(w.iloc[idx]["open"])
            level = sig.get("level")
            base_low_sig = sig.get("base_low")
            if sig.get("entry_type", "base_breakout") == "base_breakout" and level is not None and base_low_sig is not None:
                risk = entry_price - entry_price * (1 - STOP_TRAIL_PCT / 100)
                target = level + (level - base_low_sig)
                reward = target - entry_price
                if risk <= 0 or reward / risk < MIN_REWARD_RISK:
                    continue
            momentum = sig.get("momentum")  # detect_daily_setup_at already computed this at idx-1 --
                                              # reuse it instead of calling momentum_score() a second time
            score_info = score_at(w, idx - 1) if idx > 0 else None
            candidates.append({
                "symbol": symbol, "idx": idx, "entry_price": entry_price, "sig": sig,
                "stage": sig.get("stage"), "rs_60_pct": sig.get("rs_60_pct"),
                "rs_123_pct": sig.get("rs_123_pct"), "rvol_pct": sig.get("rvol_pct"),
                "momentum_return_pct": momentum["return_pct"] if momentum else None,
                "momentum_score_pct": momentum["score_pct"] if momentum else None,
                "score_pct": score_info["pct"] if score_info else None,
                "shakeout": sig.get("shakeout"), "gap_support": sig.get("gap_support"),
                "resistance_touches": sig.get("resistance_touches"),
            })

        scores = top_picks(candidates, top_n=TOP_PICKS_MAX, min_score=MIN_SCORE)
        ranked = sorted((c for c in candidates if c["symbol"] in scores), key=lambda c: -scores[c["symbol"]])

        week_key = date.isocalendar()[:2]  # (ISO year, ISO week) -- MAX_TRADES_PER_WEEK caps NEW entries per key
        for c in ranked:
            if trades_this_week.get(week_key, 0) >= MAX_TRADES_PER_WEEK:
                trades_skipped_weekly_cap += 1
                continue
            symbol, idx, entry_price, sig = c["symbol"], c["idx"], c["entry_price"], c["sig"]
            equity_now = cash + sum(
                s["remaining_shares"] * s["last_close"] for s in open_positions.values()
            )
            state, cost = _open_position(symbol, idx, date, entry_price, sig, position_size_pct, equity_now)
            if state is None:
                continue
            if cost > cash:
                trades_skipped_no_capital += 1
                continue
            state["score_pct"] = scores.get(symbol)
            cash -= cost
            open_positions[symbol] = state
            trades_this_week[week_key] = trades_this_week.get(week_key, 0) + 1

        equity_now = cash + sum(s["remaining_shares"] * s["last_close"] for s in open_positions.values())
        equity_curve.append((date, equity_now))

    # End of data: mark any still-open positions to their last known close.
    for symbol, state in open_positions.items():
        w = daily_data[symbol]
        last_close = float(w.iloc[-1]["close"])
        last_date = w.index[-1]
        cash += state["remaining_shares"] * last_close
        state["fills_out"].append({"date": last_date, "price": last_close,
                                    "shares": state["remaining_shares"], "reason": "Open (mark-to-last)"})
        state["remaining_shares"] = 0
        trade_log.append(_finalize_trade(state, last_date, open_at_end=True))

    summary = summarize(trade_log, starting_capital, equity_curve)
    summary["trades_skipped_no_capital"] = trades_skipped_no_capital
    summary["trades_skipped_weekly_cap"] = trades_skipped_weekly_cap
    return summary, trade_log
