"""GoodFoods Concierge: Streamlit entry point.

    streamlit run streamlit_app.py

Configuration comes from environment variables (or a local ``.env``). When deployed on Streamlit Community Cloud,
put the same keys in the app's Secrets; root-level secrets are copied into the environment before anything else
loads, so ``LLM_PROFILE``, ``GROQ_API_KEY`` and friends work identically in both places.
"""

import os
from pathlib import Path

import streamlit as st

STATIC = Path(__file__).parent / "app" / "static"

st.set_page_config(page_title="GoodFoods Concierge", page_icon=str(STATIC / "logo-icon.svg"), layout="wide")

if st.secrets.load_if_toml_exists():
    for key, value in st.secrets.items():
        if isinstance(value, (str, int, float, bool)):
            os.environ.setdefault(key, str(value))

st.logo(str(STATIC / "logo.svg"), size="large", icon_image=str(STATIC / "logo-icon.svg"))

pages = [
    st.Page("app/pages/concierge.py", title="Concierge", icon=":material/room_service:", default=True),
    st.Page("app/pages/dashboard.py", title="Operations", icon=":material/monitoring:"),
    st.Page("app/pages/roi.py", title="ROI Model", icon=":material/calculate:"),
    st.Page("app/pages/internals.py", title="Architecture", icon=":material/account_tree:"),
]
st.navigation(pages, position="top").run()
