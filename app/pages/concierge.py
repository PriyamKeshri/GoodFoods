"""Guest-facing concierge chat."""

from __future__ import annotations

import base64

import pandas as pd
import pydeck as pdk
import streamlit as st

from app.ui import cards, mascot, runtime, theme
from goodfoods.agent.core import TraceEvent
from goodfoods.agent.llm import PROFILES

USER_AVATAR, BOT_AVATAR = ":material/person:", ":material/room_service:"
SUGGESTIONS = [  # (icon, button label, message sent)
    (":material/favorite:", "Italian dinner for two, Saturday 8 PM", "Romantic Italian dinner in Indiranagar this Saturday at 8pm for 2"),
    (":material/translate:", "Hinglish request: biryani for four", "kal raat 8 baje 4 logon ke liye biryani, Frazer Town mein"),
    (":material/eco:", "Jain family lunch for six", "Pure veg Jain lunch for 6 in Jayanagar on Sunday"),
    (":material/sports_bar:", "Craft beer and live music", "Craft beer and live music in Koramangala this Friday at 8pm for 6"),
    (":material/confirmation_number:", "Review reservation GF-ANR42", "What time is my booking GF-ANR42?"),
    (":material/groups:", "Corporate dinner for 40 guests", "Office dinner for 40 people next Friday"),
]



def icon(paths: str) -> str:
    """A white line icon as an <img> (st.html strips inline SVG)."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" '
           f'stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>')
    return f'<img width="20" height="20" alt="" src="data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()}">'


WELCOME = f"""
<div class="gf-welcome">
  <div class="gf-kicker">Welcome to GoodFoods</div>
  <h2>Exceptional dining, <span class="gf-grad">one message away.</span></h2>
  <p>Share the occasion, location, date and party size, and the concierge will arrange the rest. For inspiration,
     select our concierge in the corner to view popular requests.</p>
  <div class="gf-features">
    <div class="gf-feature"><div class="gf-ficon" style="background:linear-gradient(135deg,#5B3FD6,#8B5CF6)">
      {icon('<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 16v4M17 18h4"/>')}</div>
      <b>Tailored recommendations</b><span>Venues matched to your occasion, cuisine, budget and dietary needs.</span></div>
    <div class="gf-feature"><div class="gf-ficon" style="background:linear-gradient(135deg,#FF7A1A,#E11D74)">
      {icon('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>')}</div>
      <b>Real-time availability</b><span>Live table inventory across all GoodFoods restaurants.</span></div>
    <div class="gf-feature"><div class="gf-ficon" style="background:linear-gradient(135deg,#0B7A55,#14B8A6)">
      {icon('<path d="M12 3l7 3v6c0 4.5-3 7.8-7 9-4-1.2-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/>')}</div>
      <b>Confirmed in moments</b><span>A reservation code and WhatsApp confirmation once you approve.</span></div>
  </div>
</div>
"""

runtime.init_state()
theme.setup()
ss = st.session_state
catalog = runtime.catalog()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    theme.label("Language model")
    ss.profile = st.selectbox("Model", runtime.profile_keys(), index=runtime.profile_keys().index(ss.profile),
                              format_func=lambda k: PROFILES[k].label, label_visibility="collapsed")
    ready, model_note = runtime.model_status(ss.profile)
    st.html(theme.status("Operational" if ready else "Unavailable", "ok" if ready else "bad"))
    if not ready:
        st.caption(model_note)
    elif PROFILES[ss.profile].local:
        st.caption("Running locally: responses take 15–30 seconds. For 1–3 second responses on the same Llama 3.1 8B "
                   "model, set GROQ_API_KEY and select the Groq profile.")
    theme.label("Agent controls")
    ss.guardrails = st.toggle("Reliability guardrails", value=ss.guardrails,
                              help="Argument repair, provenance checks, two-step confirmation and response "
                                   "verification. Disable to observe the unassisted 8B model.")
    ss.show_trace = st.toggle("Display agent trace", value=ss.show_trace,
                              help="Shows every model decision, MCP tool call and guardrail intervention for each response.")
    if st.button("Start new conversation", icon=":material/restart_alt:", width="stretch"):
        runtime.reset_conversation()
        st.rerun()
    theme.label("Sample data")
    with st.expander("Sample reservations", icon=":material/confirmation_number:"):
        st.markdown("- **GF-ANR42** · Ananya Rao, Saffron Terrace (98450 12345)\n"
                    "- **GF-FTK19** · Fatima Khan, Nawab's Biryani House\n"
                    "- **GF-RHL77** · Rahul Mehta, The Brew Ledger\n\n"
                    "Trattoria Verde, Indiranagar is fully booked on Saturday evening, which demonstrates how the "
                    "concierge offers alternatives.")
    mcp = runtime.mcp_client()
    st.caption(f"Connected to MCP server `{mcp.server_info.get('name')}` v{mcp.server_info.get('version')} "
               f"(protocol {mcp.protocol_version}).")

# ------------------------------------------------------------------ header
theme.hero("Reservations Concierge",
           "Describe the occasion, location, date and party size. The concierge verifies live table availability "
           "across every GoodFoods restaurant and completes your reservation in English, Hindi or Hinglish.",
           [f"{len(catalog.restaurants)} restaurants", f"{len(catalog.areas)} neighbourhoods",
            f"{len(catalog.brands)} dining concepts", "Real-time availability"])

chat_col, side_col = st.columns([0.66, 0.34], gap="large")
prompt = st.chat_input("Message the concierge, for example: “A table for four in Frazer Town tomorrow at 8 PM”") \
    or ss.pop("queued", None)


def run_turn(text: str) -> None:
    agent = runtime.get_agent()
    ss.messages.append({"role": "user", "content": text})
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(text)
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        progress = st.status("Processing your request…", expanded=False)

        def on_event(event: TraceEvent) -> None:
            if event.kind == "tool":
                progress.update(label=f"{cards.TOOL_LABELS.get(event.title, event.title)}…")
                progress.write(f"Tool · `{event.title}` · {event.ms:.0f} ms")
            elif event.kind == "guardrail":
                progress.write(f"Guardrail · {event.title}")
            elif event.kind == "llm":
                progress.update(label="Processing your request…")
                progress.write(f"Model · {event.title} · {event.ms / 1000:.1f}s")

        result = agent.chat(text, on_event=on_event)
        progress.update(label=f"Completed in {result.latency_ms / 1000:.1f}s", state="error" if result.error else "complete")
    ss.messages.append({"role": "assistant", "content": result.reply, "turn": runtime.pack(result)})
    st.rerun()


with chat_col:
    if not ss.messages and not prompt:
        st.html(WELCOME)
    last = len(ss.messages) - 1
    for i, m in enumerate(ss.messages):
        with st.chat_message(m["role"], avatar=USER_AVATAR if m["role"] == "user" else BOT_AVATAR):
            st.markdown(m["content"])
            turn = m.get("turn")
            if turn:
                cards.render_turn(turn, interactive=(i == last and not prompt), key=f"m{i}")
                if ss.show_trace:
                    cards.trace(turn)
    if prompt:
        if not runtime.model_status(ss.profile)[0]:
            st.warning("The selected model is currently unavailable. " + runtime.model_status(ss.profile)[1])
        else:
            run_turn(prompt)

# ------------------------------------------------------------------ context panel
turns = [m["turn"] for m in ss.messages if m.get("turn")]
highlight: dict[str, str] = {}
bookings: dict[str, dict] = {}
for t in turns:
    for run in t["tools"]:
        data = run.get("structured") or {}
        if not run["ok"] or not data:
            continue
        if run["name"] == "search_restaurants":
            highlight = {k: v for k, v in highlight.items() if v == "booked"}
            for r in data.get("results", []):
                full = r.get("availability", {}).get("status") == "full"
                highlight.setdefault(r["id"], "full" if full else "recommended")
        if data.get("status") in ("confirmed", "modified") and data.get("confirmation_code"):
            bookings[data["confirmation_code"]] = data["booking"]
            highlight[data["booking"]["restaurant_id"]] = "booked"
        if data.get("status") == "cancelled":
            bookings.pop(data.get("confirmation_code"), None)

with side_col:
    with st.container(border=True):
        theme.label("Restaurant map")
        colors = {"other": [107, 104, 128, 70], "recommended": [91, 63, 214, 235], "full": [225, 29, 116, 235],
                  "booked": [255, 122, 26, 245]}
        radius = {"other": 150, "recommended": 430, "full": 390, "booked": 480}
        df = pd.DataFrame([{"lat": r.lat, "lng": r.lng, "name": r.name, "kind": highlight.get(r.id, "other")}
                           for r in catalog.restaurants])
        df["color"] = df["kind"].map(colors)
        df["radius"] = df["kind"].map(radius)
        focus = df[df["kind"] != "other"]
        view = pdk.ViewState(latitude=float(focus["lat"].mean()) if len(focus) else 12.965,
                             longitude=float(focus["lng"].mean()) if len(focus) else 77.62,
                             zoom=11.2 if len(focus) else 10.2)
        layer = pdk.Layer("ScatterplotLayer", data=df.sort_values("kind", key=lambda s: s != "other"),
                          get_position="[lng, lat]", get_fill_color="color", get_radius="radius", pickable=True)
        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view, tooltip={"text": "{name}"}, map_style=None),
                        height=195)
        st.html('<div class="gf-legend"><span><i style="background:#5B3FD6"></i>Recommended</span>'
                '<span><i style="background:#E11D74"></i>Fully booked</span>'
                '<span><i style="background:#FF7A1A"></i>Reserved</span></div>')

    if bookings:
        with st.container(border=True):
            theme.label("Your reservations")
            for code, b in bookings.items():
                st.html(f'<div class="gf-name">{theme.esc(b["restaurant"])}</div><div class="gf-meta"><b>'
                        f'{theme.esc(b["date"])} · {theme.esc(b["time"])}</b> · {b["party_size"]} guests · '
                        f'{theme.esc(code)}</div>')
                st.download_button("Add to calendar", cards.ics(b, code), file_name=f"{code}.ics",
                                   mime="text/calendar", key=f"side-ics-{code}", icon=":material/event:")

    if turns:
        with st.container(border=True):
            theme.label("Session metrics")
            n_tools = sum(len(t["tools"]) for t in turns)
            n_guard = sum(1 for t in turns for e in t["events"]
                          if e["kind"] == "guardrail" and not e["title"].startswith("Resolved dates"))
            avg = sum(t["latency_ms"] for t in turns) / len(turns) / 1000
            st.html('<div class="gf-stats">'
                    f'<div><b>{n_tools}</b><span>Tool calls</span></div>'
                    f'<div><b>{n_guard}</b><span>Guardrail actions</span></div>'
                    f'<div><b>{avg:.1f}s</b><span>Avg. response</span></div></div>')

mascot.render(SUGGESTIONS, greeting=not ss.messages and not prompt)
