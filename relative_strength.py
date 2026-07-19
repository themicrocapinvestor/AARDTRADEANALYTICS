"""Relative strength vs a benchmark index -- the classic
same "RS Line above the zero line" read used in Stage Analysis (a stock in a
genuine Stage 2 uptrend should be outperforming the index, not just its own
50/200-day average).
  https://traderlion.com/trading-strategies/stage-analysis/
  https://marketsmithindia.com/post/rs-line-rating-key-to-invest-in-growth-stocks-3

Formula mirrors the TradingView Pine Script Ayush supplied:
    RS = (price / price[length]) / (bench / bench[length]) - 1
computed here on daily closes over TWO lookback windows -- 60 days and 123
days -- BOTH of which must show RS > 0 for a stock to pass the filter
(daily_base.exclusions' weak-RS check). Requiring both a shorter and a longer
window to agree filters out stocks that only look strong on one horizon
(e.g. a short-lived pop that hasn't caught up on the longer window, or a
stock that was strong months ago but has since faded on the shorter one).
RS > 0 means the stock outperformed the benchmark over that window; RS <= 0
means it lagged. Benchmark is the NIFTY 500 index (not just NIFTY 50),
matching the universe.
"""
RS_LOOKBACK_SHORT_DAYS = 60
RS_LOOKBACK_LONG_DAYS = 123
RS_LOOKBACK_MOMENTUM_DAYS = 30  # shorter window used only by daily_base.momentum_score's composite
                                 # ranking -- a faster RS read than the 60d/123d entry-eligibility
                                 # windows above, since ranking wants to catch a stock turning
                                 # relatively strong sooner than the entry filter requires


def compute_rs(daily_stock, daily_bench, length=RS_LOOKBACK_LONG_DAYS):
    """daily_stock/daily_bench: daily OHLCV DataFrames (or None). Returns a
    Series of RS values aligned to daily_stock's index, or None if either
    input is missing/empty -- callers treat that as "RS unknown, skip the
    filter" rather than fatal."""
    if daily_stock is None or daily_bench is None or daily_stock.empty or daily_bench.empty:
        return None
    bench_close = daily_bench["close"].reindex(daily_stock.index, method="ffill")
    stock_close = daily_stock["close"]
    return (stock_close / stock_close.shift(length)) / (bench_close / bench_close.shift(length)) - 1


def compute_rs_all(daily_stock, daily_bench):
    """Convenience wrapper -- every call site in this app needs all three RS
    windows (60d entry filter, 123d entry filter, 30d momentum-only) for the
    same stock/benchmark pair, and used to call compute_rs() three separate
    times to get them. One call here instead: returns
    {60: series_or_None, 123: series_or_None, 30: series_or_None}."""
    return {
        RS_LOOKBACK_SHORT_DAYS: compute_rs(daily_stock, daily_bench, length=RS_LOOKBACK_SHORT_DAYS),
        RS_LOOKBACK_LONG_DAYS: compute_rs(daily_stock, daily_bench, length=RS_LOOKBACK_LONG_DAYS),
        RS_LOOKBACK_MOMENTUM_DAYS: compute_rs(daily_stock, daily_bench, length=RS_LOOKBACK_MOMENTUM_DAYS),
    }


BETA_LOOKBACK_DAYS = 260  # weekly sibling app's 52-week beta window x5, close to a full year's trading days


def compute_beta(daily_stock, daily_bench, length=BETA_LOOKBACK_DAYS):
    """Rolling daily beta vs the benchmark -- covariance(stock returns, bench
    returns) / variance(bench returns), each over a trailing `length`-day
    window. Returns a Series aligned to daily_stock's index, or None if
    either input is missing/empty."""
    if daily_stock is None or daily_bench is None or daily_stock.empty or daily_bench.empty:
        return None
    bench_close = daily_bench["close"].reindex(daily_stock.index, method="ffill")
    stock_ret = daily_stock["close"].pct_change()
    bench_ret = bench_close.pct_change()
    cov = stock_ret.rolling(length).cov(bench_ret)
    var = bench_ret.rolling(length).var()
    return cov / var
