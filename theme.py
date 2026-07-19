"""Visual identity: dark ink background, warm-paper accent, two content-driven
inks (red for damage, teal for what discipline would have done).

Kept deliberately minimal after an earlier pass: hand-rolled multi-element
HTML fragments across separate st.html() calls had unreliable flex/grid
layout, producing squished/overlapping text. What's left is CSS that
reliably targets native Streamlit widgets, plus one self-contained
inline-styled stamp badge."""
import hashlib

import streamlit as st

DISCLAIMER_TEXT = """
**This tool is not investment advice.** Trade Lookback is a personal, educational,
backward-looking (hindsight) diagnostic that runs entirely on your own device/session
using your own Kite API credentials and your own uploaded Tradebook data. Nothing it
displays -- scores, triggers, "if you'd exited on X instead," behavioral labels,
technical levels, or any other output -- is investment advice, a research report, a
recommendation, or a solicitation to buy, sell, or hold any security or other
instrument, and none of it should be relied upon for any actual trading, investment,
tax, or financial decision.

**Hindsight bias is inherent to this tool.** Every condition, trigger, and "what if"
comparison shown here is computed after the fact, with full knowledge of what the
price subsequently did. Nothing here was, or could have been, known in real time at
the original entry/exit dates. Past trades, patterns, win rates, and outcomes shown
are historical only and are not indicative or predictive of future results for these
or any other securities.

**No warranty of accuracy.** This tool relies on third-party market data and on
whatever trade data you upload, and on algorithmic pattern/indicator calculations
that may contain bugs, gaps, mislabeled events, or misclassified trades. It is
provided "as is" and "as available," without any warranty of accuracy, completeness,
timeliness, merchantability, or fitness for a particular purpose, express or implied.

**Not affiliated.** This tool is an independent, unofficial project. It is not
affiliated with, endorsed by, sponsored by, or in any way officially connected to
Zerodha Broking Ltd., Kite Connect, NSE, BSE, or any other exchange, broker, data
provider, or index provider referenced in its output.

**Your data.** Your uploaded Tradebook/trade file(s) and your Kite credentials
are used only within your own active session to generate this analysis. This
tool does not sell your data to third parties. If you deploy or share this app
yourself, you are
responsible for how you configure storage, logging, and access for your own
deployment.

**Your responsibility, your risk.** Trading and investing in securities carries
risk, including the risk of substantial or total loss of capital. You are solely
responsible for independently verifying any information before acting on it and for
all trading and investment decisions you make. To the maximum extent permitted by
applicable law, the author(s) and contributors of this tool disclaim all liability
for any direct, indirect, incidental, or consequential loss or damage arising from
its use or from any reliance placed on its output. By using this tool, you accept
these terms; if you do not accept them, do not use it.
"""

# One fixed dark theme, no toggle -- a prior light/dark toggle relied on
# CSS alone to fight Streamlit's own native theme layer (config.toml) and
# never reliably won on every widget/container across Streamlit versions;
# a single hardcoded theme with no second state to fight has nothing left
# to fall back to white on. --ink is the cream used for headers/titles;
# --text is plain white for body copy (the two are deliberately different
# so headers still stand out against paragraph text).
PALETTE = {
    "bg": "#12141A", "surface": "#1B1E27", "ink": "#E9E4D6", "text": "#FFFFFF",
    "muted": "#B7BCC6", "loss": "#E1573F", "discipline": "#4FBFA6", "border": "#2C303B",
}

_ROOT_CSS = """:root {
  --bg: %(bg)s;
  --surface: %(surface)s;
  --ink: %(ink)s;
  --text: %(text)s;
  --muted: %(muted)s;
  --loss: %(loss)s;
  --discipline: %(discipline)s;
  --border: %(border)s;
}""" % PALETTE

# Single-line rules, no blank lines, no /* */ comments inside the <style>
# block itself -- st.markdown's markdown parser (not a real HTML parser)
# can mangle a multi-line stylesheet with blank lines/comments into partly
# literal text. @import instead of <link> tags for the same reason: raw
# <link> elements are more likely to get escaped by the markdown pass than
# a plain @import sitting inside <style>. Never add a rule targeting bare
# <span> font-family -- Streamlit renders its own icon glyphs (expander
# chevrons, spinner icons) as ligature text inside plain <span> elements via
# a Material Symbols font; overriding it site-wide breaks the ligature and
# the literal icon name shows up as text instead.
_CSS_RULES = [
    "@import url('https://fonts.googleapis.com/css2?family=Archivo+Narrow:wght@600;700&family=IBM+Plex+Sans:wght@400;500&family=IBM+Plex+Mono:wght@400;500;600&display=swap');",
    _ROOT_CSS.replace("\n", " "),
    "html, body, .stApp, [data-testid=\"stAppViewContainer\"], [data-testid=\"stMain\"], [data-testid=\"stMainBlockContainer\"], .main .block-container, [data-testid=\"stSidebar\"], [data-testid=\"stSidebarContent\"], [data-testid=\"stBottomBlockContainer\"], [data-testid=\"stHeader\"] { background: var(--bg) !important; color: var(--text) !important; font-family: 'IBM Plex Sans', sans-serif; }",
    ".stApp p, .stApp li, .stApp label { font-family: 'IBM Plex Sans', sans-serif; color: var(--text); }",
    "[data-testid=\"stFileUploaderDropzone\"], [data-testid=\"stFileUploader\"] section { background: var(--surface) !important; border-color: var(--border) !important; color: var(--text) !important; }",
    "[data-testid=\"stTextInput\"] input, [data-testid=\"stNumberInput\"] input, [data-testid=\"stSelectbox\"] div[data-baseweb=\"select\"] > div, [data-testid=\"stTextArea\"] textarea { background: var(--surface) !important; color: var(--text) !important; border-color: var(--border) !important; }",
    "[data-testid=\"stCheckbox\"] label span, [data-testid=\"stRadio\"] label span, [data-testid=\"stWidgetLabel\"] p { color: var(--text) !important; }",
    "[data-testid=\"stAlert\"] { background: var(--surface) !important; color: var(--text) !important; }",
    "[data-testid=\"stCaptionContainer\"] { color: var(--muted) !important; }",
    "[data-testid=\"stMarkdownContainer\"] { color: var(--text); }",
    ".stApp h1, .stApp h2, .stApp h3 { font-family: 'Archivo Narrow', sans-serif; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 700; color: var(--ink); }",
    ".stApp h2 { border-bottom: 1px solid var(--border); padding-bottom: 0.3em; margin-top: 1.6em; }",
    "[data-testid=\"stMetric\"] { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 0.8rem 1rem; }",
    "[data-testid=\"stMetricLabel\"] { font-family: 'Archivo Narrow', sans-serif; text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.78rem; color: var(--muted); }",
    "[data-testid=\"stMetricValue\"] { font-family: 'IBM Plex Mono', monospace; color: var(--text); }",
    "[data-testid=\"stExpander\"] { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; }",
    "[data-testid=\"stExpander\"] summary, [data-testid=\"stExpander\"] summary p { color: var(--text) !important; }",
    "[data-testid=\"stExpanderDetails\"] { background: var(--surface) !important; color: var(--text) !important; }",
    "[data-testid=\"stDataFrameResizable\"] { color: var(--text); }",
    ".stButton>button { font-family: 'Archivo Narrow', sans-serif; text-transform: uppercase; letter-spacing: 0.06em; border: 2px solid var(--loss); background: transparent; color: var(--loss); border-radius: 4px; }",
    ".stButton>button:hover { background: var(--loss); color: var(--bg); border-color: var(--loss); }",
    "[data-testid=\"stDataFrame\"] { font-family: 'IBM Plex Mono', monospace; color: var(--text); }",
    ":focus-visible { outline: 2px solid var(--discipline); outline-offset: 2px; }",
]
_CSS = "<style>" + " ".join(_CSS_RULES) + "</style>"


def inject():
    st.markdown(_CSS, unsafe_allow_html=True)


def _tilt(seed):
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return (h % 9) - 4


def stamp(text, kind="loss", seed=None):
    """A single, fully self-contained inline-styled badge -- deliberately
    doesn't depend on the external <style> block (a separate st.html() call
    from a different fragment wasn't reliably sharing styles in practice),
    so this renders correctly no matter what else is on the page."""
    color = {"loss": PALETTE["loss"], "discipline": PALETTE["discipline"], "neutral": PALETTE["muted"]}.get(
        kind, PALETTE["muted"]
    )
    tilt = _tilt(seed or text)
    style = (
        f"display:inline-block;font-family:'Archivo Narrow',sans-serif;font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.08em;font-size:0.78rem;padding:0.28em 0.7em;"
        f"border:2px solid {color};border-radius:3px;color:{color};"
        f"transform:rotate({tilt}deg);white-space:nowrap;"
    )
    return f'<span style="{style}">{text}</span>'


def banner(title_html, subtitle, stamp_text, stamp_kind):
    style = (
        f"background:{PALETTE['surface']};border:1px solid {PALETTE['border']};border-radius:8px;"
        "padding:1.4rem 1.6rem;display:flex;flex-wrap:wrap;align-items:center;"
        "justify-content:space-between;gap:1rem;margin-bottom:1.2rem;font-family:'IBM Plex Sans',sans-serif;"
    )
    return (
        f'<div style="{style}">'
        f'<div><h1 style="margin:0 0 0.2rem 0;font-family:\'Archivo Narrow\',sans-serif;'
        f'text-transform:uppercase;letter-spacing:0.04em;font-weight:700;color:{PALETTE["ink"]};">{title_html}</h1>'
        f'<p style="margin:0;color:{PALETTE["muted"]};font-size:0.95rem;">{subtitle}</p></div>'
        f'{stamp(stamp_text, stamp_kind, seed="banner")}'
        '</div>'
    )
