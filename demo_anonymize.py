"""Cosmetic-only symbol anonymization for the public demo app (demo_app.py).

Runs strictly AFTER every real-data step (Kite fetch, scoring, industry
classification) is already done -- the math demo_app.py shows is identical
to app.py's, computed against the user's real symbols. This module only
swaps what gets *displayed*: the real ticker is replaced with a stable
STOCK#N label wherever a trade is rendered. Industry / industry group stay
untouched (that's the point -- the demo shows real sector composition
without real tickers). company_name and isin are dropped from anonymized
copies since they'd leak the real identity right back.
"""
import re


def build_symbol_map(diagnosed):
    """real_symbol -> "STOCK#N", assigned in order of first entry_date so a
    given portfolio always anonymizes the same way within one run/session."""
    seen = []
    for d in sorted(diagnosed, key=lambda x: x["entry_date"]):
        if d["symbol"] not in seen:
            seen.append(d["symbol"])
    return {sym: f"STOCK#{i + 1}" for i, sym in enumerate(seen)}


def _scrub_text(text, symbol_map):
    """Replace every real-symbol substring in free text with its anon label.
    Longest symbols first, and matched on word boundaries, so a short symbol
    that's a substring of a longer one never partially matches."""
    if not text:
        return text
    for real in sorted(symbol_map, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(real)}\b", symbol_map[real], text)
    return text


def anonymize_trade(d, symbol_map):
    """Display-only copy of one diagnosed/classified trade: symbol swapped,
    company_name/isin dropped, industry/industry_group left as-is."""
    anon = dict(d)
    anon["symbol"] = symbol_map[d["symbol"]]
    anon.pop("company_name", None)
    anon.pop("isin", None)
    return anon


def anonymize_bullets(bullets, symbol_map):
    return [_scrub_text(b, symbol_map) for b in bullets]


def anonymize_summary(summary, symbol_map):
    if summary is None:
        return None
    anon = dict(summary)
    anon["headline"] = _scrub_text(summary["headline"], symbol_map)
    if summary.get("worst_trade_roast"):
        anon["worst_trade_roast"] = _scrub_text(summary["worst_trade_roast"], symbol_map)
    if summary.get("worst_trade"):
        anon["worst_trade"] = anonymize_trade(summary["worst_trade"], symbol_map)
    bt = summary.get("backtest")
    if bt:
        anon_bt = dict(bt)
        anon_bt["best_trade"] = {**bt["best_trade"], "symbol": symbol_map[bt["best_trade"]["symbol"]]}
        anon_bt["worst_trade"] = {**bt["worst_trade"], "symbol": symbol_map[bt["worst_trade"]["symbol"]]}
        anon["backtest"] = anon_bt
    return anon
