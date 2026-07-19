# AARD Tradebook Analytics

A brutal mirror for your own trading history. Upload a Zerodha Console
Tradebook export and this app reconstructs your actual round-trip trades,
then scores every entry and exit against a Stage Analysis / composite
technical score / Relative Strength read -- evaluated at your trade's real
dates, not "today". Each trade gets replayed forward from its entry through
a plain mechanical trailing-stop/target/max-holding system, and the gap
between what you did and what the system would have done is where the roast
comes from.

Built by Ayush. This is a personal project, not investment advice, and it
is not affiliated with or endorsed by Zerodha. See [Disclaimer](#disclaimer)
and [License](#license) below.

Every user runs this with their own Kite API key against their own
tradebook -- nothing is shared, nothing is hosted centrally.

## What it needs from you

1. A Kite Connect developer app (`api_key` + `api_secret` from
   [developers.kite.trade](https://developers.kite.trade)). See
   [HOW_TO_USE_THIS.md](HOW_TO_USE_THIS.md) for the full step-by-step --
   creating the Connect app, loading API credits, and the Streamlit Cloud
   deployment settings (Python version, secrets).
2. Your Tradebook export: Zerodha Console -> Reports -> Tradebook -> pick a
   date range (up to 12 months) -> download CSV. **This is not available
   via the Kite Connect API** -- it's a manual export from Console.

## Setup

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# fill in KITE_API_KEY / KITE_API_SECRET
streamlit run app.py
```

On first run, log in via the Kite link (the token expires daily at 6 AM --
you'll need to re-login each day you use the app). Upload your Tradebook,
click "Run the mirror", and wait for the historical candle fetch + scoring
pass (first run per symbol is the slow part; candles are cached to disk per
calendar day after that).

## How it works

- `tradebook_parser.py` -- FIFO-matches individual buy/sell fills from the
  Tradebook into round-trip trades (handles scale-ins, scale-outs, partial
  fills).
- `mistake_diagnosis.py` -- for each trade, evaluates Stage / composite
  score / Relative Strength at the actual entry and exit dates, and replays
  a plain trailing-stop/target/max-holding exit forward from the same entry
  (same rules `unified_backtest.py` uses) to get a counterfactual "what
  discipline alone would have done."
- `exit_triggers.py` -- independently checks several well-known technical
  exit reads (RSI rollover, MACD cross, RS turning negative, price/moving-
  average crosses) for every time each one fired between entry and today.
- `chart.py` -- the multi-panel price/volume/RS/MACD/RSI chart, with every
  exit-trigger occurrence and Stage change marked directly on it.
- `mirror_narrative.py` -- turns the tagged, scored trades into the roast
  copy. Tone lives entirely here, decoupled from the scoring math.
- `app.py` -- the Streamlit UI: upload, run, browse every diagnosed trade.

Also included: `kite_client.py` (OAuth), `instruments_kite.py` (instrument
master), `candle_kite.py` (candle fetch/cache), `daily_base.py` +
`extra_indicators.py` + `relative_strength.py` (the scoring engine),
`unified_backtest.py` (trade-management constants and the trailing-stop
formula).

## Limitations, on purpose

- Only scores **closed** round trips. A position still open in your
  tradebook window hasn't had a chance to be "right" or "wrong" yet.
- Symbols that don't resolve to an NSE cash-equity instrument token (BSE-only
  listings, some ETFs, delisted symbols) are skipped and reported, not
  silently dropped.
- The "systematic replay" is a single, simple set of rules (15% trailing
  stop, 30% target, 90-day max hold) -- it's a deliberately boring baseline
  to diff your actual behavior against, not a claim that it's the optimal
  system.

## Disclaimer

AARD Tradebook Analytics is a personal, educational project for looking back at your
own historical trades. It is **not investment advice, a recommendation, or
a signal-generation tool**, and nothing it shows should be treated as a
suggestion to buy, sell, or hold any security. All analysis is descriptive
(what already happened), never prescriptive (what to do next). Trading
involves risk of loss; do your own research and consult a qualified
financial advisor before making investment decisions. This project is not
affiliated with, endorsed by, or sponsored by Zerodha or Kite Connect.

## License

MIT License -- see [LICENSE](LICENSE). You're free to use, modify, and
redistribute this code, including commercially, as long as the license
notice is kept.

## About Vide Coder

Hi, my name is Ayush
