"""Recently-closed trades are excluded from analysis entirely (a hard filter
before scoring/charting/profiling, not a display-only redaction) so this
tool can never be read as commentary on a live or near-live position."""
import datetime

RECENT_TRADE_LOCKOUT_DAYS = 105  # ~3.5 months


def split_recent_trades(trades, as_of=None):
    """Splits trades into (eligible, excluded) by exit_date; excluded = closed
    within RECENT_TRADE_LOCKOUT_DAYS of `as_of` (defaults to now)."""
    as_of = as_of or datetime.datetime.now()
    cutoff = as_of - datetime.timedelta(days=RECENT_TRADE_LOCKOUT_DAYS)
    eligible = [t for t in trades if t["exit_date"] < cutoff]
    excluded = [t for t in trades if t["exit_date"] >= cutoff]
    return eligible, excluded
