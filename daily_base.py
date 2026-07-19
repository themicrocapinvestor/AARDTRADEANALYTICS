"""Ayush's Screen -- daily horizontal-base-breakout detector, encoding a
custom set of "winning stock" breakout/base/momentum rules.

Daily-cadence sibling of the weekly app (kite-weekly-screener): every
day-count threshold below is that app's week-count x5 (~5 trading days per
week); percent thresholds, volume multiples, and touch/count thresholds are
UNCHANGED from the weekly version since those aren't day-count stand-ins.

Everything here is pure (DataFrame in, dict out) -- no Kite/network calls --
so it's unit-testable and reusable from both the live scanner and the backtest.
"""
import numpy as np
import pandas as pd

from extra_indicators import adx as _adx

# --- tunable thresholds (day-counts are the weekly app's week-counts x5; see module docstring) ---
TREND_SMA_FAST = 50          # the literal daily Minervini/O'Neil fast trend SMA
TREND_SMA_MED = 150          # the literal daily Minervini/O'Neil mid trend SMA -- used only by
                              # stage() below (the classic 50/200 trend template elsewhere in
                              # this file, _trend_ok_classic, doesn't need the 150-day leg)
TREND_SMA_SLOW = 200         # the literal daily Minervini/O'Neil slow trend SMA
STAGE_MA_FAST = 60           # Kept for exclusions()'s independent "below key MA" screening
STAGE_MA_SLOW = 130          # rule (a different, unrelated check) -- NOT used by stage() itself
                              # anymore; see stage()'s own docstring for what that uses instead.
STAGE_SLOPE_LOOKBACK_DAYS = 20  # bars back to check an SMA's own slope (was 4 weeks x5)
BASE_MIN_DAYS = 30           # rescaled: min days in a qualifying base (was 6 weeks x5)
BASE_MAX_DAYS = 130          # give up calling it "the same base" past ~6 months (was 26 weeks x5)
BASE_RANGE_PCT = 10.0        # base high-to-low span -- tightened from 15.0, which let wide/unstable
                              # "bases" qualify and produced oversized stops
VOL_DRYUP = 0.70             # base days' volume must be below this * pre-base rally avg volume --
                              # tightened from 0.85, which barely required any real volume dry-up
BREAKOUT_VOL_MULT = 1.6      # breakout day's volume vs. the base's own average volume -- raised
                              # from 1.3 to demand more conviction before calling it a real breakout
BREAKOUT_BUFFER_PCT = 1.0    # breakout close must clear the base high by this % -- filters marginal,
                              # by-a-hair "breakouts" that are really just noise at the top of the base
DISTRIBUTION_LOOKBACK = 100  # days to scan for distribution-day count (was 20 weeks x5)
DISTRIBUTION_MAX = 20        # exclude if >= this many distribution days in the lookback (was 4
                              # weeks x5 -- keeps the same ~20% frequency threshold, not just the count)
CLIMAX_LOOKBACK_DAYS = 40    # days over which a climax move is measured (was 8 weeks x5)
CLIMAX_RETURN_PCT = 40.0     # cumulative % move over CLIMAX_LOOKBACK_DAYS to flag a blow-off
DEEP_DRAWDOWN_PCT = 50.0     # % below 252-day high to deprioritize
CHOPPY_ATR_PCT = 12.0        # daily ATR% above this with no clean trend -> exclude
MIN_DAILY_TURNOVER = 5e6     # avg(day's volume) * close, in rupees -- floor for tradability. This
                              # is the SAME rupee floor the weekly app used per WEEK, now applied per
                              # DAY -- a materially stricter liquidity bar; lower it if too few candidates
                              # survive in practice.

SWING_WINDOW = 15                   # bars each side to call a bar a local swing high/low (was 3 weeks x5)
RESISTANCE_TOUCH_TOL_PCT = 2.0      # a base day's high within this % of base_high counts as a "touch"
MIN_RESISTANCE_TOUCHES = 2          # require at least this many touches before a breakout counts --
                                      # on top of the range/volume-dryup checks _base_quality already does
SHAKEOUT_CLOSE_POS_MIN = 0.6        # the day that set base_low closed in the top (1-this) of its
                                      # range -- a sharp poke-and-recover, not a grind -- flagged as a tag,
                                      # doesn't gate the signal (adds conviction, isn't required)
DISTRIBUTION_SIG_LOOKBACK = 60      # days to scan for the classic distribution signature (was 12 weeks x5)
DISTRIBUTION_SIG_MIN_LOWER_HIGHS = 2  # >= this many successively lower swing highs
WEDGE_LOOKBACK = 30                  # days over which a wedge-up is measured (was 6 weeks x5)
WEDGE_MIN_RISE_PCT = 8.0            # price must have risen at least this much over the lookback
PULLBACK_LOOKBACK_DAYS = 60         # days to look back for the prior swing high (was 12 weeks x5)
PULLBACK_MAX_PCT = 12.0             # max pullback off that high still counted as "healthy"
PULLBACK_MIN_RESUME_VOL_MULT = 1.2  # resumption day's volume vs trailing average
UNDERCUT_LOOKBACK_DAYS = 50         # days to look back for the swing low being undercut (was 10 weeks x5)
UNDERCUT_MIN_RECLAIM_PCT = 1.0      # close must reclaim at least this % above the undercut low
GAP_CONTINUATION_MIN_PCT = 2.0      # min gap-up %, open vs prior day's high
MOMENTUM_LOOKBACK_DAYS = 45         # trailing-return window used for the cross-sectional momentum
                                      # composite (momentum_score below) -- Ayush's own 45-day pick
RVOL_LOOKBACK_DAYS = 100            # RVol: days in the trailing average volume (was 20 weeks x5)
MIN_RVOL_PCT = 130.0                # this day's volume must be at/above 1.3x its own trailing
                                      # RVOL_LOOKBACK_DAYS-day average -- below that means the
                                      # breakout/base day didn't trade on convincingly above-average volume.
                                      # Also reused as momentum_score's RVol hit threshold below (one
                                      # source of truth for "what counts as high volume" everywhere).
MOMENTUM_ADX_MIN = 25.0              # momentum_score: ADX above this counts as "trending" (Wilder's own
                                      # classic threshold for a market that's actually trending vs. choppy)
OBV_TREND_LOOKBACK_DAYS = 45         # momentum_score: OBV today vs. OBV this many days ago -- rising means
                                      # volume is confirming the price trend, matches MOMENTUM_LOOKBACK_DAYS
                                      # so both the price and volume legs of the composite read the same window

_VOL_AVG_WINDOW = 50                 # trailing-average window used for the various "pre-base"/"resumption"
                                      # volume checks below (was 10 weeks x5)


def _sma(s, n):
    return s.rolling(n).mean()


def _swing_highs(w, start, end, window=SWING_WINDOW, max_index=None):
    """Integer positions in [start, end] where 'high' is a local max over
    +/- window bars -- shared building block for the base/resistance/distribution checks below.

    max_index bounds how far right the +/-window comparison is allowed to
    look (defaults to len(w)-1, i.e. the whole frame). Callers evaluating an
    "as of" bar that isn't the last row of w -- e.g. the backtest walk, which
    keeps each symbol's FULL history preloaded and evaluates arbitrary
    earlier indices for performance -- must pass the true "as of" index here,
    otherwise the window would peek at bars the backtest hasn't reached yet
    (look-ahead bias)."""
    if max_index is None:
        max_index = len(w) - 1
    out = []
    for i in range(start, end + 1):
        lo, hi = max(0, i - window), min(max_index, i + window)
        if w["high"].iloc[i] == w["high"].iloc[lo:hi + 1].max():
            out.append(i)
    return out


def _swing_lows(w, start, end, window=SWING_WINDOW, max_index=None):
    """See _swing_highs -- same max_index look-ahead guard applies."""
    if max_index is None:
        max_index = len(w) - 1
    out = []
    for i in range(start, end + 1):
        lo, hi = max(0, i - window), min(max_index, i + window)
        if w["low"].iloc[i] == w["low"].iloc[lo:hi + 1].min():
            out.append(i)
    return out


def _atr_pct(daily, n=14):
    tr = pd.concat([
        daily["high"] - daily["low"],
        (daily["high"] - daily["close"].shift(1)).abs(),
        (daily["low"] - daily["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return (tr.rolling(n).mean() / daily["close"]) * 100


def compute_indicators(daily, rs_60=None, rs_123=None, rs_30=None):
    """rs_60/rs_123 columns are omitted entirely (not just left NaN) when RS
    isn't available, so exclusions() can just check for the columns'
    presence. rs_30 feeds only momentum_score's composite ranking, not an
    eligibility gate."""
    w = daily.copy()
    w["sma_fast"] = _sma(w["close"], TREND_SMA_FAST)
    w["sma_mid"] = _sma(w["close"], TREND_SMA_MED)
    w["sma_slow"] = _sma(w["close"], TREND_SMA_SLOW)
    w["sma_stage_fast"] = _sma(w["close"], STAGE_MA_FAST)
    w["sma_stage_slow"] = _sma(w["close"], STAGE_MA_SLOW)
    w["atr_pct"] = _atr_pct(w)
    w["turnover"] = w["volume"] * w["close"]
    # RVol: today's volume vs. the trailing average of PRIOR days (shift(1) so
    # today never averages in its own volume) -- same definition as the
    # TradingView RVol script, on daily bars. >100% = at/above its own average;
    # informational display metric, not a gate (see the Sectors-tab discussion).
    w["rvol_pct"] = (w["volume"] / _sma(w["volume"].shift(1), RVOL_LOOKBACK_DAYS)) * 100
    w["rolling_high_252d"] = w["high"].rolling(252, min_periods=50).max()
    # distribution day: close in bottom third of the day's range + volume up vs prior day
    day_range = (w["high"] - w["low"]).replace(0, np.nan)
    close_pos = (w["close"] - w["low"]) / day_range
    w["is_distribution_day"] = (close_pos <= 0.33) & (w["volume"] > w["volume"].shift(1))
    # ADX (Wilder, 14) -- precomputed once here (not inside momentum_score, which gets
    # called once per candidate per date in the backtest's hot loop) so it's O(1) to read.
    # +DI/-DI kept too so Stock Detail's ADX chart can read these columns directly instead
    # of calling extra_indicators.adx() a second time on the same frame.
    _adx_df = _adx(w)
    w["adx"] = _adx_df["adx"]
    w["plus_di"] = _adx_df["plus_di"]
    w["minus_di"] = _adx_df["minus_di"]
    # OBV: cumulative sum of volume signed by the day's price direction -- captures
    # whether volume is actually confirming the price trend (rising OBV = buying
    # pressure accumulating), the "volume" half of the momentum composite below.
    w["obv"] = (np.sign(w["close"].diff()).fillna(0) * w["volume"]).cumsum()
    if rs_60 is not None:
        w["rs_60"] = rs_60.reindex(w.index)
    if rs_30 is not None:
        w["rs_30"] = rs_30.reindex(w.index)
    if rs_123 is not None:
        w["rs_123"] = rs_123.reindex(w.index)
    return w


def _trend_ok_classic(w, i):
    """The classic trend lens: SMA50/SMA200 -- the literal Minervini/O'Neil daily trend template."""
    row = w.iloc[i]
    if pd.isna(row["sma_fast"]) or pd.isna(row["sma_slow"]):
        return False
    slope_ok = (i >= STAGE_SLOPE_LOOKBACK_DAYS) and (row["sma_slow"] > w["sma_slow"].iloc[i - STAGE_SLOPE_LOOKBACK_DAYS])
    return row["close"] > row["sma_fast"] and row["close"] > row["sma_slow"] and slope_ok


def stage(w, i):
    """Weinstein-style Stage Analysis (1=basing, 2=advancing, 3=topping,
    4=declining) via the daily 50/150/200-day SMA stack, 60-day Relative
    Strength, and each SMA's own slope.

    Returns None if RS(60d) isn't available at all (no benchmark was
    supplied to compute_indicators) -- RS is a required leg of the Stage 2/4
    tests, not optional confirmation, so a Stage read without it would
    silently be looser than intended."""
    row = w.iloc[i]
    s50, s150, s200 = row["sma_fast"], row["sma_mid"], row["sma_slow"]
    if pd.isna(s50) or pd.isna(s150) or pd.isna(s200) or i < STAGE_SLOPE_LOOKBACK_DAYS:
        return None
    if "rs_60" not in w.columns or pd.isna(row["rs_60"]):
        return None
    rs_positive = row["rs_60"] > 0

    prior = w.iloc[i - STAGE_SLOPE_LOOKBACK_DAYS]
    s50_rising, s150_rising, s200_rising = (
        s50 > prior["sma_fast"], s150 > prior["sma_mid"], s200 > prior["sma_slow"],
    )
    s50_falling, s150_falling, s200_falling = (
        s50 < prior["sma_fast"], s150 < prior["sma_mid"], s200 < prior["sma_slow"],
    )

    price = row["close"]
    bullish_stack = price > s50 > s150 > s200
    bearish_stack = price < s50 < s150 < s200
    all_rising = s50_rising and s150_rising and s200_rising
    all_falling = s50_falling and s150_falling and s200_falling

    if bullish_stack and all_rising and rs_positive:
        return "Stage 2"
    if bearish_stack and all_falling and not rs_positive:
        return "Stage 4"
    if s50 >= s150 >= s200:
        return "Stage 3"  # was bullish, losing momentum -- topping
    return "Stage 1"  # was bearish, not accelerating down -- basing


def trend_ok(w, i):
    """Stage-2 uptrend filter. A stock qualifies if EITHER trend lens
    confirms it -- the original SMA50/SMA200 rule, OR Stage 2 by the full
    SMA50/150/200 + RS Stage Analysis lens (see module docstring for why
    this is an OR, not a stricter AND)."""
    return _trend_ok_classic(w, i) or stage(w, i) == "Stage 2"


def momentum_score(w, i, lookback=MOMENTUM_LOOKBACK_DAYS):
    """Cross-sectional momentum-leader composite -- five equal-weighted
    (1/5 each) price/volume conditions, same "hits / N * 100" convention as
    extra_indicators.score_at() elsewhere in this app. A condition that
    can't be computed yet (not enough history, RS/ADX/OBV column missing)
    doesn't count as a hit but also isn't treated as a fail, so a
    partially-warmed-up frame still gets a (lower-confidence) score instead
    of None, provided the {lookback}-day return itself has enough history."""
    if i < lookback:
        return None
    row = w.iloc[i]
    ret_pct = (row["close"] / w["close"].iloc[i - lookback] - 1) * 100
    high_252d = row["rolling_high_252d"]
    pct_off_high = None
    if not pd.isna(high_252d) and high_252d > 0:
        pct_off_high = (high_252d - row["close"]) / high_252d * 100

    return_ok = bool(not pd.isna(ret_pct) and ret_pct > 0)

    rvol_val = row.get("rvol_pct")
    rvol_ok = bool(rvol_val is not None and not pd.isna(rvol_val) and rvol_val > MIN_RVOL_PCT)

    adx_val = row.get("adx")
    adx_ok = bool(adx_val is not None and not pd.isna(adx_val) and adx_val > MOMENTUM_ADX_MIN)

    obv_trend_up = None
    if i >= OBV_TREND_LOOKBACK_DAYS and "obv" in w.columns:
        obv_now, obv_prior = row.get("obv"), w["obv"].iloc[i - OBV_TREND_LOOKBACK_DAYS]
        if not pd.isna(obv_now) and not pd.isna(obv_prior):
            obv_trend_up = bool(obv_now > obv_prior)

    rs_30_val = row.get("rs_30")
    rs_30_ok = bool(rs_30_val is not None and not pd.isna(rs_30_val) and rs_30_val > 0)

    hits = sum([return_ok, rvol_ok, adx_ok, bool(obv_trend_up), rs_30_ok])
    score_pct = round(hits / 5 * 100)

    return {
        "return_pct": round(float(ret_pct), 2),
        "pct_off_252d_high": round(float(pct_off_high), 2) if pct_off_high is not None else None,
        "rvol_pct": round(float(rvol_val), 1) if rvol_val is not None and not pd.isna(rvol_val) else None,
        "adx": round(float(adx_val), 1) if adx_val is not None and not pd.isna(adx_val) else None,
        "obv_trend_up": obv_trend_up,
        "rs_30_pct": round(float(rs_30_val), 4) if rs_30_val is not None and not pd.isna(rs_30_val) else None,
        "score_pct": score_pct,
        "hits": hits,
    }


def _distribution_signature(w, i):
    """Classic distribution -- a run of successively LOWER swing highs,
    with multiple closes in the lower half of the day's range on above-
    average volume, over the last DISTRIBUTION_SIG_LOOKBACK days. Distinct
    from the simple distribution-day count (which just counts individual down-close/up-volume days without
    caring whether price is actually making a lower-highs pattern)."""
    lo = max(0, i - DISTRIBUTION_SIG_LOOKBACK + 1)
    swings = _swing_highs(w, lo, i, max_index=i)
    if len(swings) < DISTRIBUTION_SIG_MIN_LOWER_HIGHS + 1:
        return False
    highs = [w["high"].iloc[s] for s in swings]
    lower_high_count = sum(1 for a, b in zip(highs, highs[1:]) if b < a)
    if lower_high_count < DISTRIBUTION_SIG_MIN_LOWER_HIGHS:
        return False
    window = w.iloc[lo:i + 1]
    avg_vol = window["volume"].mean()
    day_range = (window["high"] - window["low"]).replace(0, np.nan)
    close_pos = (window["close"] - window["low"]) / day_range
    distributive_closes = int(((close_pos <= 0.5) & (window["volume"] > avg_vol)).sum())
    return distributive_closes >= DISTRIBUTION_SIG_MIN_LOWER_HIGHS


def _wedging_up(w, i):
    """Price grinding higher while its daily range AND volume both
    contract -- a rising wedge on fading conviction, a classic pre-reversal
    tell rather than a healthy advance."""
    lo = i - WEDGE_LOOKBACK + 1
    if lo < 1:
        return False
    window = w.iloc[lo:i + 1]
    rise_pct = (window["close"].iloc[-1] / window["close"].iloc[0] - 1) * 100 if window["close"].iloc[0] > 0 else 0
    if rise_pct < WEDGE_MIN_RISE_PCT:
        return False
    x = np.arange(len(window))
    vol_slope = np.polyfit(x, window["volume"].values, 1)[0]
    range_slope = np.polyfit(x, (window["high"] - window["low"]).values, 1)[0]
    return bool(vol_slope < 0 and range_slope < 0)


def _pullback_continuation(w, last):
    """After a prior swing high, a moderate pullback (<= PULLBACK_MAX_PCT)
    followed by a strong up day (close > open, close > prior close) on
    above-average volume -- a continuation entry that doesn't require a
    freshly-qualifying horizontal base, just a healthy uptrend digesting
    gains before resuming."""
    lo = max(0, last - PULLBACK_LOOKBACK_DAYS)
    if last - lo < 3:
        return None
    prior_high = w["high"].iloc[lo:last].max()
    row = w.iloc[last]
    if pd.isna(prior_high) or prior_high <= 0:
        return None
    pullback_pct = (prior_high - row["low"]) / prior_high * 100
    if pullback_pct <= 0 or pullback_pct > PULLBACK_MAX_PCT:
        return None
    prev_close = w["close"].iloc[last - 1]
    avg_vol = w["volume"].iloc[max(0, last - _VOL_AVG_WINDOW):last].mean()
    if (row["close"] > prev_close and row["close"] > row["open"]
            and not pd.isna(avg_vol) and row["volume"] > PULLBACK_MIN_RESUME_VOL_MULT * avg_vol):
        return {"level": round(float(prior_high), 2), "pullback_pct": round(float(pullback_pct), 2)}
    return None


def _undercut_and_rally(w, last):
    """Price briefly undercuts a recent swing low (a shakeout of late
    longs) then closes back above it with volume -- often a stronger entry
    than waiting for a fresh breakout, since the weak hands have already
    been flushed out."""
    lo = max(0, last - UNDERCUT_LOOKBACK_DAYS)
    swings = _swing_lows(w, lo, last - 1, max_index=last - 1)
    if not swings:
        return None
    ref_low = min(w["low"].iloc[j] for j in swings)
    row = w.iloc[last]
    if ref_low <= 0:
        return None
    if row["low"] < ref_low and row["close"] > ref_low * (1 + UNDERCUT_MIN_RECLAIM_PCT / 100):
        avg_vol = w["volume"].iloc[max(0, last - _VOL_AVG_WINDOW):last].mean()
        if not pd.isna(avg_vol) and row["volume"] > avg_vol:
            return {"level": round(float(ref_low), 2)}
    return None


def _gap_continuation(w, last):
    """A gap-up day (open clears the prior day's high) that also
    closes strong on above-average volume -- momentum continuing without
    waiting for a pullback or a fresh base."""
    if last < _VOL_AVG_WINDOW + 5:
        return None
    row, prev = w.iloc[last], w.iloc[last - 1]
    if prev["high"] <= 0:
        return None
    gap_pct = (row["open"] - prev["high"]) / prev["high"] * 100
    avg_vol = w["volume"].iloc[max(0, last - _VOL_AVG_WINDOW):last].mean()
    if (gap_pct >= GAP_CONTINUATION_MIN_PCT and row["close"] > row["open"]
            and not pd.isna(avg_vol) and row["volume"] > avg_vol):
        return {"level": round(float(prev["high"]), 2)}
    return None


def exclusions(w, i, trend_now=None):
    """Binary exclusion screens. Returns a list of triggered exclusion codes (empty = clean).

    trend_now: the caller's already-computed trend_ok(w, i), if it has one
    handy (detect_daily_setup_at needs trend_ok right after this call
    anyway, so it passes it through instead of this function recomputing
    it). Computed here if not given, for any other/future caller."""
    if trend_now is None:
        trend_now = trend_ok(w, i)
    row = w.iloc[i]
    hits = []

    below_classic = not (row["close"] > row["sma_fast"] and row["close"] > row["sma_slow"])
    below_stage = not (row["close"] > row["sma_stage_fast"] and row["close"] > row["sma_stage_slow"])
    if below_classic and below_stage:
        hits.append("below_key_ma")

    lo = max(0, i - DISTRIBUTION_LOOKBACK + 1)
    if w["is_distribution_day"].iloc[lo:i + 1].sum() >= DISTRIBUTION_MAX:
        hits.append("distribution_days")

    lo2 = max(0, i - CLIMAX_LOOKBACK_DAYS)
    base_price = w["close"].iloc[lo2]
    if base_price and base_price > 0:
        move_pct = (row["close"] - base_price) / base_price * 100
        window_vol = w["volume"].iloc[lo2:i + 1]
        if move_pct >= CLIMAX_RETURN_PCT and row["volume"] >= window_vol.max():
            hits.append("climax_move")

    if not pd.isna(row["rolling_high_252d"]) and row["rolling_high_252d"] > 0:
        drawdown_pct = (row["rolling_high_252d"] - row["close"]) / row["rolling_high_252d"] * 100
        if drawdown_pct >= DEEP_DRAWDOWN_PCT:
            hits.append("deep_drawdown")

    if not pd.isna(row["atr_pct"]) and row["atr_pct"] >= CHOPPY_ATR_PCT:
        hits.append("choppy")

    avg_turnover = w["turnover"].iloc[max(0, i - _VOL_AVG_WINDOW):i + 1].mean()
    if not pd.isna(avg_turnover) and avg_turnover < MIN_DAILY_TURNOVER:
        hits.append("illiquid")

    # BOTH the 60-day and 123-day RS windows must be > 0 -- a stock that's
    # only strong on one horizon (a short-lived pop not yet reflected over the
    # longer window, or fading momentum that hasn't caught up on the shorter
    # one) doesn't qualify. Skipped (not excluded) if either window's RS is
    # unavailable, same "unknown != fail" convention as everywhere else here.
    if "rs_60" in w.columns and "rs_123" in w.columns:
        rs_60, rs_123 = row["rs_60"], row["rs_123"]
        if not pd.isna(rs_60) and not pd.isna(rs_123) and (rs_60 <= 0 or rs_123 <= 0):
            hits.append("weak_rs")

    if not pd.isna(row["rvol_pct"]) and row["rvol_pct"] <= MIN_RVOL_PCT:
        hits.append("low_rvol")

    # The distribution-signature and wedging-up checks are the priciest here (a swing-high search, two np.polyfit
    # calls) -- only worth paying for once a stock has cleared trend_ok,
    # since a stock failing BOTH trend lenses can't become a candidate
    # anyway (detect_daily_setup{,_at} rejects it right after this
    # exclusions() call regardless of what ends up in this list). This is
    # the dominant remaining cost in the backtest's per-date, per-symbol
    # candidate scan -- gating it here skips most of that work for anything
    # already out of trend, with no change to which stocks can ever qualify
    # as a candidate (only cosmetic: an out-of-trend stock that would have
    # separately tripped one of those checks now just shows no signal at all instead of
    # EXCLUDED-with-reasons in the live Scanner's Excluded list).
    if trend_now:
        if i >= DISTRIBUTION_SIG_LOOKBACK and _distribution_signature(w, i):
            hits.append("distribution_signature")

        if i >= WEDGE_LOOKBACK and _wedging_up(w, i):
            hits.append("wedging_up")

    return hits


def _base_quality(w, base_start, base_end):
    """base_start/base_end: integer positions (inclusive) of the candidate base
    days, NOT including the breakout day itself. Returns (ok, base_low,
    base_range_pct, avg_base_vol, touches)."""
    base = w.iloc[base_start:base_end + 1]
    if len(base) < BASE_MIN_DAYS:
        return False, None, None, None, None
    base_low = base["low"].min()
    base_high = base["high"].max()
    base_range_pct = (base_high - base_low) / base_low * 100 if base_low > 0 else 999.0
    pre_base_start = max(0, base_start - _VOL_AVG_WINDOW)
    pre_base_avg_vol = w["volume"].iloc[pre_base_start:base_start].mean() if base_start > 0 else base["volume"].mean()
    avg_base_vol = base["volume"].mean()
    # count days whose high tests the top of the base (within tolerance) --
    # a base with only one probe at the highs is thinner evidence of real resistance
    # than one that's been tested and held multiple times.
    touches = int((base["high"] >= base_high * (1 - RESISTANCE_TOUCH_TOL_PCT / 100)).sum())
    ok = (
        base_range_pct <= BASE_RANGE_PCT
        and (pd.isna(pre_base_avg_vol) or avg_base_vol < VOL_DRYUP * pre_base_avg_vol)
        and touches >= MIN_RESISTANCE_TOUCHES
    )
    return ok, base_low, base_range_pct, avg_base_vol, touches


def _shakeout_tag(w, base_start, base_end, base_low):
    """Was the base's low itself a sharp poke-and-recover (shakeout) rather
    than a slow grind down? True if the day that set base_low closed in the
    upper part of its own range -- informational only, doesn't gate the signal."""
    base = w.iloc[base_start:base_end + 1]
    low_pos = base["low"].values.argmin()
    row = base.iloc[low_pos]
    day_range = row["high"] - row["low"]
    if day_range <= 0:
        return False
    close_pos = (row["close"] - row["low"]) / day_range
    return bool(close_pos >= SHAKEOUT_CLOSE_POS_MIN)


def _gap_support_level(w, base_start, base_end):
    """An unfilled gap-up within the base (low[t] > high[t-1]) that no
    later day's low has re-entered -- extra support under the pattern.
    Returns the gap's lower bound, or None if there isn't one."""
    for i in range(base_start + 1, base_end + 1):
        prev_high = w["high"].iloc[i - 1]
        cur_low = w["low"].iloc[i]
        if cur_low > prev_high:
            filled = (w["low"].iloc[i + 1:base_end + 1] <= prev_high).any()
            if not filled:
                return round(float(prev_high), 2)
    return None


def detect_daily_setup(daily, symbol=None, min_base_days=BASE_MIN_DAYS, max_base_days=BASE_MAX_DAYS,
                        rs_60_series=None, rs_123_series=None, rs_30_series=None):
    """Returns a dict describing the most recent state (status BREAKOUT /
    IN_BASE / EXCLUDED), or None if nothing qualifies. Only ever looks at
    CLOSED daily bars, so there's no look-ahead into a still-forming day.

    Thin wrapper around detect_daily_setup_at that computes indicators once
    and evaluates at the last row. Callers that already have a symbol's FULL
    multi-year indicator frame precomputed (e.g. the backtest walk, which
    evaluates many earlier "as of" dates without re-slicing/recomputing
    indicators every time -- previously the dominant cost of a backtest run)
    should call detect_daily_setup_at directly instead.
    """
    if daily is None or len(daily) < min_base_days + TREND_SMA_SLOW:
        return None
    w = compute_indicators(daily, rs_60=rs_60_series, rs_123=rs_123_series, rs_30=rs_30_series)
    return detect_daily_setup_at(w, len(w) - 1, symbol=symbol,
                                  min_base_days=min_base_days, max_base_days=max_base_days)


def detect_daily_setup_at(w, last, symbol=None, min_base_days=BASE_MIN_DAYS, max_base_days=BASE_MAX_DAYS):
    """Same detection logic as detect_daily_setup, evaluated at an explicit
    `last` index into an ALREADY indicator-computed frame `w`, which may be
    longer than last+1 (e.g. a symbol's full multi-year history precomputed
    once). Every rolling indicator is strictly backward-looking, so a value
    at row `last` is identical whether computed on the full series or a
    slice ending at `last` -- the only place look-ahead could sneak in is
    the +/-window swing search inside _distribution_signature and
    _undercut_and_rally, which is why those take an explicit max_index bound
    instead of defaulting to len(w)-1.
    """
    if w is None or last < 0 or last >= len(w) or last < min_base_days + TREND_SMA_SLOW - 1:
        return None
    row = w.iloc[last]
    rs_60_pct = round(float(row["rs_60"]), 4) if "rs_60" in w.columns and not pd.isna(row["rs_60"]) else None
    rs_123_pct = round(float(row["rs_123"]), 4) if "rs_123" in w.columns and not pd.isna(row["rs_123"]) else None
    stage_now = stage(w, last)
    momentum = momentum_score(w, last)
    rvol_pct = round(float(row["rvol_pct"]), 1) if not pd.isna(row["rvol_pct"]) else None

    trend_now = trend_ok(w, last)
    excl = exclusions(w, last, trend_now=trend_now)
    if excl:
        return {"symbol": symbol, "status": "EXCLUDED", "reasons": excl,
                "as_of": w.index[last].date().isoformat(),
                "rs_60_pct": rs_60_pct, "rs_123_pct": rs_123_pct, "stage": stage_now}

    if not trend_now:
        return None  # not in a qualifying uptrend at all -- not excluded, just not a candidate

    # search backwards for the longest valid base ending the bar before "last"
    for base_len in range(max_base_days, min_base_days - 1, -1):
        base_start = last - base_len
        base_end = last - 1
        if base_start < 0:
            continue
        ok, base_low, base_range_pct, avg_base_vol, touches = _base_quality(w, base_start, base_end)
        if not ok:
            continue

        base_high = w["high"].iloc[base_start:base_end + 1].max()
        shakeout = _shakeout_tag(w, base_start, base_end, base_low)
        gap_support = _gap_support_level(w, base_start, base_end)
        breakout = (
            row["close"] > base_high * (1 + BREAKOUT_BUFFER_PCT / 100)
            and row["volume"] > BREAKOUT_VOL_MULT * avg_base_vol
        )
        if breakout:
            return {
                "symbol": symbol, "status": "BREAKOUT", "entry_type": "base_breakout",
                "as_of": w.index[last].date().isoformat(),
                "level": round(float(base_high), 2),
                "breakout_close": round(float(row["close"]), 2),
                "base_low": round(float(base_low), 2),
                "base_days": base_len,
                "base_range_pct": round(float(base_range_pct), 2),
                "avg_base_vol": int(avg_base_vol),
                "breakout_vol": int(row["volume"]),
                "resistance_touches": touches,
                "shakeout": shakeout,
                "gap_support": gap_support,
                "rs_60_pct": rs_60_pct, "rs_123_pct": rs_123_pct,
                "stage": stage_now,
                "momentum": momentum,
                "rvol_pct": rvol_pct,
            }
        else:
            # not broken out yet -- still watch this level if price hasn't fallen below the base
            if row["low"] >= base_low:
                return {
                    "symbol": symbol, "status": "IN_BASE",
                    "as_of": w.index[last].date().isoformat(),
                    "level": round(float(base_high), 2),
                    "base_low": round(float(base_low), 2),
                    "base_days": base_len,
                    "base_range_pct": round(float(base_range_pct), 2),
                    "avg_base_vol": int(avg_base_vol),
                    "resistance_touches": touches,
                    "shakeout": shakeout,
                    "gap_support": gap_support,
                    "stage": stage_now,
                    "rs_60_pct": rs_60_pct, "rs_123_pct": rs_123_pct,
                    "momentum": momentum,
                    "rvol_pct": rvol_pct,
                }
        break  # only evaluate the longest qualifying base found, not shorter sub-bases too

    # No qualifying horizontal base -- try the alternate, base-free entry triggers
    # (pullback continuation, undercut-and-rally, gap continuation). Each returns a "level"/reference-only stop; the ACTUAL stop for
    # trade management everywhere downstream is still the 50-day MA (Ayush's rule),
    # so "base_low" here is just that same MA reused for display/sizing consistency
    # with the base_breakout dict shape.
    stop_ref = row["sma_fast"]
    if pd.isna(stop_ref) or stop_ref >= row["close"]:
        return None
    for entry_type, finder in (
        ("undercut_and_rally", _undercut_and_rally),
        ("pullback_continuation", _pullback_continuation),
        ("gap_continuation", _gap_continuation),
    ):
        hit = finder(w, last)
        if hit:
            return {
                "symbol": symbol, "status": "BREAKOUT", "entry_type": entry_type,
                "as_of": w.index[last].date().isoformat(),
                "level": hit["level"],
                "breakout_close": round(float(row["close"]), 2),
                "base_low": round(float(stop_ref), 2),
                "base_days": None, "base_range_pct": None, "avg_base_vol": None,
                "breakout_vol": int(row["volume"]),
                "resistance_touches": None, "shakeout": None, "gap_support": None,
                "rs_60_pct": rs_60_pct, "rs_123_pct": rs_123_pct, "stage": stage_now, "momentum": momentum, "rvol_pct": rvol_pct,
            }
    return None
