"""Daily candle fetch (Kite Connect's native 'day' interval) -- daily bars are
used as-is everywhere in this app, no resampling step, unlike the weekly
sibling app (kite-weekly-screener) this was forked from.

Kite's historical endpoint is rate-limited (roughly 3 req/sec in practice) and
occasionally flaky, so fetches retry with backoff -- mirroring the pattern in
the old Angel One codebase's candle.py, just against kiteconnect's exceptions
instead. Each stock's daily candles are cached to disk per calendar day so a
~750-stock scan only pays the network cost once per day, no matter how many
times you press "Run scan" in the Streamlit app afterward.

prefetch_daily_bulk() fetches many symbols concurrently (thread pool) instead
of one at a time -- Kite's ~3 req/sec cap is still enforced by _rate_limiter,
shared across every thread, so this doesn't call the API any faster than the
limit allows; it just overlaps each request's network latency with the next
one starting, instead of a single thread waiting on one full round trip
before beginning the next. Cache-hit symbols (already fresh today) skip the
limiter and the network entirely.
"""
import concurrent.futures
import datetime as dt
import json
import os
import threading
import time

import pandas as pd

from kiteconnect.exceptions import KiteException

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "candle_cache")
# 3 years of daily candles (~750 trading days) -- comfortably more than every
# indicator's warm-up need (the longest is the 252-day rolling high/low; next
# longest are BETA_LOOKBACK_DAYS=260 and TREND_SMA_SLOW=200 + BASE_MAX_DAYS=130),
# while keeping each stock's fetch/parse/compute payload smaller than the
# previous ~5-year (1800-day) window. Trade-off: the Backtest tab can now only
# walk ~2-2.5 years of live-eligible history (3 years minus ~260-day warmup)
# instead of ~4.5. Bump this back up if you want a longer backtest window.
DAILY_LOOKBACK_DAYS = 1095


def _cache_path(symbol):
    return os.path.join(CACHE_DIR, symbol.replace("/", "_") + ".json")


def _cache_is_fresh(path):
    if not os.path.exists(path):
        return False
    return dt.date.fromtimestamp(os.path.getmtime(path)) == dt.date.today()


class _RateLimiter:
    """Enforces Kite's ~3 req/sec cap across however many threads are firing
    requests concurrently -- a single shared instance, not one per thread, so
    concurrent callers still can't exceed the combined rate."""

    def __init__(self, rate_per_sec=3.0):
        self._interval = 1.0 / rate_per_sec
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_slot)
            self._next_slot = start + self._interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)


_rate_limiter = _RateLimiter(rate_per_sec=3.0)


def _fetch_with_retry(kite, token, from_date, to_date, attempts=5, base_delay=1.0):
    last_exc = None
    for attempt in range(attempts):
        _rate_limiter.wait()
        try:
            return kite.historical_data(token, from_date, to_date, "day")
        except KiteException as e:
            last_exc = e
            delay = min(base_delay * (2 ** attempt), 16.0)
            time.sleep(delay)
    raise last_exc


def fetch_daily(kite, token, symbol, use_cache=True):
    """Returns a DataFrame of daily OHLCV (date index), or None if nothing came back."""
    cp = _cache_path(symbol)
    if use_cache and _cache_is_fresh(cp):
        with open(cp) as f:
            rows = json.load(f)
    else:
        to_date = dt.date.today()
        from_date = to_date - dt.timedelta(days=DAILY_LOOKBACK_DAYS)
        raw = _fetch_with_retry(kite, token, from_date, to_date)
        rows = [
            {"date": r["date"].isoformat(), "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]}
            for r in raw
        ]
        if use_cache:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cp, "w") as f:
                json.dump(rows, f)

    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df.set_index("date", inplace=True)
    return df


def prefetch_daily_bulk(kite, symbol_token_pairs, use_cache=True, max_workers=5, on_progress=None):
    """Fetches (or loads from today's cache) every (symbol, token) pair
    concurrently, up to max_workers in flight at once. Cache-hit symbols
    return near-instantly (no network, no rate-limit wait); cache-miss
    symbols share _rate_limiter so total Kite calls still stay at ~3/sec
    regardless of how many threads are running. Returns {symbol: DataFrame
    or None}, same per-symbol value fetch_daily() would have returned.

    on_progress: optional callback(done, total) invoked after each symbol
    completes -- called from a worker thread, so the caller must only do
    thread-safe work in it (e.g. updating a plain counter/Streamlit progress
    bar, not touching shared mutable state without a lock)."""
    results = {}
    total = len(symbol_token_pairs)
    done_lock = threading.Lock()
    done = 0

    def _one(symbol, token):
        return symbol, fetch_daily(kite, token, symbol, use_cache=use_cache)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_one, symbol, token) for symbol, token in symbol_token_pairs]
        for future in concurrent.futures.as_completed(futures):
            symbol, daily = future.result()
            results[symbol] = daily
            if on_progress is not None:
                with done_lock:
                    done += 1
                    d = done
                on_progress(d, total)
    return results
