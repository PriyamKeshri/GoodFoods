"""Operations dashboard for GoodFoods managers."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from app.ui import analytics, theme
from goodfoods import config

theme.setup()
theme.hero("Operations Overview",
           "Network-wide demand, capacity utilisation, recovered covers and concierge performance across all 76 "
           "restaurants.",
           ["Historical bookings: simulated", "Concierge activity: live"])

now = config.now()
today = pd.Timestamp(now.date())
res = analytics.reservations()
live = res[res["source"] == "live"]
turns = analytics.telemetry_table("SELECT * FROM turns")
guards = analytics.telemetry_table("SELECT * FROM guardrail_events")
tools = analytics.telemetry_table("SELECT * FROM tool_calls")
funnel = analytics.telemetry_table("SELECT * FROM funnel")
waitlist = analytics.db_table("SELECT * FROM waitlist ORDER BY created_at DESC")

# ------------------------------------------------------------------ KPIs
todays = res[(res["date"] == today) & res["status"].isin(["confirmed", "completed"])]
recovered = live[(live["status"] == "confirmed") & live["requested_restaurant_id"].notna()]
k = st.columns(6)
k[0].metric("Covers today", f"{int(todays['party_size'].sum()):,}", border=True,
            help="Guests holding a reservation today across all restaurants and channels.")
k[1].metric("Covers after 7 PM", f"{int(todays[todays['start_min'] >= 19 * 60]['party_size'].sum()):,}", border=True)
k[2].metric("Concierge reservations", int((live["status"] == "confirmed").sum()), border=True,
            help="Reservations confirmed through the concierge in this environment.")
k[3].metric("Covers recovered", int(recovered["party_size"].sum()) + int((waitlist["status"] == "offered").sum()),
            border=True, help="Guests redirected to a sister restaurant after their first choice was full, plus "
                              "cancelled tables re-offered to the waitlist.")
k[4].metric("Median response time", f"{turns['latency_ms'].median() / 1000:.1f}s" if len(turns) else "–", border=True)
saves = guards[~guards["kind"].str.startswith("Resolved dates")] if len(guards) else guards
k[5].metric("Guardrail interventions", len(saves), border=True,
            help="Instances in which the reliability layer corrected or blocked a model action.")

demand_tab, agent_tab, signals_tab, feed_tab = st.tabs(
    [":material/monitoring: Demand and capacity", ":material/smart_toy: Concierge performance",
     ":material/explore: Expansion signals", ":material/rss_feed: Activity feed"])

# ------------------------------------------------------------------ demand & capacity
with demand_tab:
    left, right = st.columns([3, 2], gap="large")
    with left:
        brands = ["All concepts", *sorted(res["brand"].dropna().unique())]
        brand = st.selectbox("Concept", brands, label_visibility="collapsed")
        meta = analytics.restaurants()
        scope = res if brand == "All concepts" else res[res["brand"] == brand]
        seats = int(meta["seats"].sum() if brand == "All concepts" else meta.loc[meta["brand"] == brand, "seats"].sum())
        heat = analytics.occupancy_by_weekday_hour(scope, seats, today)
        st.markdown("**Seat occupancy by weekday and hour** <span class='gf-muted'>(last 14 days)</span>",
                    unsafe_allow_html=True)
        st.altair_chart(alt.Chart(heat).mark_rect(cornerRadius=3).encode(
            x=alt.X("hour:O", title="Hour of day"),
            y=alt.Y("weekday:O", sort=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], title=None),
            color=alt.Color("occupancy:Q", title="Occupancy", scale=alt.Scale(scheme=theme.SEQUENTIAL_SCHEME, domain=[0, 1]),
                            legend=alt.Legend(format="%")),
            tooltip=["weekday", "hour", alt.Tooltip("occupancy:Q", format=".0%")]).properties(height=250),
            width="stretch")
        st.caption("Friday and Saturday dinner services operate at peak utilisation, with the most popular venues fully "
                   "booked, while weekday afternoons remain largely unoccupied. Off-peak incentives and "
                   "cross-location routing are designed to address this imbalance.")
    with right:
        tonight = analytics.occupancy_at(res, today, 20 * 60 + 30).nlargest(12, "occupancy")
        st.markdown("**Highest occupancy this evening at 8:30 PM**")
        st.altair_chart(alt.Chart(tonight).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("occupancy:Q", title=None, axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("restaurant:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
            color=alt.value(theme.ACCENT),
            tooltip=["restaurant", alt.Tooltip("occupancy:Q", format=".0%"), "guests"]).properties(height=300),
            width="stretch")

    c1, c2 = st.columns([3, 2], gap="large")
    window = res[(res["date"] >= today - pd.Timedelta(days=14)) & (res["date"] <= today + pd.Timedelta(days=7))
                 & (res["status"] != "cancelled")]
    mix = window.groupby([window["date"].dt.date, "channel"])["party_size"].sum().reset_index()
    mix.columns = ["date", "channel", "covers"]
    with c1:
        st.markdown("**Covers by booking channel** <span class='gf-muted'>(previous 14 days and forward bookings)</span>",
                    unsafe_allow_html=True)
        st.altair_chart(alt.Chart(mix).mark_bar(size=15, stroke="#FFFFFF", strokeWidth=1).encode(
            x=alt.X("date:T", title=None), y=alt.Y("covers:Q", title="Covers"),
            color=alt.Color("channel:N", scale=alt.Scale(
                domain=["walk_in", "phone", "aggregator", "website", "ai_concierge"],
                range=theme.CATEGORICAL)),
            tooltip=["date:T", "channel", "covers"]).properties(height=260), width="stretch")
    with c2:
        past = res[(res["date"] < today) & res["status"].isin(["completed", "no_show"]) & (res["channel"] != "walk_in")]
        ns = past.groupby("channel")["status"].apply(lambda s: (s == "no_show").mean()).reset_index(name="no_show_rate")
        st.markdown("**No-show rate by channel**")
        st.altair_chart(alt.Chart(ns).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("no_show_rate:Q", title=None, axis=alt.Axis(format="%")), y=alt.Y("channel:N", sort="-x", title=None),
            color=alt.value(theme.ACCENT), tooltip=["channel", alt.Tooltip("no_show_rate:Q", format=".1%")])
            .properties(height=200), width="stretch")
        lost = past["status"].eq("no_show").sum() * 2.8 * 750
        st.caption(f"Simulated no-shows over the previous 14 days represent approximately {theme.inr(lost)} in unsold "
                   "covers. Automated reminders with one-tap cancellation, combined with waitlist backfill, "
                   "address this loss directly.")

# ------------------------------------------------------------------ AI concierge
with agent_tab:
    if turns.empty:
        st.info("No concierge conversations have been recorded yet. Activity will appear here once guests begin "
                "interacting with the concierge.", icon=":material/forum:")
    else:
        a, b, c, d = st.columns(4)
        a.metric("Conversations", turns["session_id"].nunique(), border=True)
        b.metric("Guest turns", len(turns), border=True)
        c.metric("Tool call success rate", f"{tools['ok'].mean():.0%}" if len(tools) else "–", border=True)
        d.metric("90th percentile response", f"{turns['latency_ms'].quantile(.9) / 1000:.1f}s", border=True)
        l, r = st.columns(2, gap="large")
        with l:
            steps = ["search", "full", "hold", "booked"]
            labels = {"search": "Searched", "full": "Hit a full slot", "hold": "Held a table", "booked": "Booked"}
            f = pd.DataFrame({"stage": [labels[s] for s in steps],
                              "sessions": [funnel[funnel["event"] == s]["session_id"].nunique() if len(funnel) else 0
                                           for s in steps]})
            st.markdown("**Conversation funnel** <span class='gf-muted'>(distinct conversations)</span>",
                        unsafe_allow_html=True)
            st.altair_chart(alt.Chart(f).mark_bar(cornerRadiusEnd=3, color=theme.ACCENT).encode(
                x=alt.X("sessions:Q", title=None), y=alt.Y("stage:N", sort=[labels[s] for s in steps], title=None),
                tooltip=["stage", "sessions"]).properties(height=190), width="stretch")
        with r:
            st.markdown("**Guardrail interventions by type**")
            if guards.empty:
                st.caption("No records yet.")
            else:
                g = guards.assign(kind=guards["kind"].map(analytics.guardrail_kind)).groupby("kind").size() \
                    .reset_index(name="count")
                st.altair_chart(alt.Chart(g).mark_bar(cornerRadiusEnd=3, color=theme.ACCENT).encode(
                    x=alt.X("count:Q", title=None), y=alt.Y("kind:N", sort="-x", title=None),
                    tooltip=["kind", "count"]).properties(height=190), width="stretch")
        if len(tools):
            per_tool = tools.groupby("tool").agg(calls=("ok", "size"), success=("ok", "mean"), blocked=("blocked", "sum"),
                                                 avg_ms=("latency_ms", "mean")).reset_index()
            st.markdown("**Tool reliability**")
            st.dataframe(per_tool, hide_index=True, width="stretch", column_config={
                "success": st.column_config.ProgressColumn("Success", format="percent", min_value=0, max_value=1),
                "avg_ms": st.column_config.NumberColumn("Avg latency (ms)", format="%.0f")})
        st.caption("Personal data in telemetry is redacted: telephone numbers retain only their final two digits and "
                   "names are reduced to initials.")

# ------------------------------------------------------------------ expansion signals
with signals_tab:
    demand = analytics.db_table("SELECT * FROM demand_log")
    st.markdown("Every search and availability request is recorded. **Unmet demand**, defined as searches without a "
                "match or requests for fully booked periods, indicates where GoodFoods is declining guests. This "
                "evidence informs decisions on new outlets, additional concepts and peak-period capacity.")
    if demand.empty:
        st.info("No searches have been recorded yet.", icon=":material/search:")
    else:
        unmet = demand[demand["outcome"].isin(["full", "no_match"])]
        l, r = st.columns(2, gap="large")
        with l:
            by_area = unmet.groupby("area").size().reset_index(name="unmet").nlargest(10, "unmet")
            st.markdown("**Unmet requests by neighbourhood**")
            st.altair_chart(alt.Chart(by_area).mark_bar(cornerRadiusEnd=3, color=theme.ACCENT).encode(
                x=alt.X("unmet:Q", title=None), y=alt.Y("area:N", sort="-x", title=None)).properties(height=220),
                width="stretch")
        with r:
            st.markdown("**Searches by outcome**")
            st.dataframe(demand.groupby(["outcome"]).size().reset_index(name="requests"), hide_index=True,
                         width="stretch")
        st.dataframe(unmet[["ts", "kind", "area", "cuisine", "date", "party_size", "outcome"]].tail(25),
                     hide_index=True, width="stretch")

# ------------------------------------------------------------------ live feed
with feed_tab:
    l, r = st.columns(2, gap="large")
    with l:
        st.markdown("**Recent concierge reservations**")
        feed = live.sort_values("created_at", ascending=False).head(15).copy()
        if feed.empty:
            st.caption("No records yet.")
        else:
            feed["time"] = feed["start_min"].map(lambda m: f"{m // 60:02d}:{m % 60:02d}")
            st.dataframe(feed[["code", "restaurant", "date", "time", "party_size", "status"]], hide_index=True,
                         width="stretch", column_config={"date": st.column_config.DateColumn("date")})
        st.markdown("**Waitlist**")
        st.dataframe(waitlist[["id", "restaurant_id", "date", "party_size", "status"]].head(10) if len(waitlist)
                     else waitlist, hide_index=True, width="stretch")
    with r:
        st.markdown("**WhatsApp outbox** <span class='gf-muted'>(simulated)</span>", unsafe_allow_html=True)
        outbox = analytics.db_table("SELECT created_at, kind, message FROM notifications ORDER BY id DESC LIMIT 12")
        st.dataframe(outbox, hide_index=True, width="stretch")
        st.markdown("**Callback requests**")
        st.dataframe(analytics.db_table("SELECT id, reason, details, party_size, status FROM callbacks "
                                        "ORDER BY created_at DESC LIMIT 10"), hide_index=True, width="stretch")
