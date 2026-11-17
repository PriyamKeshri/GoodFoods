"""Design system shared by all pages: tokens, CSS components and small HTML helpers.

Palette "Midnight, Violet and Saffron": a deep midnight base for the sidebar, page header and
tickets; a vivid violet for actions; a saffron-to-magenta gradient as the brand highlight.
"""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

STATIC = Path(__file__).resolve().parent.parent / "static"

# Tokens (keep in sync with .streamlit/config.toml)
ACCENT = "#5B3FD6"   # violet: actions and single-series charts
INK = "#15122B"      # midnight
MUTED = "#6B6880"
# Chart colours: one hue for magnitude; a validated colour-blind-safe order for categories.
SEQUENTIAL_SCHEME = "purples"
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
POSITIVE, NEGATIVE = "#1baf7a", "#eb6834"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root {
  --gf-ink:#15122B; --gf-muted:#6B6880; --gf-soft:#3F3B57; --gf-line:#E6E4EF; --gf-surface:#F6F5FB;
  --gf-accent:#5B3FD6; --gf-accent-soft:#EEEAFE; --gf-saffron:#FF7A1A; --gf-magenta:#E11D74;
  --gf-green:#0B7A55; --gf-green-soft:#E3F6EE; --gf-rose:#B4234B; --gf-rose-soft:#FDE8EE;
  --gf-brand: linear-gradient(120deg, #FF7A1A 0%, #E11D74 100%);
}
.block-container {padding-top: 4.4rem; padding-bottom: 1.5rem; max-width: 1280px;}

/* Page header band */
.gf-head {position:relative; overflow:hidden; display:flex; justify-content:space-between; align-items:flex-end;
          gap:1rem 1.5rem; flex-wrap:wrap; padding:1.15rem 1.5rem 1.2rem; border-radius:18px; margin-bottom:.4rem;
          background: radial-gradient(900px 260px at 105% -40%, rgba(225,29,116,.55), transparent 60%),
                      radial-gradient(700px 240px at 78% 150%, rgba(255,122,26,.50), transparent 62%),
                      linear-gradient(115deg, #15122B 0%, #241A52 58%, #3A1F6B 100%);}
.gf-eyebrow {font-size:.7rem; letter-spacing:.16em; text-transform:uppercase; font-weight:700;
             background:var(--gf-brand); -webkit-background-clip:text; background-clip:text; color:transparent;
             display:inline-block;}
.gf-head h1 {font-size:1.8rem; font-weight:650; letter-spacing:-.022em; margin:.25rem 0 .3rem; padding:0;
             color:#FFFFFF; line-height:1.2;}
.gf-head p {margin:0; color:#CFCBE6; font-size:.93rem; max-width:54rem; line-height:1.5;}
.gf-pills {display:flex; flex-wrap:wrap; gap:.4rem; justify-content:flex-end;}
.gf-pill {border:1px solid rgba(255,255,255,.22); background:rgba(255,255,255,.10); border-radius:999px;
          padding:.18rem .75rem; font-size:.76rem; color:#F3F1FB; white-space:nowrap; backdrop-filter: blur(4px);}

/* Small text helpers */
.gf-label {font-size:.68rem; letter-spacing:.12em; text-transform:uppercase; color:var(--gf-muted); font-weight:700;
           margin:.1rem 0 .3rem;}
.gf-muted {color:var(--gf-muted); font-size:.85rem;}
.gf-strong {font-weight:650; color:var(--gf-ink);}

/* Status pills */
.gf-status {display:inline-flex; align-items:center; gap:.38rem; border-radius:999px; padding:.17rem .65rem;
            font-size:.76rem; font-weight:650; white-space:nowrap;}
.gf-status::before {content:""; width:.42rem; height:.42rem; border-radius:50%; background:currentColor;}
.gf-ok {background:var(--gf-green-soft); color:var(--gf-green);}
.gf-warn {background:var(--gf-rose-soft); color:var(--gf-rose);}
.gf-info {background:var(--gf-accent-soft); color:var(--gf-accent);}
.gf-bad {background:var(--gf-rose-soft); color:var(--gf-rose);}
.gf-neutral {background:var(--gf-surface); color:var(--gf-soft);}

/* Venue rows */
.gf-row {display:flex; gap:.85rem; align-items:flex-start;}
.gf-mono {flex:0 0 46px; height:46px; border-radius:13px; display:flex; align-items:center; justify-content:center;
          font-weight:700; font-size:.88rem; letter-spacing:.03em; color:#FFFFFF;}
.gf-name {font-weight:650; font-size:1.03rem; color:var(--gf-ink); line-height:1.3;}
.gf-name span {font-weight:500; color:var(--gf-muted);}
.gf-meta {color:var(--gf-muted); font-size:.85rem; margin-top:.12rem;}
.gf-meta b {color:var(--gf-ink); font-weight:600;}
.gf-tags {display:flex; flex-wrap:wrap; gap:.3rem; margin-top:.45rem;}
.gf-tag {background:var(--gf-surface); border:1px solid var(--gf-line); border-radius:6px; padding:.06rem .45rem;
         font-size:.75rem; color:var(--gf-soft);}
.gf-side {display:flex; flex-direction:column; align-items:flex-end; gap:.3rem; text-align:right;}

/* Key-value summary */
.gf-kv {display:grid; grid-template-columns:max-content 1fr; gap:.28rem 1.2rem; font-size:.9rem; margin:.55rem 0 .15rem;}
.gf-kv dt {color:var(--gf-muted);} .gf-kv dd {margin:0; color:var(--gf-ink); font-weight:500;}
.gf-cardhead {display:flex; align-items:center; gap:.6rem; flex-wrap:wrap;}

/* Confirmation ticket */
.gf-ticket {display:flex; border:1px solid var(--gf-line); border-radius:16px; overflow:hidden; background:#fff;
            box-shadow:0 10px 30px -18px rgba(21,18,43,.45);}
.gf-ticket-main {flex:1; padding:1.05rem 1.25rem; min-width:0;}
.gf-ticket-side {flex:0 0 210px; padding:1.05rem 1.25rem; border-left:2px dashed rgba(255,255,255,.28);
                 background: radial-gradient(260px 160px at 110% -20%, rgba(225,29,116,.55), transparent 65%),
                             linear-gradient(140deg, #15122B, #2A1D5E);
                 display:flex; flex-direction:column; justify-content:center; gap:.25rem;}
.gf-ticket-side .gf-label {color:#B4AFD0;} .gf-ticket-side .gf-muted {color:#B4AFD0;}
.gf-code {font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:1.5rem; font-weight:700;
          letter-spacing:.05em; color:#FFB277; line-height:1.2;}

/* Agent trace */
.gf-ev {display:flex; align-items:baseline; gap:.55rem; font-size:.86rem; margin:.55rem 0 .15rem; color:var(--gf-ink);}
.gf-ev .gf-right {margin-left:auto; color:var(--gf-muted); font-size:.76rem; white-space:nowrap;}
.gf-badge {font-size:.64rem; font-weight:700; letter-spacing:.07em; border-radius:5px; padding:.1rem .4rem;
           text-transform:uppercase; flex:0 0 auto;}
.gf-b-llm {background:var(--gf-accent-soft); color:var(--gf-accent);}
.gf-b-tool {background:var(--gf-green-soft); color:var(--gf-green);}
.gf-b-guard {background:#FFEEDD; color:#B4470A;} .gf-b-err {background:var(--gf-rose-soft); color:var(--gf-rose);}

/* Empty state, legend, compact stats */
.gf-legend {display:flex; gap:1rem; flex-wrap:wrap; color:var(--gf-muted); font-size:.78rem; margin-top:.2rem;}
.gf-legend i {display:inline-block; width:.6rem; height:.6rem; border-radius:50%; margin-right:.35rem;}
.gf-stats {display:grid; grid-template-columns:repeat(3, 1fr); gap:.6rem;}
.gf-stats b {display:block; font-size:1.35rem; font-weight:650; letter-spacing:-.02em; color:var(--gf-ink); line-height:1.2;}
.gf-stats span {color:var(--gf-muted); font-size:.76rem;}

/* Dark sidebar */
[data-testid="stSidebar"] .gf-label {color:#A7A2C6; margin-top:.9rem;}
[data-testid="stSidebar"] .gf-ok {background:rgba(52,211,153,.16); color:#6EE7B7;}
[data-testid="stSidebar"] .gf-bad {background:rgba(251,113,133,.18); color:#FDA4AF;}

/* Lively page background */
[data-testid="stAppViewContainer"] {background:
    radial-gradient(900px 480px at 100% 100%, rgba(255,122,26,.11), transparent 62%),
    radial-gradient(900px 520px at 0% 0%, rgba(91,63,214,.09), transparent 60%), #FCFBFF;}
[data-testid="stHeader"] {background:rgba(252,251,255,.82); backdrop-filter:blur(8px);}
[data-testid="stBottom"], [data-testid="stBottom"] > div {background:transparent;}
[data-testid="stBottomBlockContainer"] {background:linear-gradient(to top, #FCFBFF 72%, rgba(252,251,255,0));}
[data-testid="stChatInput"] {max-width:620px;}
[data-testid="stVerticalBlockBorderWrapper"] {background:#FFFFFF;}

/* Welcome section */
@keyframes gf-pop {from {opacity:0; transform:translateY(10px) scale(.97);} to {opacity:1; transform:none;}}
.gf-welcome {padding:.8rem 0 .4rem; animation:gf-pop .5s ease-out both;}
.gf-kicker {font-size:.72rem; letter-spacing:.14em; text-transform:uppercase; font-weight:700; color:var(--gf-accent);}
.gf-welcome h2 {font-size:2rem; font-weight:700; letter-spacing:-.03em; line-height:1.15; margin:.35rem 0 .5rem;
                padding:0; color:var(--gf-ink);}
.gf-grad {background:var(--gf-brand); -webkit-background-clip:text; background-clip:text; color:transparent;}
.gf-welcome p {color:var(--gf-muted); font-size:.97rem; max-width:37rem; margin:0 0 1.1rem; line-height:1.5;}
.gf-features {display:grid; grid-template-columns:repeat(3, 1fr); gap:.8rem;}
.gf-feature {background:#fff; border:1px solid var(--gf-line); border-radius:16px; padding:1rem;
             transition:transform .2s ease, box-shadow .2s ease;}
.gf-feature:hover {transform:translateY(-4px); box-shadow:0 18px 32px -18px rgba(21,18,43,.38);}
.gf-ficon {width:38px; height:38px; border-radius:11px; display:flex; align-items:center; justify-content:center;
           margin-bottom:.65rem;}
.gf-feature b {display:block; font-size:.95rem; color:var(--gf-ink);}
.gf-feature span {color:var(--gf-muted); font-size:.82rem; line-height:1.4; display:block; margin-top:.15rem;}

/* Mascot */
.gf-bunny {width:100%; height:auto; display:block; filter:drop-shadow(0 12px 16px rgba(21,18,43,.22));
           transition:transform .2s ease;}
.gf-bubble {position:relative; background:#fff; color:var(--gf-ink); border:1px solid var(--gf-line);
            border-radius:16px 16px 4px 16px; padding:.6rem .85rem; font-size:.9rem; font-weight:650; line-height:1.35;
            box-shadow:0 16px 32px -14px rgba(21,18,43,.42); animation:gf-pop .5s .3s ease-out both; margin-bottom:.3rem;}
.gf-bubble small {display:block; font-weight:500; color:var(--gf-muted); font-size:.74rem; margin-top:.15rem;}
.st-key-mascot, .st-key-mascot_mini {position:fixed; right:22px; bottom:8px; z-index:1000; gap:0 !important;}
.st-key-mascot {width:205px !important;} .st-key-mascot_mini {width:112px !important;}
.st-key-mascot:hover .gf-bunny, .st-key-mascot_mini:hover .gf-bunny {transform:scale(1.04);}
.st-key-mascot_btn {position:absolute !important; inset:0; z-index:2;}
.st-key-mascot_btn .stButton, .st-key-mascot_btn button {width:100%; height:100%; opacity:0; cursor:pointer;}
.st-key-mascot_panel, .st-key-mascot_panel_mini {position:fixed; right:22px; width:330px !important; z-index:1001;
    background:#fff; border:1px solid var(--gf-line); border-radius:18px; padding:1rem 1rem 1.1rem;
    box-shadow:0 26px 60px -20px rgba(21,18,43,.5); animation:gf-pop .25s ease-out both; gap:.45rem !important;}
.st-key-mascot_panel {bottom:250px;} .st-key-mascot_panel_mini {bottom:146px;}
.st-key-mascot_panel button, .st-key-mascot_panel_mini button {justify-content:flex-start;}
@media (prefers-reduced-motion: reduce) {.gf-bubble, .gf-welcome {animation:none !important;}}

/* Streamlit polish */
[data-testid="stChatMessage"] {padding:.55rem .8rem; gap:.7rem;}
[data-testid="stMetric"] {background:#fff; border-top:3px solid transparent; border-image:var(--gf-brand) 1;}
[data-testid="stMetricValue"] {font-weight:650; letter-spacing:-.02em; color:var(--gf-ink);}
[data-testid="stMetricLabel"] p {color:var(--gf-muted); font-size:.8rem;}
[data-testid="stExpander"] details summary p {font-size:.85rem;}
h3 {letter-spacing:-.015em;}
</style>
"""


def setup() -> None:
    st.html(CSS)


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def hero(title: str, subtitle: str, chips: list[str] | None = None, eyebrow: str = "GoodFoods Hospitality · Bengaluru") -> None:
    """Page header band: eyebrow, title, one-line description, optional fact pills on the right."""
    pills = "".join(f'<span class="gf-pill">{esc(c)}</span>' for c in chips or [])
    st.html(f'<div class="gf-head"><div><div class="gf-eyebrow">{esc(eyebrow)}</div><h1>{esc(title)}</h1>'
            f'<p>{esc(subtitle)}</p></div>{f"<div class=gf-pills>{pills}</div>" if pills else ""}</div>')


def label(text: str) -> None:
    st.html(f'<div class="gf-label">{esc(text)}</div>')


def status(text: str, kind: str = "ok") -> str:
    """HTML for a status pill. kind: ok | warn | info | bad | neutral."""
    return f'<span class="gf-status gf-{kind}">{esc(text)}</span>'


def inr(value: float) -> str:
    """Indian-style compact rupees: ₹8,500 · ₹4.2 L · ₹1.3 Cr."""
    v = float(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e7:
        return f"{sign}₹{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"{sign}₹{v / 1e5:.1f} L"
    return f"{sign}₹{v:,.0f}"
