"""Parses Zerodha Console's Tradebook export (Console -> Reports -> Tradebook,
CSV/Excel, any date range up to 12 months) into round-trip trades.

This is NOT available from Kite Connect -- the Connect API's order book only
returns the CURRENT day's orders, and there's no historical trades endpoint.
The Tradebook is a separate manual export from Console, so this module reads
whatever column names that export actually uses rather than assuming a fixed
Kite Connect response shape.

Console's Tradebook is a stream of individual FILLS (one row per execution),
not trades -- a single "trade" the user thinks of as one buy-and-sell can be
many fill rows on both legs (partial fills, scale-ins, scale-outs). This
module FIFO-matches buy fills against sell fills per symbol to reconstruct
actual round-trip positions, mirroring unified_backtest.py's fills_in/
fills_out/average_entry shape so the rest of the app (mistake_diagnosis.py)
can reuse that same trade dict convention.
"""
import io

import pandas as pd

# Console's Tradebook column names have varied across export format tweaks --
# map every variant seen to one canonical name. Matched case-insensitively
# after stripping whitespace/underscores, so "Trade Date", "trade_date", and
# "TRADE DATE" all resolve the same way.
_COLUMN_ALIASES = {
    "symbol": "symbol",
    "tradingsymbol": "symbol",
    "instrument": "symbol",
    "trade_date": "trade_date",
    "tradedate": "trade_date",
    "trade_type": "trade_type",
    "tradetype": "trade_type",
    "type": "trade_type",
    "quantity": "quantity",
    "qty": "quantity",
    "price": "price",
    "order_execution_time": "order_execution_time",
    "orderexecutiontime": "order_execution_time",
    "exchange": "exchange",
    "segment": "segment",
}

REQUIRED_CANONICAL = ["symbol", "trade_date", "trade_type", "quantity", "price"]


def _canonicalize_columns(df):
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower().replace(" ", "_")
        if key in _COLUMN_ALIASES:
            rename[col] = _COLUMN_ALIASES[key]
    return df.rename(columns=rename)


def _read_one(uploaded_file):
    """uploaded_file: a Streamlit UploadedFile (has .name, file-like) or a
    plain file path string. Returns a normalized fills DataFrame with columns
    symbol/trade_date/trade_type/quantity/price, or raises ValueError if the
    file doesn't look like a Tradebook export at all."""
    name = getattr(uploaded_file, "name", str(uploaded_file)).lower()
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded_file)
    else:
        df = pd.read_csv(uploaded_file)
    df = _canonicalize_columns(df)
    missing = [c for c in REQUIRED_CANONICAL if c not in df.columns]
    if missing:
        raise ValueError(
            f"missing column(s) {missing} -- doesn't look like a Zerodha Console "
            f"Tradebook export (Console -> Reports -> Tradebook)"
        )
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.tz_localize(None).dt.normalize()
    df["trade_type"] = df["trade_type"].astype(str).str.strip().str.upper()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["symbol", "trade_date", "trade_type", "quantity", "price"])
    return df[REQUIRED_CANONICAL]


def load_tradebook(uploaded_files):
    """uploaded_files: list of uploaded Tradebook files (typically one, but
    a user may hand over separate exports for different date ranges).
    Returns (fills_df, errors) -- errors is [(filename, message)] for files
    that couldn't be parsed, same "report and skip, never raise on one bad
    file" convention as manual_universe.load_manual_universe."""
    frames, errors = [], []
    for f in uploaded_files:
        try:
            frames.append(_read_one(f))
        except Exception as e:
            errors.append((getattr(f, "name", str(f)), str(e)))
    if not frames:
        return pd.DataFrame(columns=REQUIRED_CANONICAL), errors
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    return combined, errors


def build_roundtrip_trades(fills_df):
    """FIFO-matches BUY fills against SELL fills, per symbol, into round-trip
    trade dicts. A position that returns to flat (0 qty) closes one trade;
    further buys after that start a new one. Handles scale-ins (multiple
    buys before flat) and scale-outs (multiple sells draining one position)
    by keeping fills_in/fills_out lists and a quantity-weighted average
    entry/exit, same shape unified_backtest._finalize_trade produces.

    A SELL that exceeds the currently open quantity (short sale, or a fill
    the tradebook window doesn't have the matching buy for -- e.g. a
    position opened before the uploaded date range) is capped to the
    available open quantity; the excess is dropped and counted in
    `unmatched_sell_qty` per symbol, reported back rather than raised, since
    one ragged edge shouldn't sink the whole reconstruction.

    Returns (trades, unmatched) -- trades: list of dicts (symbol, entry_date,
    entry_price, exit_date, exit_price, quantity, pnl_rupees, return_pct,
    days_in_trade, fills_in, fills_out). unmatched: {symbol: qty} for any
    SELL quantity that couldn't be matched to a prior BUY.
    """
    trades = []
    unmatched = {}

    for symbol, group in fills_df.groupby("symbol"):
        group = group.sort_values("trade_date")
        open_lots = []  # list of [date, price, qty] -- FIFO queue, mutated in place
        fills_in, fills_out = [], []

        def _flatten():
            if not fills_in or not fills_out:
                return
            total_in_qty = sum(q for _, _, q in fills_in)
            total_out_qty = sum(q for _, _, q in fills_out)
            avg_entry = sum(p * q for _, p, q in fills_in) / total_in_qty
            avg_exit = sum(p * q for _, p, q in fills_out) / total_out_qty
            entry_date = fills_in[0][0]
            exit_date = fills_out[-1][0]
            matched_qty = min(total_in_qty, total_out_qty)
            pnl_rupees = matched_qty * (avg_exit - avg_entry)
            trades.append({
                "symbol": symbol,
                "entry_date": entry_date,
                "entry_price": round(avg_entry, 2),
                "exit_date": exit_date,
                "exit_price": round(avg_exit, 2),
                "quantity": matched_qty,
                "pnl_rupees": round(pnl_rupees, 2),
                "return_pct": round((avg_exit - avg_entry) / avg_entry * 100, 2) if avg_entry else None,
                "days_in_trade": (exit_date - entry_date).days,
                "fills_in": list(fills_in),
                "fills_out": list(fills_out),
            })

        for _, row in group.iterrows():
            date, ttype, qty, price = row["trade_date"], row["trade_type"], row["quantity"], row["price"]
            if ttype == "BUY":
                open_lots.append([date, price, qty])
                fills_in.append((date, price, qty))
            elif ttype == "SELL":
                remaining = qty
                sell_qty_matched = 0
                while remaining > 0 and open_lots:
                    lot = open_lots[0]
                    take = min(remaining, lot[2])
                    sell_qty_matched += take
                    lot[2] -= take
                    remaining -= take
                    if lot[2] <= 0:
                        open_lots.pop(0)
                if sell_qty_matched > 0:
                    fills_out.append((date, price, sell_qty_matched))
                if remaining > 0:
                    unmatched[symbol] = unmatched.get(symbol, 0) + remaining
                if not open_lots:
                    # Position flat -- close out this round trip, reset for the next one.
                    _flatten()
                    fills_in, fills_out = [], []

        # Anything still open at the end of the uploaded window is a live
        # position, not a completed round trip -- deliberately not appended
        # to `trades` here (mistake_diagnosis only scores CLOSED trades;
        # an open position hasn't had a chance to be "right" or "wrong" yet).

    return trades, unmatched
