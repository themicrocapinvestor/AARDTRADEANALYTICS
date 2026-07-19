"""Behavioral bias inference layer -- reads diagnosed trades (already scored
against Stage/composite/RS by mistake_diagnosis.py) plus each trade's fired
exit triggers (exit_triggers.py) and the raw fill sequence (tradebook_parser
.py's fills_in), and surfaces named psychological patterns (disposition
effect, revenge trading, overconfidence, anchoring, loss aversion, herding/
FOMO, sunk cost, status-quo/paralysis, endowment effect) with plain-English
evidence -- both per trade and as a whole-tradebook "Investor Behavioral
Profile".

READ-ONLY on top of existing output: never touches diagnosis, chart, or
trigger-detection code, and never invents new market data. Position size in
the strict risk-management sense (stop-distance-based sizing) isn't
available, but capital deployed per trade (quantity x entry_price) already
is -- that's what the revenge-trading size check uses.

Correlational, not diagnostic: individual causal attribution (e.g. "held
because of regret aversion" vs a rational thesis) can't be proven from
price/date data alone, so every trait reads as "consistent with X", never a
clinical claim. Scores are down-weighted by trade count so a handful of
trades never reads as a confident portfolio-wide diagnosis.
"""
import exit_triggers

# --- thresholds -- literature-grounded starting points, not fit to any one
# trader's sample. ---
DISPOSITION_RATIO_STRONG = 1.8          # hold-time ratio (losers/winners) at which disposition-effect score saturates
REVENGE_LATENCY_WINDOW_DAYS = 10        # a next entry inside this many days of a loss counts as "soon after"
REVENGE_SIZE_SPIKE_RATIO = 1.3          # capital deployed >= this x the trader's own median counts as "sized up"
IGNORED_TRIGGER_OVERCONFIDENT = 2       # >= this many independently-fired triggers ignored before exit
EXTREME_DRAWDOWN_PCT = -50.0            # held to at least this much loss from entry counts as endowment-territory
ANCHOR_BREAKEVEN_BAND_PCT = 3.0         # exit within this band of entry price -- fixated on getting back to even
ANCHOR_MIN_DAYS_HELD = 30               # only counts as anchoring if the hold was long enough to have seen better exits
LOSS_AVERSION_LAG_SATURATION_DAYS = 30  # avg days-past-first-warning at which loss-aversion score saturates
CONFIDENCE_FULL_N = 15                  # trade count at/above which confidence weighting saturates at 1.0


def capital_deployed(diag):
    return diag["quantity"] * diag["entry_price"]


def ignored_triggers_before_exit(diag, w):
    """Every independent exit-trigger occurrence that fired strictly between
    entry and the user's OWN exit date -- i.e. every warning already shown
    on the trade's chart that wasn't acted on. w: that symbol's prepared
    indicator frame (prepare_symbol_frame output), or None if unavailable."""
    if w is None:
        return []
    i_entry = w.index.searchsorted(diag["entry_date"], side="right") - 1
    if i_entry < 0:
        return []
    rows = exit_triggers.evaluate_triggers(
        w, i_entry, diag["entry_price"], diag["quantity"], diag["user_return_pct"]
    )
    ignored = []
    for row in rows:
        for occ in row["occurrences"]:
            if occ["date"] < diag["exit_date"]:
                ignored.append({"trigger": row["label"], "date": occ["date"]})
    return ignored


def is_averaging_down(raw_trade):
    """True if this trade's buy fills (tradebook_parser fills_in: [(date,
    price, qty), ...]) show a SECOND (or later) buy at a lower price than
    the first -- adding to a position that had already gone against the
    entry, the textbook sunk-cost "lower my average" move. raw_trade: the
    matching dict from tradebook_parser.build_roundtrip_trades (has
    fills_in) -- NOT the diagnosed dict, which doesn't carry fills through."""
    fills_in = (raw_trade or {}).get("fills_in") or []
    if len(fills_in) < 2:
        return False
    first_price = fills_in[0][1]
    return any(price < first_price for _, price, _ in fills_in[1:])


def _median(values):
    values = sorted(values)
    n = len(values)
    if n == 0:
        return None
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


def _confidence(n):
    return round(min(1.0, n / CONFIDENCE_FULL_N), 2)


def raw_trade_lookup(raw_trades):
    """{(symbol, entry_date, exit_date): raw trade dict} -- raw_trades is
    tradebook_parser.build_roundtrip_trades's output, matched against a
    diagnosed dict by the same three keys both share."""
    return {(t["symbol"], t["entry_date"], t["exit_date"]): t for t in (raw_trades or [])}


class Baseline:
    """Portfolio-wide reference stats every per-trade check compares
    against -- computed once per report, not per trade."""

    def __init__(self, diagnosed):
        self.by_entry = sorted(diagnosed, key=lambda d: d["entry_date"])
        self.median_capital = _median([capital_deployed(d) for d in diagnosed]) or 0
        self.n_trades = len(diagnosed)

    def prior_loss_within(self, diag, window_days):
        """Most recent trade (by exit_date, across the WHOLE portfolio --
        not just the same symbol) that closed at a loss before `diag`'s own
        entry, if its exit fell within `window_days` of this entry. None if
        no qualifying prior loss exists."""
        candidates = [
            d for d in self.by_entry
            if d is not diag and d["exit_date"] <= diag["entry_date"] and d["pnl_rupees"] < 0
        ]
        if not candidates:
            return None
        prior = max(candidates, key=lambda d: d["exit_date"])
        if (diag["entry_date"] - prior["exit_date"]).days > window_days:
            return None
        return prior


def trade_behavioral_notes(diag, baseline, w=None, raw_trade=None, currency="₹"):
    """Humanized 0-6 bullet list of behavioral traits THIS specific trade is
    evidence for -- meant to sit alongside the existing roast/aftermath
    bullets (mirror_narrative.all_bullets) when a trade is selected. Each
    bullet names the trait plainly and cites the number/date that
    triggered it, matching mirror_narrative.py's convention of never
    roasting without a real figure attached."""
    symbol = diag["symbol"]
    ignored = ignored_triggers_before_exit(diag, w)
    notes = []

    prior_loss = baseline.prior_loss_within(diag, REVENGE_LATENCY_WINDOW_DAYS)
    if prior_loss is not None:
        gap = (diag["entry_date"] - prior_loss["exit_date"]).days
        cap = capital_deployed(diag)
        size_ratio = cap / baseline.median_capital if baseline.median_capital else None
        if size_ratio is not None and size_ratio >= REVENGE_SIZE_SPIKE_RATIO:
            notes.append(
                f"**Revenge trading** -- you entered {symbol} just {gap} day(s) after {prior_loss['symbol']} "
                f"closed at a loss, sized at {size_ratio:.1f}x your usual capital per trade. That's the "
                f"'get it back fast' pattern, not a coincidence of timing."
            )
        elif (diag["entry_context"].get("clumsy_score") or 0) >= 50:
            notes.append(
                f"**Revenge trading** -- entered {symbol} {gap} day(s) after {prior_loss['symbol']}'s loss, into "
                f"a setup where most of the standard good-trade conditions weren't even in place. Reads like "
                f"chasing a fix, not a planned entry."
            )

    if len(ignored) >= IGNORED_TRIGGER_OVERCONFIDENT and diag["user_return_pct"] > EXTREME_DRAWDOWN_PCT:
        trigger_names = ", ".join(sorted({i["trigger"] for i in ignored}))
        notes.append(
            f"**Overconfidence** -- {len(ignored)} independent technical warnings fired on {symbol} before you "
            f"finally exited ({trigger_names}), and you sat through all of them. That's not missing the signal, "
            f"that's deciding you knew better."
        )

    if "chased_extended" in diag["tags"]:
        ext = diag["entry_context"].get("extension_above_sma50_pct") or 0
        notes.append(
            f"**Herding / FOMO entry** -- {symbol} was already {ext:.0f}% above its 50-day average when you "
            f"bought. Buying strength that's already run is chasing the crowd in, not spotting it early."
        )

    if is_averaging_down(raw_trade):
        prices = [p for _, p, _ in raw_trade["fills_in"]]
        notes.append(
            f"**Sunk cost / averaging down** -- you bought {symbol} again at a lower price than your first fill "
            f"({currency}{prices[0]:.2f} → {currency}{min(prices):.2f}) instead of cutting the original entry. Lowering the "
            f"average is a way of avoiding admitting the first buy was wrong."
        )

    if "bagheld_past_breakdown" in diag["tags"] and ignored:
        notes.append(
            f"**Status-quo bias** -- {symbol} had already broken down and kept firing exit signals, and you took "
            f"no action for a stretch. Not conviction, just avoiding the decision."
        )

    if diag["user_return_pct"] <= EXTREME_DRAWDOWN_PCT and len(ignored) >= IGNORED_TRIGGER_OVERCONFIDENT:
        notes.append(
            f"**Endowment effect** -- you rode {symbol} down to {diag['user_return_pct']:.0f}% through "
            f"{len(ignored)} ignored exit signals. That reads like holding because you own it, not because the "
            f"setup still made sense."
        )

    if abs(diag["user_return_pct"]) <= ANCHOR_BREAKEVEN_BAND_PCT and diag["days_in_trade"] > ANCHOR_MIN_DAYS_HELD:
        notes.append(
            f"**Anchoring** -- after {diag['days_in_trade']} days, you got out within "
            f"{ANCHOR_BREAKEVEN_BAND_PCT:.0f}% of your entry price. That's a 'just get me back to breakeven' "
            f"exit, anchored to what you paid rather than to what the chart was saying."
        )

    return notes


def investor_profile(diagnosed, symbol_frames, raw_trades=None):
    """Bottom-of-report 'Investor Behavioral Profile' -- covers every closed
    trade in the uploaded tradebook (the whole uploaded range, not a
    calendar/FY slice, since the export window is whatever the user chose
    when downloading from Console). Returns {"traits": {trait_key: {label,
    score (0-1), confidence (0-1), evidence: [bullets]}}, "dominant": [top
    trait_keys], "n_trades": int}, or None if there's nothing to profile."""
    if not diagnosed:
        return None
    baseline = Baseline(diagnosed)
    raw_by_key = raw_trade_lookup(raw_trades)
    traits = {}

    win_days = [d["days_in_trade"] for d in diagnosed if d["pnl_rupees"] > 0]
    loss_days = [d["days_in_trade"] for d in diagnosed if d["pnl_rupees"] <= 0]
    if win_days and loss_days:
        avg_win_days = sum(win_days) / len(win_days)
        avg_loss_days = sum(loss_days) / len(loss_days)
        if avg_win_days:
            ratio = avg_loss_days / avg_win_days
            score = min(1.0, max(0.0, (ratio - 1.0) / (DISPOSITION_RATIO_STRONG - 1.0)))
            traits["disposition_effect"] = {
                "label": "Disposition Effect",
                "score": round(score, 2),
                "confidence": _confidence(len(win_days) + len(loss_days)),
                "evidence": [
                    f"Held losers {ratio:.1f}x longer than winners on average ({avg_loss_days:.0f} days vs "
                    f"{avg_win_days:.0f} days, across {len(win_days)} winning and {len(loss_days)} losing trades)."
                ],
            }

    revenge_trades = []
    for d in diagnosed:
        prior = baseline.prior_loss_within(d, REVENGE_LATENCY_WINDOW_DAYS)
        if prior is None:
            continue
        ratio = capital_deployed(d) / baseline.median_capital if baseline.median_capital else None
        if ratio is not None and ratio >= REVENGE_SIZE_SPIKE_RATIO:
            revenge_trades.append((d, prior, ratio))
    if revenge_trades:
        pct = len(revenge_trades) / baseline.n_trades
        ex_d, ex_prior, ex_ratio = revenge_trades[0]
        traits["revenge_trading"] = {
            "label": "Revenge Trading",
            "score": round(min(1.0, pct / 0.3), 2),
            "confidence": _confidence(baseline.n_trades),
            "evidence": [
                f"{len(revenge_trades)} of {baseline.n_trades} trades were entered within "
                f"{REVENGE_LATENCY_WINDOW_DAYS} days of a loss, sized at {REVENGE_SIZE_SPIKE_RATIO:.1f}x+ your "
                f"median capital per trade.",
                f"Example: {ex_d['symbol']} entered {(ex_d['entry_date'] - ex_prior['exit_date']).days} day(s) "
                f"after {ex_prior['symbol']}'s loss, sized {ex_ratio:.1f}x your median.",
            ],
        }

    overconfident = []
    for d in diagnosed:
        ignored = ignored_triggers_before_exit(d, symbol_frames.get(d["symbol"]))
        if len(ignored) >= IGNORED_TRIGGER_OVERCONFIDENT:
            overconfident.append((d, ignored))
    if overconfident:
        pct = len(overconfident) / baseline.n_trades
        avg_ignored = sum(len(i) for _, i in overconfident) / len(overconfident)
        traits["overconfidence"] = {
            "label": "Overconfidence / Illusion of Control",
            "score": round(min(1.0, pct / 0.4), 2),
            "confidence": _confidence(baseline.n_trades),
            "evidence": [
                f"{len(overconfident)} of {baseline.n_trades} trades sat through {avg_ignored:.1f} independent "
                f"exit warnings on average before you finally got out."
            ],
        }

    herd = [d for d in diagnosed if "chased_extended" in d["tags"] or "wrong_stage_entry" in d["tags"]]
    if herd:
        pct = len(herd) / baseline.n_trades
        traits["herding_fomo"] = {
            "label": "Herding / FOMO Entries",
            "score": round(min(1.0, pct / 0.5), 2),
            "confidence": _confidence(baseline.n_trades),
            "evidence": [
                f"{len(herd)} of {baseline.n_trades} trades ({pct * 100:.0f}%) were bought already extended or "
                f"outside an established uptrend -- chasing strength that had already run, not spotting it early."
            ],
        }

    avg_down = [d for d in diagnosed if is_averaging_down(raw_by_key.get((d["symbol"], d["entry_date"], d["exit_date"])))]
    if avg_down:
        pct = len(avg_down) / baseline.n_trades
        traits["sunk_cost"] = {
            "label": "Sunk Cost Fallacy",
            "score": round(min(1.0, pct / 0.3), 2),
            "confidence": _confidence(baseline.n_trades),
            "evidence": [
                f"{len(avg_down)} of {baseline.n_trades} trades were added to at a lower price than the first "
                f"buy -- lowering the average instead of reassessing the original entry."
            ],
        }

    paralysis = [d for d in diagnosed if "bagheld_past_breakdown" in d["tags"]]
    if paralysis:
        pct = len(paralysis) / baseline.n_trades
        traits["status_quo"] = {
            "label": "Status-Quo Bias / Decision Paralysis",
            "score": round(min(1.0, pct / 0.3), 2),
            "confidence": _confidence(baseline.n_trades),
            "evidence": [
                f"{len(paralysis)} of {baseline.n_trades} trades were held well past a confirmed breakdown with "
                f"no exit action -- inertia, not conviction."
            ],
        }

    endowment = [d for d in diagnosed if d["user_return_pct"] <= EXTREME_DRAWDOWN_PCT]
    if endowment:
        pct = len(endowment) / baseline.n_trades
        traits["endowment_effect"] = {
            "label": "Endowment Effect",
            "score": round(min(1.0, pct / 0.15), 2),
            "confidence": _confidence(baseline.n_trades),
            "evidence": [
                f"{len(endowment)} trade(s) were ridden to a {EXTREME_DRAWDOWN_PCT:.0f}%+ loss from entry -- "
                f"holding on because it was already owned, past the point discipline alone would explain."
            ],
        }

    anchored = [
        d for d in diagnosed
        if abs(d["user_return_pct"]) <= ANCHOR_BREAKEVEN_BAND_PCT and d["days_in_trade"] > ANCHOR_MIN_DAYS_HELD
    ]
    if anchored:
        pct = len(anchored) / baseline.n_trades
        traits["anchoring"] = {
            "label": "Anchoring",
            "score": round(min(1.0, pct / 0.25), 2),
            "confidence": _confidence(baseline.n_trades),
            "evidence": [
                f"{len(anchored)} of {baseline.n_trades} trades were held {ANCHOR_MIN_DAYS_HELD}+ days and closed "
                f"within {ANCHOR_BREAKEVEN_BAND_PCT:.0f}% of entry -- fixated on getting back to what you paid, "
                f"not on what the chart was saying."
            ],
        }

    lag_losers = []
    for d in diagnosed:
        if d["pnl_rupees"] >= 0:
            continue
        ignored = ignored_triggers_before_exit(d, symbol_frames.get(d["symbol"]))
        if ignored:
            first_trigger_date = min(i["date"] for i in ignored)
            lag_losers.append((d["exit_date"] - first_trigger_date).days)
    if lag_losers:
        avg_lag = sum(lag_losers) / len(lag_losers)
        traits["loss_aversion"] = {
            "label": "Loss Aversion",
            "score": round(min(1.0, avg_lag / LOSS_AVERSION_LAG_SATURATION_DAYS), 2),
            "confidence": _confidence(len(lag_losers)),
            "evidence": [
                f"On losing trades, you held an average of {avg_lag:.0f} day(s) past the first exit warning "
                f"before actually selling ({len(lag_losers)} losing trade(s) had at least one warning fire)."
            ],
        }

    if not traits:
        return {"traits": {}, "dominant": [], "n_trades": baseline.n_trades}

    ranked = sorted(traits.items(), key=lambda kv: kv[1]["score"] * kv[1]["confidence"], reverse=True)
    dominant = [k for k, v in ranked if v["score"] * v["confidence"] > 0.15][:3]

    return {"traits": traits, "dominant": dominant, "n_trades": baseline.n_trades}
