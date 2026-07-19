"""Compliance guardrail shared by app.py and demo_app.py: trades that closed
too recently are excluded from analysis entirely, so this tool can never be
read as commentary on a live or near-live position -- see
RECENT_TRADE_LOCKOUT_DAYS. This is a hard filter applied to the trade list
before anything else touches it (scoring, charting, behavioral profiling),
not a display-only redaction -- excluded trades are never fetched, scored,
or shown anywhere in the app.
"""
import datetime

RECENT_TRADE_LOCKOUT_DAYS = 105  # ~3.5 months


def split_recent_trades(trades, as_of=None):
    """Splits closed round-trip trades (tradebook_parser.build_roundtrip_trades
    output) into (eligible, excluded) by exit_date. `excluded` holds every
    trade that closed within the last RECENT_TRADE_LOCKOUT_DAYS days of
    `as_of` (defaults to now) -- those are dropped before any fetching,
    scoring, or display happens."""
    as_of = as_of or datetime.datetime.now()
    cutoff = as_of - datetime.timedelta(days=RECENT_TRADE_LOCKOUT_DAYS)
    eligible = [t for t in trades if t["exit_date"] < cutoff]
    excluded = [t for t in trades if t["exit_date"] >= cutoff]
    return eligible, excluded
