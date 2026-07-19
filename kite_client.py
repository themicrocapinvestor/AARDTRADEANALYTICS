"""Kite Connect session handling for the Streamlit Cloud deployment.

Kite's access_token comes from a browser login redirect (request_token) and
expires daily at 6 AM -- there's no headless password login. On Streamlit
Cloud the whole login round-trip happens inside the app itself:

  1. If there's no valid token for today, show a "Log in to Kite" link
     (kite.login_url()) built from KITE_API_KEY in st.secrets.
  2. Kite redirects the browser back to this same app's URL with
     ?request_token=... in the query string.
  3. Streamlit reads that from st.query_params, exchanges it for an
     access_token via kite.generate_session() (api_secret never leaves this
     server-side code, never reaches the browser), and caches it.

Caching is st.session_state only -- one browser session per Zerodha login,
so two different users hitting the same deployed app each get their own
token and never see each other's account. (An earlier version of this file
also cached the token to a shared on-disk file; that was a single-user bug,
not a feature -- it let whoever logged in first each day be silently reused
for every other visitor. Removed.) session_state doesn't survive a Streamlit
Cloud redeploy/restart, a browser tab closing, or the 6 AM token expiry --
all three just mean clicking "Log in to Kite" again.
"""
import datetime as dt

import streamlit as st
from kiteconnect import KiteConnect


def _today():
    return dt.date.today().isoformat()


def get_kite():
    """Returns an authenticated KiteConnect client, or None if login is still
    needed (caller should render the login link in that case)."""
    api_key = st.secrets["KITE_API_KEY"]

    token = st.session_state.get("kite_access_token")
    token_date = st.session_state.get("kite_access_token_date")
    if token and token_date == _today():
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(token)
        return kite

    # Not logged in yet today -- check if Kite just redirected back with a token.
    request_token = st.query_params.get("request_token")
    if request_token:
        kite = KiteConnect(api_key=api_key)
        try:
            session = kite.generate_session(request_token, api_secret=st.secrets["KITE_API_SECRET"])
        except Exception as e:
            st.error(f"Kite login failed: {e}. Try logging in again.")
            st.query_params.clear()
            return None
        access_token = session["access_token"]
        st.session_state["kite_access_token"] = access_token
        st.session_state["kite_access_token_date"] = _today()
        st.query_params.clear()  # scrub the token out of the visible URL
        kite.set_access_token(access_token)
        try:
            profile = kite.profile()
            st.session_state["kite_user_id"] = profile.get("user_id")
            st.session_state["kite_user_name"] = profile.get("user_name")
        except Exception:
            pass  # non-fatal -- just means "Logged in as ..." won't show a name
        return kite

    return None


def login_url():
    api_key = st.secrets["KITE_API_KEY"]
    return KiteConnect(api_key=api_key).login_url()
