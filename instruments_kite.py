"""Kite NSE instrument master: fetch, cache, symbol -> token map.

Cached to disk for a few hours since it's a multi-MB daily-refreshed dump."""
import datetime as dt
import json
import os

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kite_instruments_nse_cache.json")
CACHE_MAX_AGE_SECONDS = 12 * 60 * 60


def _cache_is_fresh(path):
    if not os.path.exists(path):
        return False
    return (dt.datetime.now().timestamp() - os.path.getmtime(path)) < CACHE_MAX_AGE_SECONDS


def fetch_nse_instruments(kite, use_cache=True):
    if use_cache and _cache_is_fresh(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    data = kite.instruments("NSE")
    for row in data:
        row.pop("expiry", None)  # datetime.date isn't JSON serializable; unused for cash equities
    if use_cache:
        with open(CACHE_PATH, "w") as f:
            json.dump(data, f)
    return data


def build_symbol_token_map(instruments):
    """Restricted to plain NSE cash equities -- excludes indices/ETFs/etc."""
    return {
        row["tradingsymbol"]: row["instrument_token"]
        for row in instruments
        if row.get("segment") == "NSE" and row.get("instrument_type") == "EQ"
    }


def find_index_token(instruments, tradingsymbol="NIFTY 500"):
    """Returns None if not found, so RS just gets skipped rather than the app breaking."""
    for row in instruments:
        if row.get("segment") == "INDICES" and row.get("tradingsymbol") == tradingsymbol:
            return row["instrument_token"]
    return None
