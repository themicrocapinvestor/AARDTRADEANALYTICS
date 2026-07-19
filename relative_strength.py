"""Relative strength vs a benchmark index (NIFTY 500):
    RS = (price / price[length]) / (bench / bench[length]) - 1
Entry filter requires BOTH the 60-day and 123-day windows to show RS > 0,
so a stock that only looks strong on one horizon doesn't pass alone."""
RS_LOOKBACK_SHORT_DAYS = 60
RS_LOOKBACK_LONG_DAYS = 123
RS_LOOKBACK_MOMENTUM_DAYS = 30  # faster RS read for daily_base.momentum_score's ranking only


def compute_rs(daily_stock, daily_bench, length=RS_LOOKBACK_LONG_DAYS):
    """Returns None if either input is missing/empty -- callers treat that as
    "RS unknown, skip the filter" rather than fatal."""
    if daily_stock is None or daily_bench is None or daily_stock.empty or daily_bench.empty:
        return None
    bench_close = daily_bench["close"].reindex(daily_stock.index, method="ffill")
    stock_close = daily_stock["close"]
    return (stock_close / stock_close.shift(length)) / (bench_close / bench_close.shift(length)) - 1


def compute_rs_all(daily_stock, daily_bench):
    """Returns {60: series_or_None, 123: series_or_None, 30: series_or_None}."""
    return {
        RS_LOOKBACK_SHORT_DAYS: compute_rs(daily_stock, daily_bench, length=RS_LOOKBACK_SHORT_DAYS),
        RS_LOOKBACK_LONG_DAYS: compute_rs(daily_stock, daily_bench, length=RS_LOOKBACK_LONG_DAYS),
        RS_LOOKBACK_MOMENTUM_DAYS: compute_rs(daily_stock, daily_bench, length=RS_LOOKBACK_MOMENTUM_DAYS),
    }


BETA_LOOKBACK_DAYS = 260  # close to a full year's trading days


def compute_beta(daily_stock, daily_bench, length=BETA_LOOKBACK_DAYS):
    """Rolling daily beta: covariance(stock returns, bench returns) /
    variance(bench returns) over a trailing `length`-day window."""
    if daily_stock is None or daily_bench is None or daily_stock.empty or daily_bench.empty:
        return None
    bench_close = daily_bench["close"].reindex(daily_stock.index, method="ffill")
    stock_ret = daily_stock["close"].pct_change()
    bench_ret = bench_close.pct_change()
    cov = stock_ret.rolling(length).cov(bench_ret)
    var = bench_ret.rolling(length).var()
    return cov / var
