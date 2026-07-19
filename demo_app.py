"""AARD Tradebook Analytics -- DEMO MODE.

Identical analysis pipeline to app.py (same Kite fetch, same scoring, run
against YOUR real tradebook with YOUR own Kite login) -- the only
difference is what's rendered: every real ticker symbol is replaced with a
stable STOCK#1, STOCK#2... label before anything reaches the screen.
Industry / industry group are left real, since that's sector composition,
not identity. See demo_anonymize.py for exactly what gets swapped and why.

This is a separate entry point (not a mode flag inside app.py) so the
anonymization boundary is a hard file boundary, not a runtime branch that
could accidentally be left off.
"""
import os

import pandas as pd
import streamlit as st

import behavioral_profile
import candle_kite
import chart
import compliance
import demo_anonymize
import exit_triggers
import instruments_kite
import kite_client
import mirror_narrative
import theme
import tradebook_parser
from mistake_diagnosis import (
    diagnose_all, prepare_symbol_frame, required_lookback_days, resolve_token,
)
from mirror_narrative import humanize_tag

st.set_page_config(page_title="AARD Tradebook Analytics -- Demo", page_icon="\U0001F576", layout="wide")

_toggle_col = st.columns([6, 1])[1]
with _toggle_col:
    theme_mode = theme.mode_toggle()
theme.inject(theme_mode)

currency = "₹"

with open(os.path.join(os.path.dirname(__file__), "tradebook_template.csv"), "rb") as _f:
    _TEMPLATE_CSV_BYTES = _f.read()

st.html(
    theme.banner(
        "AARD Tradebook Analytics -- Demo Mode",
        "Same real analysis as the main app, run with your own Kite login and your own "
        "tradebook -- but every ticker is shown as STOCK#1, STOCK#2, etc. Safe to record or "
        "share screenshots from.",
        "ANONYMIZED DEMO", "neutral", mode=theme_mode,
    )
)
st.caption(
    "Not investment advice or a recommendation -- a backward-looking diagnostic on your own "
    "past trades, run entirely with your own Kite API key. Not affiliated with Zerodha. "
    "Symbol names are anonymized for display only; industry/industry group are shown as-is."
)
st.markdown(
    f"**This app will not analyze, score, chart, or display any trade closed within the "
    f"last {compliance.RECENT_TRADE_LOCKOUT_DAYS} days (~3.5 months) from today. Recently "
    f"closed trades are dropped before any processing happens.**"
)
with st.expander("Disclaimer -- read before using", expanded=False):
    st.markdown(theme.DISCLAIMER_TEXT)

# Zerodha's exported tradebook filename embeds the client ID (e.g.
# "tradebook-AB1234-EQ.csv") -- Streamlit's file_uploader widget shows the
# real filename in its own built-in UI once a file is picked, which would
# leak that ID on a public/screen-shared demo. This CSS hides the native
# filename text and overlays a generic label in its place; nothing about
# parsing or upload behavior changes, this is display-only.
st.html("""
<style>
[data-testid="stFileUploaderFileName"] { visibility: hidden; position: relative; }
[data-testid="stFileUploaderFileName"]::before {
  visibility: visible; content: "File uploaded"; position: absolute; left: 0; top: 0;
}
</style>
""")

kite = kite_client.get_kite()
if kite is None:
    st.markdown(f"[Log in to Kite]({kite_client.login_url()})")
    st.stop()
user_name = st.session_state.get("kite_user_name")
if user_name:
    st.caption(f"Logged in as {user_name}")

uploaded_files = st.file_uploader(
    "Zerodha Console Tradebook (Console -> Reports -> Tradebook, CSV or Excel, up to 12 months)",
    type=["csv", "xlsx", "xls"], accept_multiple_files=True,
)
st.download_button(
    "Download custom CSV template",
    data=_TEMPLATE_CSV_BYTES,
    file_name="tradebook_template.csv",
    mime="text/csv",
    help="No Zerodha Tradebook export? Fill in your own trades using this minimal "
         "5-column format (symbol, trade_date, trade_type, quantity, price) and upload "
         "it above instead.",
)
with st.expander("Using a different broker? Convert your tradebook with an AI assistant"):
    st.markdown(
        "This app only reads 5 columns: `symbol, trade_date, trade_type, quantity, price` "
        "(one row per buy/sell fill). If your broker isn't Zerodha, you don't need to "
        "reformat the file by hand:\n\n"
        "1. Click **Download custom CSV template** above to get `tradebook_template.csv`.\n"
        "2. Open a chat with Claude (or any capable AI assistant) and give it two files: "
        "the template you just downloaded, and your own broker's tradebook/trade-history "
        "export (whatever format it comes in -- CSV, Excel, PDF statement, etc.).\n"
        "3. Ask it to convert your tradebook into the template's exact format -- for "
        "example: *\"Convert my attached tradebook into this exact CSV template: same "
        "columns, same header, one row per buy/sell fill. Use BUY/SELL for trade_type and "
        "YYYY-MM-DD for trade_date.\"*\n"
        "4. Download the CSV the AI produces and upload that above instead.\n\n"
        "Skim the converted file before uploading it (row count, a few spot-checked "
        "prices/dates) -- this app has no way to tell a converted file was wrong if it "
        "happens to still be well-formed."
    )

if not uploaded_files:
    st.info("Upload one or more Tradebook exports to begin. Multiple files (e.g. separate date "
            "ranges) are combined automatically.")
    st.stop()

fills_df, parse_errors = tradebook_parser.load_tradebook(uploaded_files)
# Real filenames aren't shown here either -- same reasoning as the CSS above,
# a Zerodha tradebook filename embeds the client ID.
for i, (fname, msg) in enumerate(parse_errors, start=1):
    st.warning(f"Couldn't read uploaded file #{i}: {msg}")

if fills_df.empty:
    st.error("No usable fills found in the uploaded file(s).")
    st.stop()

trades, unmatched = tradebook_parser.build_roundtrip_trades(fills_df)
trades, excluded_recent = compliance.split_recent_trades(trades)
st.write(f"Parsed **{len(fills_df)}** fills into **{len(trades) + len(excluded_recent)}** closed "
         f"round-trip trades across **{fills_df['symbol'].nunique()}** symbols.")
if excluded_recent:
    st.warning(
        f"**{len(excluded_recent)} trade(s) closed within the last "
        f"{compliance.RECENT_TRADE_LOCKOUT_DAYS} days are not analyzed, for compliance.** "
        f"They're listed below exactly as parsed from your upload (symbols anonymized), but "
        f"are never fetched, scored, charted, or otherwise processed. **{len(trades)}** "
        f"trade(s) remain eligible for analysis below."
    )
    _excluded_symbol_map = demo_anonymize.build_symbol_map(excluded_recent)
    st.dataframe(
        pd.DataFrame([
            {
                "Symbol": _excluded_symbol_map[t["symbol"]],
                "Entry date": t["entry_date"].date(),
                "Exit date": t["exit_date"].date(),
                "Quantity": t["quantity"],
            }
            for t in sorted(excluded_recent, key=lambda x: x["exit_date"], reverse=True)
        ]),
        use_container_width=True, hide_index=True,
    )
if unmatched:
    with st.expander(f"{len(unmatched)} symbol(s) had unmatched sell quantity (short sale, or a "
                      f"position opened before this upload's date range)"):
        st.json(unmatched)

if not trades:
    st.warning(
        "No closed round trips to diagnose -- either every position in this upload is still "
        "open, or every closed trade falls inside the compliance lockout window above."
    )
    st.stop()

symbols = sorted({t["symbol"] for t in trades})

if st.button("Run the mirror", type="primary"):
    with st.spinner("Fetching NSE instrument master..."):
        instruments = instruments_kite.fetch_nse_instruments(kite)
        symbol_token_map = instruments_kite.build_symbol_token_map(instruments)
        bench_token = instruments_kite.find_index_token(instruments, "NIFTY 500")

    candle_kite.DAILY_LOOKBACK_DAYS = required_lookback_days(trades)

    resolved = {s: resolve_token(s, symbol_token_map, instruments) for s in symbols}
    unresolved_symbols = [s for s, tok in resolved.items() if tok is None]
    symbol_token_pairs = [(s, tok) for s, tok in resolved.items() if tok is not None]

    progress = st.progress(0.0, text="Fetching historical candles...")

    def _on_progress(done, total):
        progress.progress(done / total if total else 1.0, text=f"Fetching candles... {done}/{total}")

    daily_by_symbol = candle_kite.prefetch_daily_bulk(kite, symbol_token_pairs, on_progress=_on_progress)
    benchmark_daily = candle_kite.fetch_daily(kite, bench_token, "NIFTY 500") if bench_token else None
    progress.empty()
    skipped_extra = [(s, "symbol didn't resolve to an NSE instrument token") for s in unresolved_symbols]

    with st.spinner("Scoring every trade..."):
        symbol_frames = {s: prepare_symbol_frame(d, benchmark_daily) for s, d in daily_by_symbol.items()}
        diagnosed, skipped = diagnose_all(trades, symbol_frames)
        report = mirror_narrative.build_report(diagnosed, currency=currency)
        trigger_stats = exit_triggers.portfolio_stats(diagnosed, symbol_frames)
        symbol_map = demo_anonymize.build_symbol_map(diagnosed)

    st.session_state["demo_report"] = report
    st.session_state["demo_frames"] = symbol_frames
    st.session_state["demo_skipped"] = skipped + skipped_extra
    st.session_state["demo_trigger_stats"] = trigger_stats
    st.session_state["demo_symbol_map"] = symbol_map

report = st.session_state.get("demo_report")
if report is None:
    st.stop()

symbol_frames = st.session_state.get("demo_frames", {})
skipped = st.session_state.get("demo_skipped", [])
trigger_stats = st.session_state.get("demo_trigger_stats", {})
symbol_map = st.session_state.get("demo_symbol_map", {})

if skipped:
    # Skipped-symbol reasons are internal (no candle history etc.), not a
    # per-trade table -- anonymize the symbol column same as everywhere else.
    skipped_display = [(symbol_map.get(s, s), reason) for s, reason in skipped]
    with st.expander(f"{len(skipped)} trade(s) skipped (no usable history)"):
        st.dataframe(pd.DataFrame(skipped_display, columns=["symbol", "reason"]), use_container_width=True)

summary = demo_anonymize.anonymize_summary(report["summary"], symbol_map)
if summary is None:
    st.warning("Nothing to diagnose -- every trade was skipped.")
    st.stop()

st.markdown("## The Mirror")
st.markdown(summary["headline"])

col1, col2, col3 = st.columns(3)
col1.metric("Left on the table (vs. discipline)", f"{currency}{summary['total_left_on_table']:,.0f}")
col2.metric("Actual realized P&L", f"{currency}{summary['total_pnl']:,.0f}")
col3.metric("Clean trades", summary["mistake_frequency"].get("clean", 0))

st.markdown("### Backtest stats")
st.caption(
    "Straight from your own entry/exit prices and dates -- independent of the systematic-replay "
    "comparison above."
)
bt = summary["backtest"]
b1, b2, b3 = st.columns(3)
b1.metric("Avg return / trade", f"{bt['expectancy_pct']:+.1f}%",
          help=f"The average price return per trade, gross -- excludes brokerage, taxes, and any "
               f"other transaction costs. {bt['n_wins']} wins / {bt['n_losses']} losses out of "
               f"{bt['n_trades']} trades.")
b2.metric("Risk : Reward", f"1 : {bt['risk_reward']:.2f}" if bt["risk_reward"] else "—",
          help=f"Avg win {bt['avg_win_pct']:+.1f}% vs. avg loss {bt['avg_loss_pct']:+.1f}%")
b3.metric("Avg days held", f"{bt['avg_days_held']:.0f}",
          help=f"Range {bt['min_days_held']}-{bt['max_days_held']} days")

st.markdown("### Your avg return per trade vs. a profit + stop-loss condition")
st.caption(
    "Pick one profit-taking condition and one stop-loss condition -- each trade is checked against "
    "BOTH, and whichever fired first after entry decides the hypothetical exit. Compared against your "
    "actual average return per trade (gross, excludes brokerage/costs). Descriptive, not a "
    "recommendation."
)
profit_label_to_key = {label: key for key, label in exit_triggers.PROFIT_TRIGGERS}
stop_label_to_key = {label: key for key, label in exit_triggers.STOP_LOSS_TRIGGERS}
pc1, pc2 = st.columns(2)
with pc1:
    profit_label = st.selectbox("Profit condition", list(profit_label_to_key.keys()))
with pc2:
    stop_label = st.selectbox("Stop loss condition", list(stop_label_to_key.keys()))

combined = exit_triggers.combined_portfolio_stats(
    report["all_trades"], symbol_frames, profit_label_to_key[profit_label], stop_label_to_key[stop_label]
)
tc1, tc2 = st.columns(2)
tc1.metric("Your actual avg return / trade", f"{bt['expectancy_pct']:+.1f}%",
           help=f"Across all {bt['n_trades']} closed trades, gross (excludes brokerage/costs)")
if combined["n_trades"]:
    delta = round(combined["avg_return_pct"] - bt["expectancy_pct"], 1)
    tc2.metric(f"If exited on: {profit_label} / {stop_label}", f"{combined['avg_return_pct']:+.1f}%",
               delta=f"{delta:+.1f} pts",
               help=f"Whichever fired first, based on the {combined['n_trades']} trade(s) where at least one did")
else:
    tc2.metric(f"If exited on: {profit_label} / {stop_label}", "—",
               help="Neither condition ever fired on any of your trades")

st.markdown("### Your recurring mistakes")
freq = {humanize_tag(k): v for k, v in summary["mistake_frequency"].items() if k != "clean"}
if freq:
    st.plotly_chart(chart.mistake_frequency_chart(freq), use_container_width=True)
else:
    st.success("No recurring mistake patterns detected. Suspiciously clean.")

st.markdown("## Every diagnosed trade")
st.caption("Click a row to see the full chart, exit-trigger comparison, and analysis for that trade.")

all_trades_sorted = sorted(report["all_trades"], key=lambda x: x["impact_rupees"], reverse=True)
pnl_col, left_col = f"P&L ({currency})", f"Left on table ({currency})"
table_df = pd.DataFrame([
    {
        "Symbol": symbol_map.get(d["symbol"], d["symbol"]),
        "Entry": d["entry_date"].date(), "Exit": d["exit_date"].date(),
        "Return %": d["user_return_pct"], pnl_col: d["pnl_rupees"],
        left_col: d["impact_rupees"],
        "What went wrong": ", ".join(humanize_tag(t) for t in d["tags"] if t != "clean") or "Clean trade",
    }
    for d in all_trades_sorted
])
selection = st.dataframe(
    table_df,
    use_container_width=True, hide_index=True,
    on_select="rerun", selection_mode="single-row",
    column_config={
        "Return %": st.column_config.NumberColumn(format="%.1f%%"),
        pnl_col: st.column_config.NumberColumn(format=f"{currency}%.0f"),
        left_col: st.column_config.NumberColumn(format=f"{currency}%.0f"),
    },
)

selected_rows = selection.selection.rows if selection and selection.selection else []
selected_idx = selected_rows[0] if selected_rows else 0
d_real = all_trades_sorted[selected_idx]
d = demo_anonymize.anonymize_trade(d_real, symbol_map)

st.markdown(f"## {d['symbol']}: {d['entry_date'].date()} → {d['exit_date'].date()}")
is_clean = d["tags"] == ["clean"]
verdict = "Clean trade" if is_clean else " + ".join(humanize_tag(t) for t in d["tags"])
color = "green" if is_clean else "red"
st.markdown(f"**What went wrong:** :{color}[{verdict}]")

m1, m2, m3 = st.columns(3)
m1.metric("Your return", f'{d["user_return_pct"]:+.1f}%')
m2.metric("Days held", d["days_in_trade"])
m3.metric("P&L", f'{currency}{d["pnl_rupees"]:,.0f}')

# Candle history is keyed by the REAL symbol -- look it up with d_real, but
# every diag dict passed downstream from here on is the anonymized one, so
# nothing rendered from this point can carry the real ticker.
w = symbol_frames.get(d_real["symbol"])
if w is None:
    st.warning("No candle history available for this symbol -- can't build the chart or triggers.")
else:
    i_entry = w.index.searchsorted(d["entry_date"], side="right") - 1
    triggers_all = exit_triggers.evaluate_triggers(w, i_entry, d["entry_price"], d["quantity"], d["user_return_pct"])
    gann_levels = exit_triggers.gann_fib_levels(w, i_entry, d["entry_price"])

    st.markdown("### Mark conditions on the chart")
    st.caption(
        "Pick up to two triggers to plot on the chart below, with an arrow label at each well-spaced "
        "occurrence (every occurrence still gets a dot, even where labels are thinned out). Everything "
        "this app checks is always in the table further down, whether or not it's picked here."
    )
    chart_label_to_key = {label: key for key, label in exit_triggers.TRIGGERS}
    chart_options = ["None"] + list(chart_label_to_key.keys())
    gc1, gc2 = st.columns(2)
    with gc1:
        chart_pick1 = st.selectbox(
            "Chart condition 1", chart_options,
            index=chart_options.index("Price closed below its 50-day average"), key="chart_trigger_1",
        )
    with gc2:
        chart_pick2 = st.selectbox(
            "Chart condition 2", chart_options,
            index=chart_options.index(f"RSI(14) crossed above {exit_triggers.RSI_OVERBOUGHT:.0f} (overbought)"),
            key="chart_trigger_2",
        )
    triggers_by_key = {t["key"]: t for t in triggers_all}
    triggers_chart = [
        triggers_by_key[chart_label_to_key[pick]] for pick in (chart_pick1, chart_pick2) if pick != "None"
    ]
    show_gann_on_chart = st.checkbox("Show Gann/Fibonacci levels on chart", value=True)

    st.plotly_chart(
        chart.trade_chart(w, d, triggers_chart, gann_levels if show_gann_on_chart else None),
        use_container_width=True,
    )

    st.markdown("### If you'd exited on a different technical trigger instead")
    st.caption(
        "Every trigger this app checks, including the profit/stop-loss conditions and the chart picks "
        "above -- not a recommendation, just what each one would have meant, every time it fired between "
        "your entry and today."
    )
    vs_exit_col = f"vs. your exit ({currency})"
    trig_rows = []
    for t in triggers_all:
        if not t["fired"]:
            trig_rows.append({
                "Trigger": t["label"], "Fired?": "Never (as of latest data)",
                "Date": None, "Price": None, "Return from entry": None,
                "Days after entry": None, "vs. your exit (pts)": None, vs_exit_col: None,
            })
            continue
        for n, occ in enumerate(t["occurrences"], start=1):
            label = t["label"] if len(t["occurrences"]) == 1 else f'{t["label"]} (#{n})'
            trig_rows.append({
                "Trigger": label, "Fired?": "Yes",
                "Date": occ["date"].date(),
                "Price": occ["price"],
                "Return from entry": occ["return_pct"],
                "Days after entry": occ["days_after_entry"],
                "vs. your exit (pts)": occ["vs_your_return_pct"],
                vs_exit_col: occ["vs_your_return_rupees"],
            })
    st.dataframe(
        pd.DataFrame(trig_rows), use_container_width=True, hide_index=True,
        column_config={
            "Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Price": st.column_config.NumberColumn(format=f"{currency}%.2f"),
            "Return from entry": st.column_config.NumberColumn(format="%+.1f%%"),
            "Days after entry": st.column_config.NumberColumn(format="%d"),
            "vs. your exit (pts)": st.column_config.NumberColumn(format="%+.1f pts"),
            vs_exit_col: st.column_config.NumberColumn(format=f"{currency}%+,.0f"),
        },
    )

    st.markdown("### Analysis")
    for b in demo_anonymize.anonymize_bullets(mirror_narrative.all_bullets(d_real, currency=currency), symbol_map):
        st.markdown(f"- {b}")

    behavioral_baseline = behavioral_profile.Baseline(report["all_trades"])
    raw_trade = behavioral_profile.raw_trade_lookup(trades).get(
        (d_real["symbol"], d_real["entry_date"], d_real["exit_date"])
    )
    behavioral_notes_real = behavioral_profile.trade_behavioral_notes(
        d_real, behavioral_baseline, w=w, raw_trade=raw_trade, currency=currency
    )
    behavioral_notes = demo_anonymize.anonymize_bullets(behavioral_notes_real, symbol_map)
    if behavioral_notes:
        st.markdown("### Behavioral read")
        st.caption(
            "Inferred from this trade's own numbers -- entry sizing, exit-trigger timing, fill history. "
            "Correlational, not a diagnosis: a pattern consistent with a known bias, not proof of intent."
        )
        for b in behavioral_notes:
            st.markdown(f"- {b}")

st.markdown("## Investor Behavioral Profile")
st.caption(
    "Covers every closed trade in this upload, computed purely from entry/exit timing, sizing, and how "
    "exit signals were (or weren't) acted on. These are behavioral signatures consistent with well-known "
    "biases, not a clinical read on you -- and scores are down-weighted when trade count is small."
)
investor_profile = behavioral_profile.investor_profile(report["all_trades"], symbol_frames, trades)
if investor_profile is None or not investor_profile["traits"]:
    st.info("Not enough patterns in this trade history to profile yet.")
else:
    ranked_traits = sorted(
        investor_profile["traits"].values(), key=lambda v: v["score"] * v["confidence"], reverse=True
    )

    if investor_profile["dominant"]:
        st.markdown("#### Dominant pattern(s)")
        dominant_traits = [investor_profile["traits"][k] for k in investor_profile["dominant"]]
        for col, v in zip(st.columns(len(dominant_traits)), dominant_traits):
            col.metric(v["label"], f"{v['score']:.2f}", help=f"Confidence {v['confidence']:.2f}")

    st.markdown("#### Full scorecard")
    scorecard_df = pd.DataFrame([
        {"Trait": v["label"], "Score": v["score"], "Confidence": v["confidence"]} for v in ranked_traits
    ])
    st.dataframe(
        scorecard_df, use_container_width=True, hide_index=True,
        column_config={
            "Score": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
            "Confidence": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
        },
    )

    st.markdown("#### Evidence")
    trait_label = st.selectbox(
        "See the trades behind a trait", [v["label"] for v in ranked_traits], key="behavioral_trait_select"
    )
    selected_trait = next(v for v in ranked_traits if v["label"] == trait_label)
    st.caption(f"Score {selected_trait['score']:.2f} · Confidence {selected_trait['confidence']:.2f}")
    for e in demo_anonymize.anonymize_bullets(selected_trait["evidence"], symbol_map):
        st.markdown(f"- {e}")
