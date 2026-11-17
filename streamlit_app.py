"""GoodFoods Concierge: Streamlit entry point.

    streamlit run streamlit_app.py
"""

from pathlib import Path

import streamlit as st

STATIC = Path(__file__).parent / "app" / "static"

st.set_page_config(page_title="GoodFoods Concierge", page_icon=str(STATIC / "logo-icon.svg"), layout="wide")
st.logo(str(STATIC / "logo.svg"), size="large", icon_image=str(STATIC / "logo-icon.svg"))

pages = [
    st.Page("app/pages/concierge.py", title="Concierge", icon=":material/room_service:", default=True),
    st.Page("app/pages/dashboard.py", title="Operations", icon=":material/monitoring:"),
    st.Page("app/pages/roi.py", title="ROI Model", icon=":material/calculate:"),
    st.Page("app/pages/internals.py", title="Architecture", icon=":material/account_tree:"),
]
st.navigation(pages, position="top").run()
