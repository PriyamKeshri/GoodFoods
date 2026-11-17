"""The GoodFoods concierge mascot: an animated rabbit that waves from the bottom-right corner.

It is pure SVG + CSS (animations live in ``theme.CSS``). The rabbit is overlaid with an invisible
Streamlit button, so a click toggles a panel of suggested requests without any JavaScript.
Suggestions are sent as ordinary guest messages: the LLM still decides what to do with them.
"""

from __future__ import annotations

import base64

import streamlit as st

GREETING = "Hi, how may I assist you today?"

RABBIT_SVG = """
<svg viewBox="0 0 200 230" xmlns="http://www.w3.org/2000/svg">
  <style>
    @keyframes wave {from {transform:rotate(-10deg);} to {transform:rotate(18deg);}}
    @keyframes bob {from {transform:translateY(0);} to {transform:translateY(-5px);}}
    @keyframes blink {0%, 91%, 100% {transform:scaleY(1);} 95% {transform:scaleY(.08);}}
    @keyframes twitch {0%, 84%, 100% {transform:rotate(0);} 90% {transform:rotate(-8deg);}}
    .gf-bob {animation:bob 1.6s ease-in-out infinite alternate;}
    .gf-wave {transform-box:fill-box; transform-origin:12% 92%; animation:wave .55s ease-in-out infinite alternate;}
    .gf-blink {transform-box:fill-box; transform-origin:center; animation:blink 4.2s infinite;}
    .gf-ear-l {transform-box:fill-box; transform-origin:50% 100%; animation:twitch 5s infinite;}
    @media (prefers-reduced-motion: reduce) {* {animation:none !important;}}
  </style>
  <defs>
    <linearGradient id="gfShirt" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#FF7A1A"/><stop offset="1" stop-color="#E11D74"/>
    </linearGradient>
  </defs>
  <ellipse cx="100" cy="222" rx="54" ry="6" fill="#15122B" opacity=".13"/>
  <g class="gf-bob">
    <g class="gf-ear-l">
      <ellipse cx="78" cy="40" rx="13" ry="36" fill="#FFFFFF" stroke="#D9D2F0" stroke-width="2" transform="rotate(-9 78 40)"/>
      <ellipse cx="78" cy="44" rx="6" ry="24" fill="#FFC2D9" transform="rotate(-9 78 44)"/>
    </g>
    <g class="gf-ear-r">
      <ellipse cx="122" cy="40" rx="13" ry="36" fill="#FFFFFF" stroke="#D9D2F0" stroke-width="2" transform="rotate(9 122 40)"/>
      <ellipse cx="122" cy="44" rx="6" ry="24" fill="#FFC2D9" transform="rotate(9 122 44)"/>
    </g>
    <ellipse cx="80" cy="213" rx="20" ry="9" fill="#FFFFFF" stroke="#D9D2F0" stroke-width="2"/>
    <ellipse cx="120" cy="213" rx="20" ry="9" fill="#FFFFFF" stroke="#D9D2F0" stroke-width="2"/>
    <ellipse cx="52" cy="176" rx="9" ry="21" fill="#FFFFFF" stroke="#D9D2F0" stroke-width="2" transform="rotate(16 52 176)"/>
    <path d="M62 139 Q100 125 138 139 L147 199 Q100 215 53 199 Z" fill="url(#gfShirt)"/>
    <path d="M66 138 L44 160 L58 172 L74 154 Z" fill="url(#gfShirt)"/>
    <g transform="translate(100 168)">
      <path d="M-13 4a13 13 0 0 1 26 0z" fill="#FFFFFF"/>
      <rect x="-16" y="6.5" width="32" height="3.4" rx="1.7" fill="#FFFFFF"/>
      <circle cx="0" cy="-11.5" r="2.5" fill="#FFFFFF"/>
    </g>
    <text x="100" y="193" text-anchor="middle" font-size="9.5" font-weight="700" fill="#FFFFFF"
          font-family="Inter, -apple-system, 'Segoe UI', Arial, sans-serif">GoodFoods</text>
    <g class="gf-wave">
      <path d="M134 138 L158 122 L168 138 L144 156 Z" fill="url(#gfShirt)"/>
      <ellipse cx="172" cy="108" rx="9" ry="23" fill="#FFFFFF" stroke="#D9D2F0" stroke-width="2" transform="rotate(24 172 108)"/>
    </g>
    <ellipse cx="100" cy="98" rx="46" ry="40" fill="#FFFFFF" stroke="#D9D2F0" stroke-width="2"/>
    <ellipse cx="71" cy="109" rx="8" ry="5" fill="#FFC2D9" opacity=".85"/>
    <ellipse cx="129" cy="109" rx="8" ry="5" fill="#FFC2D9" opacity=".85"/>
    <g class="gf-blink">
      <ellipse cx="84" cy="94" rx="5" ry="6.5" fill="#15122B"/><circle cx="85.6" cy="91.8" r="1.6" fill="#FFFFFF"/>
      <ellipse cx="116" cy="94" rx="5" ry="6.5" fill="#15122B"/><circle cx="117.6" cy="91.8" r="1.6" fill="#FFFFFF"/>
    </g>
    <path d="M96 104h8l-4 5z" fill="#F0559A"/>
    <path d="M100 109v4M100 113q-6 6-11 1M100 113q6 6 11 1" stroke="#15122B" stroke-width="1.8" fill="none"
          stroke-linecap="round"/>
  </g>
</svg>
"""
# The sanitiser used by st.html removes inline <svg>, so the mascot ships as a self-animating SVG image.
RABBIT = (f'<img class="gf-bunny" alt="GoodFoods concierge mascot, a waving rabbit" '
          f'src="data:image/svg+xml;base64,{base64.b64encode(RABBIT_SVG.strip().encode()).decode()}">')


def _toggle() -> None:
    st.session_state.mascot_open = not st.session_state.get("mascot_open", False)


def _pick(message: str) -> None:
    st.session_state.queued = message
    st.session_state.mascot_open = False


def render(suggestions: list[tuple[str, str, str]], *, greeting: bool) -> None:
    """Draw the mascot. ``greeting=True`` shows the full-size rabbit with its speech bubble (home state)."""
    is_open = st.session_state.get("mascot_open", False)
    size = "" if greeting else "_mini"
    with st.container(key=f"mascot{size}"):
        bubble = (f'<div class="gf-bubble">{GREETING}<small>Select me to view popular requests.</small></div>'
                  if greeting and not is_open else "")
        st.html(bubble + RABBIT)
        st.button("Show suggested requests", key="mascot_btn", on_click=_toggle, width="stretch")
    if is_open:
        with st.container(key=f"mascot_panel{size}"):
            st.html('<div class="gf-label">Popular requests</div>'
                    '<div class="gf-muted">Select a request to begin, or type your own below.</div>')
            for i, (icon, text, message) in enumerate(suggestions):
                st.button(text, key=f"suggest-{i}", on_click=_pick, args=(message,), icon=icon, width="stretch")
