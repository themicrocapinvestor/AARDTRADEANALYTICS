"""Kite NSE instrument master: fetch, cache, symbol -> token map.

`kite.instruments("NSE")` returns the full daily-refreshed dump (every listed
NSE instrument -- equities, indices, ETFs). Cached to disk for a few hours
since it's a multi-MB pull and only changes once a day; every script/page in
this app reuses the same cache within that window instead of re-fetching.
"""
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
    """{tradingsymbol: instrument_token} restricted to plain NSE cash equities
    (segment == 'NSE', instrument_type == 'EQ') -- excludes indices/ETFs/etc."""
    return {
        row["tradingsymbol"]: row["instrument_token"]
        for row in instruments
        if row.get("segment") == "NSE" and row.get("instrument_type") == "EQ"
    }


def find_index_token(instruments, tradingsymbol="NIFTY 500"):
    """Instrument token for an NSE index (segment 'INDICES') by exact
    tradingsymbol match -- used to fetch the NIFTY 500 index as the
    relative-strength benchmark (see relative_strength.py). Returns None if
    not found, so RS just gets skipped rather than the app breaking."""
    for row in instruments:
        if row.get("segment") == "INDICES" and row.get("tradingsymbol") == tradingsymbol:
            return row["instrument_token"]
    return None
