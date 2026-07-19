# How to Use This

Two things need to be set up before this app works: a Kite Connect app on
Zerodha's developer console (so this app can log in and pull your candle
data), and the Streamlit deployment itself. Both are one-time setup per
person running this.

## 1. Create a Kite Connect app on Zerodha

1. Go to [developers.kite.trade](https://developers.kite.trade) and log in
   with your normal Zerodha (Kite) credentials. ([zerodha.com/products/api](https://zerodha.com/products/api/)
   is Zerodha's overview page for the API product and links here.)
2. **Load API credits.** Go to **Billing** and add credits. A **Connect**
   app (the type this project needs) costs **₹500 for 30 days**. You'll
   need to keep this topped up to keep using the app; it's a Zerodha fee,
   not something this project charges.
3. **Create the app** (Console -> "Create new app"):
   - **Type**: `Connect` -- this is the one that includes historical
     chart data APIs and live market quotes. `Personal` is free but
     explicitly excludes historical chart data, which this app needs to
     score your trades; `Publisher` has no API access at all. Pick
     `Connect`.
   - **App name**: anything, e.g. `AARD Tradebook Analytics`.
   - **Zerodha Client ID**: your own Kite login ID (e.g. `AB1234`). A
     Connect app is locked to whichever client ID you enter here -- only
     that account can log into it.
   - **Redirect URL**: this has to be your *deployed Streamlit app's URL*
     (e.g. `https://your-app-name.streamlit.app`), not `localhost`. Kite
     sends the browser back to this exact URL after login, with a
     `request_token` in the query string, which is how `kite_client.py`
     completes the login. If you don't have the Streamlit URL yet, deploy
     first (step 2 below), then come back and edit this field -- the app
     can be edited after creation.
   - **Postback URL**: leave blank. This app doesn't use order postbacks.
   - **Description**: anything -- it's mandatory but not shown anywhere
     in this app.
   - Click **Create**.
4. On the app's detail page you'll now see an **API key** and **API
   secret**. Copy both -- you'll paste them into Streamlit's secrets in
   step 2. Keep the secret private: never commit it to git, paste it in a
   chat, or put it anywhere but Streamlit's Secrets box.

## 2. Deploy on Streamlit Community Cloud

1. Push this repo to your own GitHub account.
2. On [share.streamlit.io](https://share.streamlit.io), click **New app**,
   pick your repo/branch, and set the main file to `app.py`.
3. Before clicking Deploy, open **Advanced settings**:
   - **Python version**: change it from whatever the dropdown defaults to
     (currently `3.14`) down to **`3.12`**. This project's dependencies
     (`requirements.txt`) are pinned against Python 3.12 (`runtime.txt` /
     `.python-version` both say `3.12`) and aren't verified to work on
     newer interpreter versions -- leaving the default will likely break
     the build.
   - **Secrets**: paste your two Kite credentials from step 1.4 into the
     text box, in TOML format:
     ```toml
     KITE_API_KEY = "your_api_key_here"
     KITE_API_SECRET = "your_api_secret_here"
     ```
     (This is the same format as `.streamlit/secrets.toml.example` in this
     repo, which is for local runs only -- never commit an actual
     `secrets.toml` file.)
4. Click **Deploy**.
5. Once it's live, copy the app's URL. Go back to your Kite Connect app on
   developers.kite.trade, edit it, and set **Redirect URL** to that exact
   URL (must match exactly -- `https://`, no trailing slash mismatch).
   Save. This step has to happen after the first deploy, since you don't
   know the URL beforehand.

## 3. Using it day to day

- Kite's login session expires every day at **6 AM IST** -- there's no
  persistent/headless login. Each day you use the app, click "Log in to
  Kite" and go through the redirect flow again (see `kite_client.py` for
  why -- this is a Zerodha platform limitation, not a bug here).
- Only the Zerodha Client ID entered in step 1.3 can log into this app --
  that's Zerodha's restriction on the Connect app itself, not something
  this project enforces.
- Each time you want a fresh run: Zerodha Console -> Reports -> Tradebook
  -> export a CSV for your date range -> upload it -> click "Run the
  mirror".

## 4. Using a different broker's tradebook

This app is built around Zerodha's Tradebook export, but the actual upload
only needs five columns: `symbol, trade_date, trade_type, quantity, price`
(one row per fill/execution -- see `tradebook_template.csv`). If your
broker isn't Zerodha, you don't need to reformat the file by hand -- have
an AI assistant do it:

1. On the app's front page, click **Download custom CSV template**. This
   gives you `tradebook_template.csv` -- the exact 5-column shape this app
   expects, with a couple of example rows.
2. Open a chat with Claude (or any capable AI assistant) and give it two
   files:
   - the `tradebook_template.csv` you just downloaded (so it knows the
     target shape), and
   - your own broker's tradebook/trade-history export, whatever format
     that broker gives you (CSV, Excel, PDF statement, etc.).
3. Ask it to convert your tradebook into the template's format -- for
   example: *"Convert my attached tradebook into this exact CSV template:
   same columns, same header, one row per buy/sell fill. Use BUY/SELL for
   trade_type and YYYY-MM-DD for trade_date."*
4. Download the CSV the AI produces and upload that to this app instead of
   a Zerodha export.

Because the app only reads those five columns, this makes it broker-agnostic
in practice -- Zerodha just happens to be the one broker whose export this
app can read natively without that conversion step. As with any AI-assisted
data conversion, skim the converted file before uploading it (row count,
a few spot-checked prices/dates) to make sure nothing got mangled -- this
app has no way to tell a converted file was wrong if it happens to still be
well-formed.
