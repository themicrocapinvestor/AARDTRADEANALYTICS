"""Composite technical score -- 8 equal-weighted (1/8 each) binary conditions,
all evaluated on the same daily bar, folding trend/momentum/RS/volume into
one 0-100 read alongside the individual indicator columns (EMAs, MACD, RSI,
volume MA, 252-day low/high, ATR-based stop lines, a volatility stop) used
throughout this app.

Conditions 1-3 and 6 use the Minervini Trend Template's three moving
averages, at their literal Daily 50/150/200-day EMA periods (see
EMA_MED/EMA_LONG/EMA_XLONG below):
  1. Close > 50-day EMA (Minervini)
  2. 50-day EMA > 150-day EMA (Minervini)
  3. 150-day EMA > 200-day EMA (Minervini)
  4. MACD(12,26,9) line > signal
  5. RSI(14) in [50, 65]
  6. Stage Analysis == "Stage 2" (daily_base.stage()'s SMA50/150/200 + RS
     lens -- the same canonical Stage read used everywhere else in this
     app, so the entry read and the score can't quietly disagree about
     Stage; a close SMA-based sibling of conditions 1-3's EMA-based stack,
     with RS and each MA's own slope added as extra bars)
  7. RS (60-day vs Nifty 500) > 0
  8. Volume > 20-day volume SMA

Score = round(hits / 8 * 100).

Golden Setup: Stage 2 AND Score > 70 AND the volatility stop is in an
uptrend AND the Minervini Trend Template is met AND RS > 0 -- five strong
reads at once, not just a high score.
"""
import numpy as np
import pandas as pd

TOP_PICKS_MAX = 4

# Minervini Trend Template MAs -- literal Daily 50/150/200-day EMA periods.
EMA_MED = 50
EMA_LONG = 150
EMA_XLONG = 200
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
RSI_LEN = 14
RSI_BAND = (50.0, 65.0)
VOL_MA_LEN = 20
ATR_LEN_VSTOP = 20
VSTOP_FACTOR = 2.0
ATR_LEN_STOPLINE = 14
ATR_STOPLINE_MULT = 2.0
MINERVINI_52W_LOW_BUFFER_PCT = 25.0
MINERVINI_52W_HIGH_BUFFER_PCT = 25.0
MINERVINI_EMA200_SLOPE_DAYS = 20  # daily_base's STAGE_SLOPE_LOOKBACK_DAYS -- same ~1-month slope window
GOLDEN_SETUP_SCORE_MIN = 70

TOTAL_CONDITIONS = 8


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _rma(s, n):
    """Wilder's smoothing -- what TradingView's ta.rsi actually uses under the
    hood, not a plain SMA of gains/losses."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def _rsi(close, n=RSI_LEN):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = _rma(gain, n)
    avg_loss = _rma(loss, n)
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


def _atr_abs(w, n):
    tr = pd.concat([
        w["high"] - w["low"],
        (w["high"] - w["close"].shift(1)).abs(),
        (w["low"] - w["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _volatility_stop(close, atr, factor=VSTOP_FACTOR):
    """Chandelier-style volatility stop with trend-flip reset. Returns
    (stop, uptrend) Series aligned to close's index. Stateful/sequential by
    construction (each bar's stop depends on the previous bar's regime), so
    this loops rather than vectorizes -- fine at daily-bar row counts (a few
    thousand per symbol)."""
    n = len(close)
    stop = np.full(n, np.nan)
    uptrend = np.zeros(n, dtype=bool)
    if n == 0:
        return pd.Series(stop, index=close.index), pd.Series(uptrend, index=close.index)

    max_val = min_val = float(close.iloc[0])
    cur_stop = float(close.iloc[0])
    cur_uptrend = True
    for i in range(n):
        px = float(close.iloc[i])
        a = atr.iloc[i]
        atrm = float(a) if not pd.isna(a) else 0.0
        max_val = max(max_val, px)
        min_val = min(min_val, px)
        cur_stop = max(cur_stop, max_val - atrm) if cur_uptrend else min(cur_stop, min_val + atrm)
        new_uptrend = (px - cur_stop) >= 0.0
        if new_uptrend != cur_uptrend and i > 0:
            max_val = min_val = px
            cur_stop = max_val - atrm if new_uptrend else min_val + atrm
        cur_uptrend = new_uptrend
        stop[i] = cur_stop
        uptrend[i] = cur_uptrend
    return pd.Series(stop, index=close.index), pd.Series(uptrend, index=close.index)


def compute_extra_indicators(w, beta=None):
    """w: the daily indicator DataFrame from daily_base.compute_indicators
    (must already have 'rolling_high_252d', and 'rs_60' if RS-dependent
    conditions are to work). beta: optional Series from
    relative_strength.compute_beta, aligned onto w's index. Returns a copy of
    w with the additional columns this module needs -- EMAs, MACD, RSI,
    volume MA, 252-day low, ATR-based stop lines, volatility stop, and beta."""
    w = w.copy()
    w["ema50"] = _ema(w["close"], EMA_MED)
    w["ema150"] = _ema(w["close"], EMA_LONG)
    w["ema200"] = _ema(w["close"], EMA_XLONG)

    macd_line = _ema(w["close"], MACD_FAST) - _ema(w["close"], MACD_SLOW)
    w["macd_line"] = macd_line
    w["macd_signal"] = _ema(macd_line, MACD_SIGNAL)

    w["rsi"] = _rsi(w["close"], RSI_LEN)
    w["vol_sma20"] = w["volume"].rolling(VOL_MA_LEN).mean()
    w["low_252d"] = w["low"].rolling(252, min_periods=50).min()

    w["atr20_abs"] = _atr_abs(w, ATR_LEN_VSTOP)
    w["atr_stopline"] = w["close"] - ATR_STOPLINE_MULT * _atr_abs(w, ATR_LEN_STOPLINE)
    vstop, vstop_up = _volatility_stop(w["close"], w["atr20_abs"])
    w["vstop"] = vstop
    w["vstop_uptrend"] = vstop_up

    w["beta"] = beta.reindex(w.index) if beta is not None else np.nan
    return w


def supertrend(w, atr_period=10, mult=3.0):
    """Standard ATR-band flip indicator on the daily frame. Returns
    (line, direction) Series aligned to w's index -- direction is True while
    the trend is up (line sits below price, acting as a trailing stop),
    False while down (line sits above price, acting as a trailing cap).
    Stateful/sequential by construction, same pattern as _volatility_stop."""
    atr = _atr_abs(w, atr_period)
    hl2 = (w["high"] + w["low"]) / 2
    upper_band = hl2 + mult * atr
    lower_band = hl2 - mult * atr

    n = len(w)
    line = np.full(n, np.nan)
    direction = np.zeros(n, dtype=bool)
    if n == 0:
        return pd.Series(line, index=w.index), pd.Series(direction, index=w.index)

    close = w["close"]
    final_upper = final_lower = None
    started = False
    cur_up = True
    for i in range(n):
        ub, lb, px = upper_band.iloc[i], lower_band.iloc[i], float(close.iloc[i])
        if pd.isna(ub) or pd.isna(lb):
            line[i] = np.nan
            direction[i] = cur_up
            continue
        if not started:
            # First bar with a real ATR reading -- bootstrap the bands here
            # rather than at i==0, where atr (and so ub/lb) is still NaN
            # during the warm-up window; seeding from a NaN would poison
            # every subsequent max()/min() comparison forever.
            final_upper, final_lower = float(ub), float(lb)
            started = True
        else:
            prev_close = float(close.iloc[i - 1])
            final_upper = ub if (ub < final_upper or prev_close > final_upper) else final_upper
            final_lower = lb if (lb > final_lower or prev_close < final_lower) else final_lower
        if cur_up:
            cur_up = px >= final_lower
        else:
            cur_up = px > final_upper
        line[i] = final_lower if cur_up else final_upper
        direction[i] = cur_up
    return pd.Series(line, index=w.index), pd.Series(direction, index=w.index)


def adx(w, n=14):
    """Standard Wilder ADX/+DI/-DI on the daily frame. Returns a DataFrame
    with adx/plus_di/minus_di columns aligned to w's index."""
    up_move = w["high"].diff()
    down_move = -w["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=w.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=w.index)
    tr = pd.concat([
        w["high"] - w["low"],
        (w["high"] - w["close"].shift(1)).abs(),
        (w["low"] - w["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr_n = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / n, adjust=False).mean() / atr_n
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / n, adjust=False).mean() / atr_n
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    adx_line = dx.ewm(alpha=1.0 / n, adjust=False).mean()
    return pd.DataFrame({"adx": adx_line, "plus_di": plus_di, "minus_di": minus_di})


def minervini_ok(w, i):
    """The literal daily Minervini Trend Template: close > 50-day EMA >
    150-day EMA > 200-day EMA, the 200-day EMA rising over the lookback,
    price >=25% above the 252-day low, and within 25% of the 252-day high."""
    row = w.iloc[i]
    if i < MINERVINI_EMA200_SLOPE_DAYS or pd.isna(row.get("ema200")):
        return False
    ema200_prior = w["ema200"].iloc[i - MINERVINI_EMA200_SLOPE_DAYS]
    if pd.isna(ema200_prior):
        return False
    ema200_rising = row["ema200"] > ema200_prior
    low_252d, high_52w = row.get("low_252d"), row.get("rolling_high_252d")
    if pd.isna(low_252d) or pd.isna(high_52w) or low_252d <= 0 or high_52w <= 0:
        return False
    above_low = (row["close"] - low_252d) / low_252d * 100 >= MINERVINI_52W_LOW_BUFFER_PCT
    near_high = (high_52w - row["close"]) / high_52w * 100 <= MINERVINI_52W_HIGH_BUFFER_PCT
    return bool(
        row["close"] > row["ema50"] > row["ema150"] > row["ema200"]
        and ema200_rising and above_low and near_high
    )


_default_stage_fn = None  # resolved lazily, once, on first score_at() call -- see below


def score_at(w, i, stage_fn=None):
    """w: a daily frame already run through daily_base.compute_indicators
    AND compute_extra_indicators. stage_fn: which Stage lens to use for
    condition 6 and the "stage" field -- defaults to daily_base.stage() (the
    same 60d/130d SMA read used everywhere else). That default is resolved
    via a lazy import (to avoid a module-load-time circular import with
    daily_base, which imports adx() from this module), but cached in
    _default_stage_fn after the first call, since score_at() can run once
    per candidate per date in a hot backtest loop. Returns None if there
    isn't enough history yet for all 8 conditions, else a dict: pct (0-100),
    hits (0-8), stage, minervini (bool), vstop_uptrend (bool or None),
    golden_setup (bool)."""
    global _default_stage_fn
    if stage_fn is None:
        if _default_stage_fn is None:
            from daily_base import stage as _default_stage_fn
        stage_fn = _default_stage_fn
    if i < 0 or i >= len(w):
        return None
    row = w.iloc[i]
    needed = ["ema50", "ema150", "ema200", "macd_line", "macd_signal",
              "rsi", "vol_sma20"]
    if any(pd.isna(row.get(c)) for c in needed):
        return None

    stage_now = stage_fn(w, i)
    rs_60 = row.get("rs_60") if "rs_60" in w.columns else None
    rs_ok = bool(rs_60 is not None and not pd.isna(rs_60) and rs_60 > 0)

    hits = sum([
        row["close"] > row["ema50"],
        row["ema50"] > row["ema150"],
        row["ema150"] > row["ema200"],
        row["macd_line"] > row["macd_signal"],
        RSI_BAND[0] <= row["rsi"] <= RSI_BAND[1],
        stage_now == "Stage 2",
        rs_ok,
        row["volume"] > row["vol_sma20"],
    ])
    pct = round(hits / TOTAL_CONDITIONS * 100)
    mins_ok = minervini_ok(w, i)
    vstop_up = bool(row["vstop_uptrend"]) if not pd.isna(row.get("vstop_uptrend")) else None
    golden = bool(
        stage_now == "Stage 2" and pct > GOLDEN_SETUP_SCORE_MIN
        and vstop_up is True and mins_ok and rs_ok
    )
    return {
        "pct": pct, "hits": hits, "stage": stage_now, "minervini": mins_ok,
        "vstop_uptrend": vstop_up, "golden_setup": golden,
    }


def top_picks(candidates, top_n=TOP_PICKS_MAX, min_score=None):
    """candidates: list of dicts, each with 'symbol', 'score_pct', and
    'momentum_score_pct' (None allowed, sorted last). Ties broken by
    rs_60_pct, then momentum_return_pct, both descending. Returns
    {symbol: score_pct} for the top `top_n`.

    min_score: if given, a candidate is only eligible once BOTH its
    composite Score AND its Momentum Score clear this bar -- a strong
    composite Score alone (good trend/structure) isn't enough if price/
    volume momentum right now doesn't confirm it, and vice versa. Missing
    either score (not enough history yet) makes a candidate ineligible
    rather than passing it through unconfirmed. A weak day yields fewer
    than top_n picks instead of forcing weak ones through to fill the quota."""
    ranked = sorted(
        candidates,
        key=lambda c: (
            c.get("score_pct") is None,
            -(c.get("score_pct") or 0),
            -(c.get("rs_60_pct") if c.get("rs_60_pct") is not None else float("-inf")),
            -(c.get("momentum_return_pct") if c.get("momentum_return_pct") is not None else float("-inf")),
        ),
    )
    eligible = [c for c in ranked if c.get("score_pct") is not None]
    if min_score is not None:
        eligible = [
            c for c in eligible
            if c["score_pct"] >= min_score
            and c.get("momentum_score_pct") is not None and c["momentum_score_pct"] >= min_score
        ]
    top = eligible[:top_n]
    return {c["symbol"]: c["score_pct"] for c in top}
