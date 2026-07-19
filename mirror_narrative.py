"""Turns mistake_diagnosis.py's tagged trade dicts into the brutal-mirror
copy -- deliberately mocking, but every line cites a real number/date/level
from the diagnosis, never a generic insult and never the raw internal
condition/checklist names the scoring runs on internally. Tone and
plain-English translation both live here, decoupled from the scoring math
in mistake_diagnosis.py.

all_bullets() is the one function app.py actually renders per trade: a flat
7-8 bullet list mixing the entry mistake, the exit-timing verdict, and a
genuine post-exit price-action story (what carried the move, and the actual
date/price/level where the trend broke) -- not three separate jargon-heavy
sections.
"""
import random

# Human labels for every tag code -- used anywhere a tag is displayed (the
# verdict badge, the summary table, the mistake-frequency chart) so raw
# snake_case identifiers never reach the screen.
TAG_LABELS = {
    "chased_extended": "Chased an extended move",
    "wrong_stage_entry": "Bought outside an uptrend",
    "weak_rs_entry": "Bought a relative laggard",
    "clumsy_entry": "Messy setup at entry",
    "no_stop_discipline": "No stop discipline",
    "panic_exit": "Exited early",
    "bagheld_past_breakdown": "Held past the breakdown",
    "clean": "Clean trade",
}


def humanize_tag(tag):
    return TAG_LABELS.get(tag, tag.replace("_", " ").capitalize())


# One or more template lines per tag -- {symbol}/{entry_date}/etc. are filled
# from the diagnosed trade dict. Picked with a per-trade-seeded random choice
# so the same trade always reads the same way across a re-render, but
# different trades with the same tag don't all read identically.
_TAG_LINES = {
    "chased_extended": [
        "You bought {symbol} {extension:.0f}% above its own 50-day average -- that's not an entry, that's chasing.",
        "{symbol} was already stretched {extension:.0f}% above its 50-day average when you clicked buy. You weren't early.",
    ],
    "wrong_stage_entry": [
        "{symbol} wasn't in an established uptrend when you bought it -- price and its own moving averages weren't aligned yet.",
        "You bought {symbol} before the trend had actually turned up -- the averages were still pointing the wrong way.",
    ],
    "weak_rs_entry": [
        "{symbol} had been lagging the Nifty 500 for months before you bought it -- you picked a laggard and hoped it would lead.",
        "Relative strength on {symbol} (60-day, vs Nifty 500) was negative at entry. You bought weakness and called it a dip.",
    ],
    "panic_exit": [
        "You sold {symbol} on {exit_date} for {user_return:+.1f}%. A plain trailing stop wouldn't have touched you until {sys_return:+.1f}% -- you left roughly {currency}{impact:,.0f} on the table because you got scared, not because the trade actually broke down.",
        "Nothing in the chart stopped you out of {symbol} -- you stopped yourself out, {gap:.1f} points earlier than a mechanical trailing stop would have, handing back roughly {currency}{impact:,.0f} for no structural reason.",
    ],
    "bagheld_past_breakdown": [
        "{symbol} had already rolled over well before you finally sold. You didn't get stopped out -- you just eventually gave up.",
        "You watched {symbol} break down and held anyway. Hope is not a stop-loss.",
    ],
    "no_stop_discipline": [
        "You rode {symbol} down {loss:.1f}% -- a basic trailing stop caps that around {stop_pct:.0f}%. Nobody made you hold past your own stop.",
    ],
    "clumsy_entry": [
        "Almost nothing about {symbol}'s setup was working in your favor at entry -- trend, momentum and volume were all pointing the wrong way at once.",
        "{symbol} had very little going for it technically when you bought -- this wasn't a high-quality setup to begin with.",
    ],
    "clean": [
        "{symbol}: entered in trend, exited on structure. No notes here.",
    ],
}

_SUMMARY_OPENERS = [
    "Here's the number that should sting: ",
    "Before the excuses start, look at this: ",
    "No sugar-coating this one: ",
]


def _pick(tag, symbol, lines_dict=None):
    lines = (lines_dict or _TAG_LINES).get(tag, [])
    if not lines:
        return None
    rnd = random.Random(symbol + tag)  # stable per (symbol, tag), not per render
    return rnd.choice(lines)


def roast_trade(diag, clean_count=0, currency="₹"):
    """Plain-English bullet(s) about the ENTRY/EXIT decision -- one per
    non-'clean' tag (a trade can have multiple mistakes stacked)."""
    lines = []
    entry_ctx, systematic = diag["entry_context"], diag["systematic"]
    for tag in diag["tags"]:
        template = _pick(tag, diag["symbol"])
        if template is None:
            continue
        try:
            line = template.format(
                symbol=diag["symbol"],
                extension=entry_ctx.get("extension_above_sma50_pct") or 0,
                exit_date=f'{diag["exit_date"]:%d %b %Y}',
                user_return=diag["user_return_pct"],
                sys_return=systematic["return_pct"] if systematic else 0,
                gap=(systematic["return_pct"] - diag["user_return_pct"]) if systematic else 0,
                impact=abs(diag["impact_rupees"]),
                loss=-diag["user_return_pct"],
                stop_pct=15,  # STOP_TRAIL_PCT mirrored here for the copy; see unified_backtest.STOP_TRAIL_PCT
                count=clean_count,
                currency=currency,
            )
        except (KeyError, ValueError):
            continue
        lines.append(line)
    return lines


def aftermath_story_bullets(diag, currency="₹"):
    """The actual price-action postmortem: what carried the stock after the
    user's exit, and -- concretely, with a real date/price/level -- where
    the trend broke. Built entirely from mistake_diagnosis._aftermath_context
    facts (peak/trough/trend_break), never from internal condition/score
    names. This is the "what happened to the trade after exit" analysis,
    independent of whether the exit itself was a good decision."""
    a = diag.get("aftermath") or {}
    tags = a.get("aftermath_tags", [])
    symbol = diag["symbol"]
    bullets = []

    if "too_recent" in tags:
        return bullets

    peak, trough, brk = a.get("peak"), a.get("trough"), a.get("trend_break")
    run_pct, dd_pct = a.get("max_run_pct"), a.get("max_drawdown_pct")
    brk_mentioned = False

    if "sold_too_early" in tags and peak:
        bullets.append(
            f"After you sold, {symbol} wasn't done -- it kept climbing to {currency}{peak['price']:,.0f} by "
            f"{peak['date']:%d %b %Y}, {run_pct:+.1f}% above where you got out. That's a move you missed."
        )
        if peak.get("rsi") is not None and peak["rsi"] >= 70:
            bullets.append(
                f"By the time it topped out, RSI had touched {peak['rsi']:.0f} -- well into overbought "
                f"territory, which is usually about where a run like that starts to run out of road anyway."
            )
        if brk:
            bullets.append(
                f"Here's where it actually turned: on {brk['date']:%d %b %Y}, {symbol} closed at "
                f"{currency}{brk['price']:,.0f}, below its own 20-day average for the first time since the top -- "
                f"{brk['pct_off_peak']:.1f}% off the high, just {brk['days_after_peak']} trading day(s) later. "
                f"A simple trailing exit would have caught you right around there."
            )
            brk_mentioned = True
        else:
            bullets.append(
                f"As of the latest close, {symbol} is still holding above its 20-day average -- "
                f"the move hasn't technically broken down yet, so there's no clean exit level to point to."
            )

    if "exit_vindicated" in tags and trough:
        bullets.append(
            f"This one worked out, though: after you sold, {symbol} dropped {dd_pct:.1f}% to "
            f"{currency}{trough['price']:,.0f} by {trough['date']:%d %b %Y}. Whatever the reason, that exit saved you real money."
        )
        if brk and brk["date"] <= trough["date"] and not brk_mentioned:
            bullets.append(
                f"And the warning was there in advance -- it closed at {currency}{brk['price']:,.0f}, below its 20-day "
                f"average, on {brk['date']:%d %b %Y}, well before the worst of the drop. That level alone "
                f"would have confirmed you were right to be out."
            )

    if "dead_money" in tags:
        bullets.append(
            f"Nothing much happened to {symbol} after you sold -- basically flat over the following month. "
            f"Not a mistake, not a story either; just a stock you correctly stopped watching."
        )

    return bullets


def all_bullets(diag, clean_count=0, currency="₹"):
    """The single flat bullet list app.py renders per trade -- entry/exit
    decision quality first, then the post-exit price-action story, in one
    voice. This replaces the old three-separate-sections layout (roast /
    condition checklist / aftermath) with one clean list."""
    bullets = roast_trade(diag, clean_count=clean_count, currency=currency) + aftermath_story_bullets(diag, currency=currency)
    return bullets


def mistake_frequency(diagnosed):
    """{tag: count} across every diagnosed trade, sorted worst (most
    frequent, excluding 'clean') first."""
    freq = {}
    for d in diagnosed:
        for tag in d["tags"]:
            freq[tag] = freq.get(tag, 0) + 1
    return dict(sorted(freq.items(), key=lambda kv: (kv[0] == "clean", -kv[1])))


def leaderboard(diagnosed, worst_n=10):
    """Worst-first by impact_rupees (rupees left on the table vs. the
    systematic replay) -- this is what should lead the report, not an
    average across everything, so the single dumbest trade isn't buried."""
    ranked = sorted(diagnosed, key=lambda d: d["impact_rupees"], reverse=True)
    return ranked[:worst_n]


def backtest_stats(diagnosed):
    """Win rate, risk:reward, expectancy, and days-held spread across every
    closed trade -- computed straight from each trade's own entry/exit price
    and date, independent of the systematic-replay comparison the rest of
    this report is built around."""
    if not diagnosed:
        return None
    n = len(diagnosed)
    wins = [d for d in diagnosed if d["pnl_rupees"] > 0]
    losses = [d for d in diagnosed if d["pnl_rupees"] <= 0]
    win_rate = round(100 * len(wins) / n, 1)
    avg_win_pct = round(sum(d["user_return_pct"] for d in wins) / len(wins), 1) if wins else 0.0
    avg_loss_pct = round(sum(d["user_return_pct"] for d in losses) / len(losses), 1) if losses else 0.0
    risk_reward = round(avg_win_pct / abs(avg_loss_pct), 2) if avg_loss_pct else None
    expectancy_pct = round((win_rate / 100) * avg_win_pct + (1 - win_rate / 100) * avg_loss_pct, 1)
    days = [d["days_in_trade"] for d in diagnosed]
    best = max(diagnosed, key=lambda d: d["user_return_pct"])
    worst = min(diagnosed, key=lambda d: d["user_return_pct"])
    return {
        "n_trades": n, "n_wins": len(wins), "n_losses": len(losses),
        "win_rate": win_rate, "avg_win_pct": avg_win_pct, "avg_loss_pct": avg_loss_pct,
        "risk_reward": risk_reward, "expectancy_pct": expectancy_pct,
        "avg_days_held": round(sum(days) / n, 1), "min_days_held": min(days), "max_days_held": max(days),
        "best_trade": {"symbol": best["symbol"], "return_pct": best["user_return_pct"]},
        "worst_trade": {"symbol": worst["symbol"], "return_pct": worst["user_return_pct"]},
    }


def summary_roast(diagnosed, currency="₹"):
    """Headline stats for the top of the report: total left on the table
    (sum of positive impact_rupees only -- negative impact means the
    user's own exit beat the systematic replay, that's not a mistake to
    roast), mistake frequency, and the single worst trade."""
    if not diagnosed:
        return None
    total_left_on_table = sum(d["impact_rupees"] for d in diagnosed if d["impact_rupees"] > 0)
    total_pnl = sum(d["pnl_rupees"] for d in diagnosed)
    freq = mistake_frequency(diagnosed)
    worst = max(diagnosed, key=lambda d: d["impact_rupees"], default=None)
    n_clean = freq.get("clean", 0)
    n_total = len(diagnosed)

    opener = random.Random(str(n_total)).choice(_SUMMARY_OPENERS)
    trade_word = "trade" if n_total == 1 else "trades"
    headline = (
        f"{opener}across {n_total} closed {trade_word}, you left approximately "
        f"{currency}{total_left_on_table:,.0f} on the table versus what a plain, boring, "
        f"mechanical trailing-stop system would have banked on the exact same entries. "
        f"Only {n_clean} of {n_total} {trade_word} have nothing to say for themselves."
    )
    worst_line = None
    if worst is not None and worst["impact_rupees"] > 0:
        roast_lines = roast_trade(worst, currency=currency)
        worst_line = roast_lines[0] if roast_lines else None

    return {
        "headline": headline,
        "total_left_on_table": round(total_left_on_table, 2),
        "total_pnl": round(total_pnl, 2),
        "mistake_frequency": freq,
        "worst_trade": worst,
        "worst_trade_roast": worst_line,
        "backtest": backtest_stats(diagnosed),
    }


def top_bottom_trades(diagnosed, n=3):
    """Best n_trades / worst n_trades by user_return_pct (not impact_rupees --
    leaderboard() above is about the biggest MISTAKES vs. the systematic
    replay; this is just "which trades actually did best/worst for you,
    full stop"). Returns (best, worst), each sorted so the most extreme
    trade comes first; both lists are capped at n even if there are fewer
    than 2n trades total (a trade can appear in both if there are <= n
    trades overall -- acceptable for a small trade count, not hidden)."""
    if not diagnosed:
        return [], []
    best = sorted(diagnosed, key=lambda d: d["user_return_pct"], reverse=True)[:n]
    worst = sorted(diagnosed, key=lambda d: d["user_return_pct"])[:n]
    return best, worst


def _benchmark_monthly_returns(benchmark_daily):
    """{(year, month): pct_return} for every calendar month present in
    benchmark_daily's index -- each month's return is its last close vs.
    the PRIOR month's last close (proper month-over-month return, not just
    first-vs-last-within-the-month, which would miss the gap over month
    boundaries). The very first month in the series has no prior close to
    compare against, so it's computed against its own first close instead
    (the only bar with no better reference available) and excluded from
    ranking is left to the caller, not enforced here."""
    if benchmark_daily is None or benchmark_daily.empty:
        return {}
    grouped = benchmark_daily["close"].groupby(
        [benchmark_daily.index.year, benchmark_daily.index.month]
    ).agg(["first", "last"])
    result = {}
    prior_close = None
    for (y, m), row in grouped.iterrows():
        base = prior_close if prior_close is not None else row["first"]
        if base:
            result[(y, m)] = round((row["last"] / base - 1) * 100, 2)
        prior_close = row["last"]
    return result


def monthly_returns(diagnosed, benchmark_daily=None):
    """Groups closed trades by EXIT month (that's when the P&L actually
    landed) and returns one dict per month that had at least one trade,
    sorted chronologically: {month_label ('YYYY-MM'), n_trades,
    avg_return_pct (plain mean of user_return_pct for trades exiting that
    month -- same convention as backtest_stats' expectancy_pct, just
    sliced by month), total_pnl_rupees, nifty_return_pct (None if no
    benchmark data was available for that month)}."""
    buckets = {}
    for d in diagnosed:
        key = (d["exit_date"].year, d["exit_date"].month)
        buckets.setdefault(key, []).append(d)

    bench_monthly = _benchmark_monthly_returns(benchmark_daily)

    months = []
    for (y, m), trades in buckets.items():
        months.append({
            "month_label": f"{y}-{m:02d}",
            "n_trades": len(trades),
            "avg_return_pct": round(sum(t["user_return_pct"] for t in trades) / len(trades), 1),
            "total_pnl_rupees": round(sum(t["pnl_rupees"] for t in trades), 2),
            "nifty_return_pct": bench_monthly.get((y, m)),
        })
    months.sort(key=lambda x: x["month_label"])
    return months


def best_worst_months(diagnosed, benchmark_daily=None, n=3):
    """(best, worst) month dicts from monthly_returns(), ranked by
    avg_return_pct -- best first in the best list, worst first in the
    worst list. Both capped at n even if there are fewer than 2n distinct
    months (a month can appear in both lists if there are <= n months
    total)."""
    months = monthly_returns(diagnosed, benchmark_daily)
    best = sorted(months, key=lambda x: x["avg_return_pct"], reverse=True)[:n]
    worst = sorted(months, key=lambda x: x["avg_return_pct"])[:n]
    return best, worst


_SEVERITY_WORDS = {
    (0.7, 1.01): "This one's not subtle -- ",
    (0.4, 0.7): "This shows up clearly -- ",
    (0.0, 0.4): "There's a mild version of this here -- ",
}

# Within one trait's own evidence sentences (rare -- most traits cite just one).
_JOIN_WORDS = ["On top of that, ", "And ", "Alongside that, ", "There's also this: "]
# Between two DIFFERENT traits in the same paragraph -- a separate pool so a
# profile with several dominant traits doesn't repeat the same transition
# (or the same severity phrase) three times in a row.
_TRAIT_JOIN_WORDS = ["Then there's the fact that ", "On top of that, ", "Add to that: ", "And separately, "]


def _severity_lead(score):
    for (lo, hi), text in _SEVERITY_WORDS.items():
        if lo <= score < hi:
            return text
    return ""


def _lowercase_first(s):
    return s[0].lower() + s[1:] if s else s


def _clean_evidence(e):
    """"Example: X" reads like a caption, not a sentence -- rephrase it as a
    clause that actually joins the flow of a paragraph. NOT lowercased --
    what follows is usually a ticker/proper noun (e.g. "RELIANCE entered
    ..."), and lowercasing its first letter would mangle it."""
    if e.startswith("Example: "):
        return "for instance, " + e[len("Example: "):]
    return e


def _join_evidence(evidence_sentences, seed):
    """Stitches a trait's own evidence bullet(s) into flowing prose instead
    of a list -- the first sentence stands alone, any further sentences for
    the SAME trait get a light connector picked from _JOIN_WORDS (seeded so
    a given profile always reads the same way on re-render, matching
    _pick()'s convention elsewhere in this module)."""
    if not evidence_sentences:
        return ""
    cleaned = [_clean_evidence(e) for e in evidence_sentences]
    rnd = random.Random(seed)
    out = cleaned[0]
    for e in cleaned[1:]:
        out += " " + rnd.choice(_JOIN_WORDS) + _lowercase_first(e)
    return out


def _paragraph_from_traits(traits_list, lead_with_severity):
    """Joins several traits' evidence into one paragraph body: the first
    trait gets a severity-scaled lead-in (if lead_with_severity), every
    later trait gets a transition connector instead of repeating that lead
    -- prevents e.g. three dominant traits all opening with "This one's not
    subtle" back to back. Connectors cycle by position (not seeded random)
    so two ADJACENT traits never get the same one, which a random pick
    could otherwise coincidentally produce."""
    parts = []
    for i, t in enumerate(traits_list):
        ev = _join_evidence(t["evidence"], seed=t["label"])
        if i == 0 and lead_with_severity:
            parts.append(_severity_lead(t["score"]) + _lowercase_first(ev))
        elif i == 0:
            parts.append(ev)
        else:
            connector = _TRAIT_JOIN_WORDS[(i - 1) % len(_TRAIT_JOIN_WORDS)]
            parts.append(connector + _lowercase_first(ev))
    return " ".join(parts)


def _and_join_bold(labels):
    """['A'] -> '**A**'; ['A','B'] -> '**A** and **B**';
    ['A','B','C'] -> '**A**, **B**, and **C**' (Oxford comma)."""
    bolded = [f"**{label}**" for label in labels]
    if len(bolded) == 1:
        return bolded[0]
    if len(bolded) == 2:
        return f"{bolded[0]} and {bolded[1]}"
    return ", ".join(bolded[:-1]) + f", and {bolded[-1]}"


def investor_narrative(investor_profile):
    """Turns behavioral_profile.investor_profile()'s trait scorecard into
    flowing prose -- 2-3 paragraphs in the same second-person, evidence-
    cited voice as the rest of this app's copy (every claim still traces
    back to a real count/percentage from the evidence strings, nothing
    added here is invented), instead of a bare scorecard + progress bars +
    a dropdown someone has to click through to see the reasoning. Returns a
    list of paragraph strings (app.py renders one st.markdown per
    paragraph), or [] if there's nothing to profile.

    Paragraph 1 covers the dominant pattern(s) (the ones investor_profile()
    already flagged as dominant -- score x confidence > 0.15). Paragraph 2,
    if there's anything left, covers secondary patterns that showed up but
    didn't rise to "dominant." A closing paragraph carries the same
    correlational-not-clinical caveat this section has always shown."""
    if investor_profile is None or not investor_profile["traits"]:
        return []

    traits = investor_profile["traits"]
    n_trades = investor_profile["n_trades"]
    ranked = sorted(traits.values(), key=lambda v: v["score"] * v["confidence"], reverse=True)
    dominant = [traits[k] for k in investor_profile["dominant"]]
    if not dominant:
        dominant = ranked[:1]
    dominant_labels = {t["label"] for t in dominant}
    secondary = [v for v in ranked if v["label"] not in dominant_labels and v["score"] * v["confidence"] > 0.08]

    paragraphs = []

    if len(dominant) == 1:
        opener = f"The clearest pattern in this trade history is {_and_join_bold([dominant[0]['label']])}. "
    else:
        word = "Two patterns" if len(dominant) == 2 else "A few patterns"
        opener = f"{word} dominate this trade history: {_and_join_bold([t['label'] for t in dominant])}. "
    paragraphs.append(opener + _paragraph_from_traits(dominant, lead_with_severity=True))

    if secondary:
        names = _and_join_bold([t["label"] for t in secondary])
        verb = "shows" if len(secondary) == 1 else "show"
        lead = f"That's not the whole picture, though -- {names} {verb} up too, less dominant but still real. "
        paragraphs.append(lead + _paragraph_from_traits(secondary, lead_with_severity=False))

    trade_word = "trade" if n_trades == 1 else "trades"
    paragraphs.append(
        f"None of this is a clinical read on you -- these are behavioral signatures consistent with "
        f"well-known biases, correlational patterns in the numbers, not proof of intent. Scores are "
        f"down-weighted automatically when there isn't much trade history behind them, so a pattern "
        f"with low confidence is a hint worth watching, not a verdict. This covers all {n_trades} "
        f"closed {trade_word} in this upload."
    )
    return paragraphs


def build_report(diagnosed, currency="₹"):
    """Full structure app.py renders: aggregate summary, plus every
    diagnosed trade (app.py sorts/displays these in a selectable table and
    computes the bullet list + trigger chart lazily only for whichever
    trade the user has selected, rather than precomputing for all of
    them)."""
    return {"summary": summary_roast(diagnosed, currency=currency), "all_trades": diagnosed}
