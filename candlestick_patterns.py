"""Bearish reversal candlestick patterns, detected from OHLCV alone.
Dropdown/table-only (exit_triggers.py wires these into STOP_LOSS_TRIGGERS,
not chart.py's CHART_TRIGGER_KEYS). Each function returns a boolean Series
aligned to w.index -- True on bars where that pattern's shape completed.
"""
SMALL_BODY_RATIO = 0.30       # body <= this fraction of the day's range counts as "small"
LONG_WICK_RATIO = 2.0         # a wick this many times the body counts as "long"
DOJI_BODY_RATIO = 0.10
NEAR_HIGH_BAND_PCT = 3.0      # close within this % of the rolling 20-day high counts as "at a high"
UPTREND_LOOKBACK_DAYS = 10


def _shapes(w):
    o, h, l, c = w["open"], w["high"], w["low"], w["close"]
    body_top = c.where(c > o, o)     # max(open, close)
    body_bottom = c.where(c < o, o)  # min(open, close)
    body = (c - o).abs()
    rng = (h - l).replace(0, float("nan"))
    upper_wick = h - body_top
    lower_wick = body_bottom - l
    return o, h, l, c, body, rng, upper_wick, lower_wick


def bearish_engulfing(w):
    o, h, l, c, body, rng, upper_wick, lower_wick = _shapes(w)
    prev_bullish = c.shift(1) > o.shift(1)
    engulfs = (o >= c.shift(1)) & (c <= o.shift(1)) & (c < o)
    return prev_bullish & engulfs


def shooting_star_at_high(w):
    o, h, l, c, body, rng, upper_wick, lower_wick = _shapes(w)
    near_high = h >= h.shift(1).rolling(20, min_periods=5).max() * (1 - NEAR_HIGH_BAND_PCT / 100)
    shape = (upper_wick >= LONG_WICK_RATIO * body) & (lower_wick <= SMALL_BODY_RATIO * body) & (body <= SMALL_BODY_RATIO * rng)
    return shape & near_high


def hanging_man(w):
    o, h, l, c, body, rng, upper_wick, lower_wick = _shapes(w)
    near_high = h >= h.shift(1).rolling(20, min_periods=5).max() * (1 - NEAR_HIGH_BAND_PCT / 100)
    shape = (lower_wick >= LONG_WICK_RATIO * body) & (upper_wick <= SMALL_BODY_RATIO * body) & (body <= SMALL_BODY_RATIO * rng)
    return shape & near_high


def evening_star(w):
    o, h, l, c, body, rng, upper_wick, lower_wick = _shapes(w)
    body_bottom = c.where(c < o, o)  # min(open, close)
    d1_bullish, d1_body = c.shift(2) > o.shift(2), (c.shift(2) - o.shift(2)).abs()
    d2_small = body.shift(1) <= SMALL_BODY_RATIO * d1_body
    d2_gapped_up = body_bottom.shift(1) > c.shift(2)
    d3_bearish = c < o
    d3_deep = c < (o.shift(2) + c.shift(2)) / 2
    return d1_bullish & d2_small & d2_gapped_up & d3_bearish & d3_deep


def three_black_crows(w):
    c, o = w["close"], w["open"]
    all_bearish = (c < o) & (c.shift(1) < o.shift(1)) & (c.shift(2) < o.shift(2))
    lower_closes = (c < c.shift(1)) & (c.shift(1) < c.shift(2))
    opens_within_body = (o < o.shift(1)) & (o.shift(1) < o.shift(2))
    return all_bearish & lower_closes & opens_within_body


def doji_after_uptrend(w):
    o, h, l, c, body, rng, upper_wick, lower_wick = _shapes(w)
    is_doji = body <= DOJI_BODY_RATIO * rng
    uptrend = c > c.shift(UPTREND_LOOKBACK_DAYS)
    return is_doji & uptrend


PATTERNS = {
    "bearish_engulfing": ("Bearish engulfing candle", bearish_engulfing),
    "shooting_star_at_high": ("Shooting star at a high", shooting_star_at_high),
    "hanging_man": ("Hanging man at a high", hanging_man),
    "evening_star": ("Evening star", evening_star),
    "three_black_crows": ("Three black crows", three_black_crows),
    "doji_after_uptrend": ("Doji after a sustained uptrend", doji_after_uptrend),
}
