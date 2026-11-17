"""Interactive ROI model for the GoodFoods concierge. Every input is an editable assumption."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from app.ui import runtime, theme

theme.setup()
theme.hero("Return on Investment Model",
           "A transparent business case. Every input is an adjustable assumption, to be replaced with GoodFoods' "
           "operating data during discovery.",
           ["Conservative defaults", "Contribution margin basis", "Attribution discount applied"])

restaurants = len(runtime.catalog().restaurants)
inputs, results = st.columns([2, 3], gap="large")

with inputs:
    with st.expander("Demand", expanded=True, icon=":material/call:"):
        locations = st.number_input("Restaurants", 1, 500, restaurants)
        requests = st.slider("Reservation requests per restaurant per day", 5, 150, 30,
                             help="Calls, WhatsApps, web and aggregator booking attempts.")
        lost = st.slider("Requests lost today (missed calls, after-hours, slow replies)", 0, 40, 10, format="%d%%") / 100
        capture = st.slider("Share of those the concierge converts", 0, 100, 50, format="%d%%") / 100
        full_share = st.slider("Requests that hit a fully booked slot", 0, 40, 10, format="%d%%") / 100
        base_save = st.slider("Saved by staff today (they offer another option)", 0, 100, 15, format="%d%%") / 100
        ai_save = st.slider("Saved by the concierge (sister outlets, other times, waitlist)", 0, 100, 35,
                            format="%d%%") / 100
    with st.expander("Value per cover", icon=":material/payments:"):
        party = st.slider("Average party size", 1.0, 6.0, 2.8, 0.1)
        spend = st.number_input("Average spend per cover (₹)", 100, 10000, 750, step=50)
        margin = st.slider("Contribution margin on an extra cover", 10, 80, 40, format="%d%%",
                           help="Revenue minus food cost and variable costs. Fixed rent and salaries don't grow "
                                "with one more table.") / 100
        noshow_before = st.slider("No-show rate today", 0.0, 20.0, 8.0, 0.5, format="%.1f%%") / 100
        noshow_after = st.slider("No-show rate with reminders and one-tap cancel", 0.0, 20.0, 5.0, 0.5,
                                 format="%.1f%%") / 100
        resale = st.slider("Freed tables that get resold", 0, 100, 50, format="%d%%") / 100
    with st.expander("Channel and operations savings", icon=":material/sync_alt:"):
        agg_share = st.slider("Bookings via aggregators today", 0, 80, 30, format="%d%%") / 100
        shift = st.slider("Of those, shifted to the direct channel", 0, 100, 15, format="%d%%") / 100
        fee = st.number_input("Aggregator fee per cover (₹)", 0, 500, 50)
        handled = st.slider("Requests handled end-to-end by the concierge", 0, 100, 60, format="%d%%") / 100
        minutes = st.slider("Staff minutes per request today", 0.0, 10.0, 3.0, 0.5)
        wage = st.number_input("Staff cost per hour (₹)", 50, 2000, 200)
    with st.expander("Costs and confidence", icon=":material/tune:"):
        llm_cost = st.number_input("LLM cost per conversation (₹)", 0.0, 50.0, 0.15, 0.05,
                                   help="Roughly 12k input + 0.6k output tokens on a hosted 8B model costs well under "
                                        "₹0.10; padded for retries.")
        msg_cost = st.number_input("WhatsApp / telephony per conversation (₹)", 0.0, 50.0, 0.6, 0.1)
        platform = st.number_input("Platform, hosting & support per month (₹)", 0, 5_000_000, 250_000, step=25_000)
        build = st.number_input("One-time build & integration (₹)", 0, 50_000_000, 2_000_000, step=100_000)
        attribution = st.slider("Attribution confidence", 10, 100, 50, format="%d%%",
                                help="Haircut on all benefits: how much of the uplift you'd credit to the concierge "
                                     "rather than seasonality or marketing.") / 100

# ------------------------------------------------------------------ model (monthly)
month = 30
req = locations * requests * month
booked_today = req * (1 - lost)
recovered_bookings = req * lost * capture
overflow_bookings = req * full_share * max(ai_save - base_save, 0)
noshow_bookings = booked_today * max(noshow_before - noshow_after, 0) * resale
covers = (recovered_bookings + overflow_bookings + noshow_bookings) * party
value_per_cover = spend * margin
benefits = {
    "Recovered lost requests": recovered_bookings * party * value_per_cover,
    "Full slots rerouted": overflow_bookings * party * value_per_cover,
    "No-shows resold": noshow_bookings * party * value_per_cover,
    "Aggregator fees avoided": booked_today * agg_share * shift * party * fee,
    "Staff time freed": req * handled * minutes / 60 * wage,
}
benefits = {k: v * attribution for k, v in benefits.items()}
conversations = req * handled
costs = {"LLM inference": conversations * llm_cost, "Messaging": conversations * msg_cost, "Platform & support": platform}
total_benefit, total_cost = sum(benefits.values()), sum(costs.values())
net = total_benefit - total_cost
breakeven_covers_day = total_cost / max(value_per_cover, 1) / month / locations

with results:
    a, b, c, d = st.columns(4)
    a.metric("Net monthly benefit", theme.inr(net), border=True)
    b.metric("Return on operating cost", f"{total_benefit / total_cost:.1f}×" if total_cost else "–", border=True)
    c.metric("Payback period (months)", f"{build / net:.1f}" if net > 0 else "Not reached", border=True,
             help="One-time build and integration cost divided by the monthly net benefit.")
    d.metric("Break-even covers", f"{breakeven_covers_day:.2f}", border=True,
             help="Additional covers per restaurant per day required to offset all operating costs.")
    st.success(f"The concierge covers its operating costs at **{breakeven_covers_day:.2f} additional covers per "
               f"restaurant per day**, which is less than a single guest. After the attribution discount, the model "
               f"projects **{covers * attribution / month / locations:.1f}**.", icon=":material/flag:")

    rows = [{"item": k, "amount": v, "type": "Benefit"} for k, v in benefits.items()]
    rows += [{"item": k, "amount": -v, "type": "Cost"} for k, v in costs.items()]
    df = pd.DataFrame(rows)
    df["label"] = df["amount"].map(theme.inr)
    st.markdown("**Monthly benefit and cost breakdown**")
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("amount:Q", title="₹ per month", axis=alt.Axis(format="~s")),
        y=alt.Y("item:N", sort=None, title=None, axis=alt.Axis(labelLimit=240)),
        color=alt.Color("type:N", scale=alt.Scale(domain=["Benefit", "Cost"], range=[theme.POSITIVE, theme.NEGATIVE]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["item", "label"])
    st.altair_chart((bars + bars.mark_text(align="left", dx=4, color=theme.INK).encode(text="label"))
                    .properties(height=300), width="stretch")

    st.markdown("**Underlying monthly volumes**")
    st.dataframe(pd.DataFrame([
        {"metric": "Reservation requests", "value": f"{req:,.0f}"},
        {"metric": "Incremental reservations (before attribution)",
         "value": f"{recovered_bookings + overflow_bookings + noshow_bookings:,.0f}"},
        {"metric": "Incremental covers (after attribution)", "value": f"{covers * attribution:,.0f}"},
        {"metric": "Concierge conversations", "value": f"{conversations:,.0f}"},
        {"metric": "Operating cost per conversation", "value": f"₹{total_cost / max(conversations, 1):.2f}"},
    ]), hide_index=True, width="stretch")
    st.caption("All inputs are indicative assumptions for a mid-market Bengaluru restaurant group and do not "
               "represent GoodFoods data. The principal finding is structural: at 8B-model pricing, inference cost "
               "is negligible, and the business case depends on recovering a fraction of one table per restaurant "
               "per day.")
