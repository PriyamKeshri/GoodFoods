"""Rich chat cards rendered from MCP ``structuredContent`` (the model sees compact text; the UI sees data).

Buttons never call tools directly: they send a natural-language message on the guest's behalf,
so the LLM still decides what to do, and the hard-coded paths stay out of intent detection.
"""

from __future__ import annotations

import json
import zlib
from datetime import UTC, datetime, timedelta

import streamlit as st

from app.ui.theme import esc, status

TOOL_LABELS = {
    "search_restaurants": "Searching restaurants", "get_restaurant_details": "Retrieving venue details",
    "check_availability": "Checking availability", "hold_table": "Placing a hold on your table",
    "confirm_reservation": "Confirming your reservation", "get_reservation": "Retrieving your reservation",
    "modify_reservation": "Preparing the amendment", "cancel_reservation": "Preparing the cancellation",
    "join_waitlist": "Adding you to the waitlist", "request_callback": "Arranging a callback",
}
# (background, text) tints for venue monograms, picked by brand name so a brand keeps its colour
TINTS = [("linear-gradient(135deg,#5B3FD6,#8B5CF6)", "#FFFFFF"), ("linear-gradient(135deg,#FF7A1A,#E11D74)", "#FFFFFF"),
         ("linear-gradient(135deg,#0B7A55,#14B8A6)", "#FFFFFF"), ("linear-gradient(135deg,#1D4ED8,#38BDF8)", "#FFFFFF"),
         ("linear-gradient(135deg,#B4234B,#F472B6)", "#FFFFFF"), ("linear-gradient(135deg,#15122B,#4C3FA0)", "#FFFFFF")]


def queue(text: str) -> None:
    st.session_state.queued = text


def _button(label: str, prompt: str, key: str, *, primary: bool = False, icon: str | None = None,
            stretch: bool = False) -> None:
    st.button(label, key=key, on_click=queue, args=(prompt,), type="primary" if primary else "secondary", icon=icon,
              width="stretch" if stretch else "content")


def _venue(full_name: str, meta: str, tags: list[str] | None = None) -> str:
    """HTML for a venue row: monogram, name + area, a meta line and optional reason tags."""
    brand, _, area = full_name.partition(", ")
    initials = "".join(w[0] for w in brand.replace("&", " ").replace("'", "").split()[:2]).upper()
    bg, fg = TINTS[zlib.crc32(brand.encode()) % len(TINTS)]
    tag_html = "".join(f'<span class="gf-tag">{esc(t)}</span>' for t in tags or [])
    return (f'<div class="gf-row"><div class="gf-mono" style="background:{bg};color:{fg}">{esc(initials)}</div><div>'
            f'<div class="gf-name">{esc(brand)}{f" <span>· {esc(area)}</span>" if area else ""}</div>'
            f'<div class="gf-meta">{meta}</div>{f"<div class=gf-tags>{tag_html}</div>" if tag_html else ""}</div></div>')


def _kv(pairs: list[tuple[str, str | None]]) -> str:
    return '<dl class="gf-kv">' + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in pairs if v) + "</dl>"


def ics(booking: dict, code: str) -> str:
    start = datetime.strptime(f"{booking['date']} {booking['time']}", "%a, %d %b %Y %I:%M %p")
    end = start + timedelta(minutes=120)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//GoodFoods//Concierge//EN", "BEGIN:VEVENT",
        f"UID:{code}@goodfoods.example", f"DTSTAMP:{stamp}",
        f"DTSTART;TZID=Asia/Kolkata:{start:%Y%m%dT%H%M%S}", f"DTEND;TZID=Asia/Kolkata:{end:%Y%m%dT%H%M%S}",
        f"SUMMARY:{booking['restaurant']} ({booking['party_size']} guests)",
        f"LOCATION:{booking.get('address', '')}",
        f"DESCRIPTION:GoodFoods reservation {code}. Complimentary cancellation up to 2 hours before.",
        "END:VEVENT", "END:VCALENDAR", ""])


# ------------------------------------------------------------------ dispatcher
def render_turn(turn: dict, *, interactive: bool, key: str) -> None:
    latest: dict[str, dict] = {}
    for run in turn["tools"]:
        if run["ok"] and run.get("structured"):
            latest[run["name"]] = run["structured"]
    if "search_restaurants" in latest:
        recommendations(latest["search_restaurants"], interactive, key)
    if "check_availability" in latest and "hold_table" not in latest:
        availability(latest["check_availability"], interactive, key)
    if "hold_table" in latest:
        data = latest["hold_table"]
        if data.get("status") == "held" and "confirm_reservation" not in latest:
            hold_card(data, interactive, key)
        elif data.get("status") == "unavailable":
            alternatives(data, interactive, key)
    if "confirm_reservation" in latest:
        ticket(latest["confirm_reservation"], key, title="Reservation confirmed")
    if "get_reservation" in latest and not ({"modify_reservation", "cancel_reservation"} & set(latest)):
        booking_list(latest["get_reservation"], interactive, key)
    for name in ("modify_reservation", "cancel_reservation"):
        if name in latest:
            change_card(name, latest[name], interactive, key)
    if "join_waitlist" in latest:
        waitlist_card(latest["join_waitlist"])
    if "request_callback" in latest:
        callback_card(latest["request_callback"])


# ------------------------------------------------------------------ discovery
def recommendations(data: dict, interactive: bool, key: str) -> None:
    results = data.get("results") or []
    if not results:
        return
    understood = data.get("understood", {})
    for note in data.get("notes", []):
        if not note.startswith("Availability not checked"):
            st.caption(f":material/info: {note}")
    for i, r in enumerate(results):
        price_band, _, cost = r["price"].partition(" · ")
        avail = r.get("availability")
        meta = f"{esc(r['cuisine'])} · <b>★ {r['rating']}</b> · {esc(price_band)} · {esc(cost)}"
        why = [w for w in r.get("why", []) if w != r["cuisine"] and "★" not in w and not w.startswith("in ")]
        with st.container(border=True):
            left, right = st.columns([0.7, 0.3], vertical_alignment="center")
            left.html(_venue(r["name"], meta, why[:4]))
            with right:
                free = bool(avail and avail["status"] == "available")
                if free:
                    st.html(f'<div class="gf-side">{status("Available at " + avail["requested"], "ok")}</div>')
                elif avail:
                    later = avail.get("other_times") or []
                    hint = f'<span class="gf-muted">Alternative times: {esc(", ".join(later[:3]))}</span>' if later else ""
                    st.html(f'<div class="gf-side">{status("Fully booked at " + avail["requested"], "warn")}{hint}</div>')
                if interactive:
                    party, date = understood.get("party_size"), understood.get("date")
                    if avail and not free and avail.get("other_times"):
                        when, text = avail["other_times"][0], f"Reserve at {avail['other_times'][0]}"
                    else:
                        when, text = understood.get("time"), "Reserve this table" if free else "Select"
                    if not avail or free or avail.get("other_times"):
                        details = " ".join(x for x in [f"for {party}" if party else "", f"on {date}" if date else "",
                                                       f"at {when}" if when else ""] if x)
                        _button(text, f"I would like to reserve {r['name']} {details}".strip() + ".", f"{key}-book-{i}",
                                primary=free, stretch=True)


def availability(data: dict, interactive: bool, key: str) -> None:
    if not data.get("available"):
        alternatives(data, interactive, key)
        return
    r = data["restaurant"]
    with st.container(border=True):
        left, right = st.columns([0.7, 0.3], vertical_alignment="center")
        left.html(_venue(r["name"], f"{esc(data['date'])} · <b>{esc(data['time'])}</b> · {data['party_size']} guests · "
                                    f"{esc(data.get('table', 'a table'))}"))
        with right:
            st.html(f'<div class="gf-side">{status("Table available", "ok")}</div>')
            if interactive:
                _button("Hold this table", f"Please hold a table at {r['name']} for {data['party_size']} on {data['date']} "
                                           f"at {data['time']}.", f"{key}-hold", primary=True, stretch=True)


def alternatives(data: dict, interactive: bool, key: str) -> None:
    alt = data.get("alternatives") or {}
    r = data.get("restaurant") or {}
    party, when, date = data.get("party_size"), data.get("time"), data.get("date")
    reason = data.get("reason", "")
    with st.container(border=True):
        st.html(f'<div class="gf-cardhead">{status("Fully booked", "warn")}<span class="gf-strong">'
                f'{esc(r.get("name", "That slot"))}</span><span class="gf-muted">{esc(date)} · {esc(when)} · '
                f'{esc(party)} guests</span></div>'
                + ("" if reason.startswith("Fully booked") else f'<div class="gf-muted">{esc(reason)}</div>'))
        times = alt.get("same_restaurant_other_times") or []
        if times:
            st.html('<div class="gf-label">Alternative times at this restaurant</div>')
            with st.container(horizontal=True):
                for j, t in enumerate(times):
                    if interactive:
                        _button(t, f"{t} at {r.get('name')} works for me.", f"{key}-time-{j}")
                    else:
                        st.html(status(t, "neutral"))
        nearby = alt.get("nearby_at_requested_time") or []
        if nearby:
            st.html(f'<div class="gf-label">Available nearby at {esc(when)}</div>')
            for j, n in enumerate(nearby):
                sister = n.get("relation") == "same restaurant brand"
                c1, c2 = st.columns([0.7, 0.3], vertical_alignment="center")
                c1.html(_venue(n["name"], f"{esc(n['cuisine'])} · <b>★ {n['rating']}</b> · {n['distance_km']} km away",
                               ["sister restaurant"] if sister else None))
                if interactive:
                    with c2:
                        _button("Reserve here", f"I would like to reserve {n['name']} instead, for {party} on {date} at {when}.",
                                f"{key}-near-{j}", stretch=True)
        if interactive and alt.get("waitlist"):
            _button("Join waitlist", f"Please add me to the waitlist for {r.get('name')} on {date} at {when} "
                                         f"for {party}.", f"{key}-waitlist", icon=":material/hourglass_top:")


# ------------------------------------------------------------------ booking lifecycle
def hold_card(data: dict, interactive: bool, key: str) -> None:
    b = data["booking"]
    guest = data.get("returning_guest")
    with st.container(border=True):
        st.html(f'<div class="gf-cardhead">{status("Table held", "info")}<span class="gf-muted">{esc(data["hold_id"])}'
                f' · held for {data["expires_in_minutes"]} minutes · awaiting your confirmation</span></div>'
                + _kv([("Restaurant", b["restaurant"]), ("When", f"{b['date']} at {b['time']}"),
                       ("Guests", f"{b['party_size']} · {b['table']}"), ("Name", b.get("guest_name")),
                       ("Mobile", b.get("phone")), ("Occasion", b.get("occasion")),
                       ("Requests", b.get("special_requests")),
                       ("Returning guest", f"{guest.get('previous_visits', 0)} previous visits" if guest else None)]))
        if interactive:
            with st.container(horizontal=True):
                _button("Confirm reservation", "Yes, please confirm the reservation.", f"{key}-confirm", primary=True,
                        icon=":material/check:")
                _button("Amend details", "I would like to amend the details before confirming.", f"{key}-change")


def ticket(data: dict, key: str, *, title: str) -> None:
    b, code = data["booking"], data["confirmation_code"]
    note = f'<div class="gf-muted" style="margin-top:.5rem">{esc(data["whatsapp"])}</div>' if data.get("whatsapp") else ""
    st.html(f'<div class="gf-ticket"><div class="gf-ticket-main">{status(title, "ok")}'
            f'<div class="gf-name" style="margin-top:.55rem">{esc(b["restaurant"])}</div>'
            f'<div class="gf-meta"><b>{esc(b["date"])} · {esc(b["time"])}</b> · {b["party_size"]} guests · '
            f'{esc(b.get("guest_name", ""))}</div><div class="gf-meta">{esc(b.get("address", ""))}</div>{note}</div>'
            f'<div class="gf-ticket-side"><div class="gf-label">Confirmation code</div>'
            f'<div class="gf-code">{esc(code)}</div><div class="gf-muted">Please present this code on arrival</div></div></div>')
    st.download_button("Add to calendar", ics(b, code), file_name=f"{code}.ics", mime="text/calendar",
                       key=f"{key}-ics-{code}", icon=":material/event:")


def booking_list(data: dict, interactive: bool, key: str) -> None:
    kinds = {"confirmed": "ok", "held": "info", "cancelled": "bad"}
    for j, b in enumerate(data.get("bookings", [])):
        state = b.get("status", "")
        with st.container(border=True):
            c1, c2 = st.columns([0.66, 0.34], vertical_alignment="center")
            extras = [x for x in [b.get("guest_name"), b.get("occasion"), b.get("special_requests")] if x]
            c1.html(_venue(b["restaurant"], f"<b>{esc(b['date'])} · {esc(b['time'])}</b> · {b['party_size']} guests",
                           extras))
            with c2:
                st.html(f'<div class="gf-side">{status(state.capitalize(), kinds.get(state, "neutral"))}'
                        f'<span class="gf-muted">{esc(b.get("confirmation_code") or b.get("hold_id"))}</span></div>')
                if interactive and state == "confirmed":
                    with st.container(horizontal=True, horizontal_alignment="right"):
                        _button("Amend", f"I would like to amend reservation {b['confirmation_code']}.", f"{key}-chg-{j}")
                        _button("Cancel", f"Please cancel reservation {b['confirmation_code']}.", f"{key}-cxl-{j}")


def change_card(tool: str, data: dict, interactive: bool, key: str) -> None:
    state = data.get("status")
    if state == "modified":
        ticket(data, key, title="Reservation amended")
    elif state in ("cancelled", "already_cancelled"):
        b = data.get("booking", {})
        with st.container(border=True):
            st.html(f'<div class="gf-cardhead">{status("Cancelled", "bad")}<span class="gf-strong">'
                    f'{esc(b.get("restaurant"))}</span><span class="gf-muted">{esc(b.get("date"))} · {esc(b.get("time"))}'
                    f' · {esc(data.get("confirmation_code"))}</span></div>'
                    + (f'<div class="gf-muted">{esc(data["waitlist_backfill"])}</div>'
                       if data.get("waitlist_backfill") else ""))
    elif state == "preview" and tool == "modify_reservation":
        cur, new = data["current"], data["proposed"]
        with st.container(border=True):
            st.html(f'<div class="gf-cardhead">{status("Amendment preview", "info")}<span class="gf-strong">'
                    f'{esc(cur["restaurant"])}</span><span class="gf-muted">{esc(data["confirmation_code"])} · no '
                    f'changes applied yet</span></div>'
                    + _kv([("Current", f"{cur['date']} at {cur['time']} · {cur['party_size']} guests"),
                           ("Proposed", f"{new['date']} at {new['time']} · {new['party_size']} guests"),
                           ("Requests", new.get("special_requests"))]))
            if interactive:
                with st.container(horizontal=True):
                    _button("Apply amendment", "Yes, please apply the amendment.", f"{key}-apply", primary=True,
                            icon=":material/check:")
                    _button("Keep current reservation", "No, please keep my reservation as it is.", f"{key}-keep")
    elif state == "preview":
        b = data["booking"]
        with st.container(border=True):
            st.html(f'<div class="gf-cardhead">{status("Cancellation pending", "warn")}<span class="gf-muted">'
                    f'{esc(data["confirmation_code"])} · awaiting your confirmation</span></div>'
                    + _kv([("Restaurant", b["restaurant"]), ("When", f"{b['date']} at {b['time']}"),
                           ("Guests", str(b["party_size"])), ("Policy", data.get("policy"))]))
            if interactive:
                with st.container(horizontal=True):
                    _button("Confirm cancellation", "Yes, please cancel the reservation.", f"{key}-yescxl", primary=True)
                    _button("Keep reservation", "No, please keep my reservation.", f"{key}-nocxl")
    elif state == "unavailable":
        times = (data.get("alternatives") or {}).get("same_restaurant_other_times") or []
        with st.container(border=True):
            st.html(f'<div class="gf-cardhead">{status("Not available", "warn")}<span class="gf-muted">'
                    f'{esc(data.get("reason", "That change is not possible."))}</span></div>'
                    + (f'<div class="gf-muted">Alternative times: {esc(", ".join(times))}</div>' if times else ""))


def waitlist_card(data: dict) -> None:
    if data.get("status") != "waitlisted":
        return
    with st.container(border=True):
        st.html(f'<div class="gf-cardhead">{status("Waitlisted", "info")}<span class="gf-muted">'
                f'{esc(data["waitlist_id"])} · position {data["position"]} · chance: {esc(data["chance"])}</span></div>'
                + _kv([("Restaurant", data["restaurant"]), ("When", f"{data['date']} at {data['time']}"),
                       ("Guests", str(data["party_size"])), ("Next", data["how_it_works"])]))


def callback_card(data: dict) -> None:
    summary = data.get("summary", {})
    with st.container(border=True):
        st.html(f'<div class="gf-cardhead">{status("Callback requested", "info")}<span class="gf-muted">'
                f'{esc(data.get("ticket_id"))}</span></div>'
                + _kv([("Team", data.get("team")), ("When", data.get("expected_response")),
                       ("Calling", summary.get("phone")), ("About", summary.get("details"))]))


# ------------------------------------------------------------------ glass-box trace
def trace(turn: dict) -> None:
    tools, events = turn["tools"], turn["events"]
    guards = [e for e in events if e["kind"] == "guardrail"]
    title = (f"Agent trace · {turn['llm_calls']} model call{'s' * (turn['llm_calls'] != 1)} · {len(tools)} tool "
             f"call{'s' * (len(tools) != 1)} · {len(guards)} guardrail{'s' * (len(guards) != 1)} · "
             f"{turn['latency_ms'] / 1000:.1f}s")
    with st.expander(title, icon=":material/account_tree:"):
        for e in events:
            d = e["detail"]
            if e["kind"] == "llm":
                decision = d.get("decision")
                decision = "respond to the guest" if decision == "reply" else "invoke " + ", ".join(decision)
                st.html(f'<div class="gf-ev"><span class="gf-badge gf-b-llm">Model</span><span>Decision: <b>'
                        f'{esc(decision)}</b></span><span class="gf-right">{d.get("prompt_tokens")} + '
                        f'{d.get("completion_tokens")} tokens · {e["ms"] / 1000:.1f}s</span></div>')
            elif e["kind"] == "tool":
                outcome = "ok" if d.get("ok") else "error"
                st.html(f'<div class="gf-ev"><span class="gf-badge gf-b-tool">MCP tool</span><span><b>{esc(e["title"])}'
                        f'</b> · {outcome}</span><span class="gf-right">tools/call · {e["ms"]:.0f} ms</span></div>')
                st.code(json.dumps(d.get("args"), ensure_ascii=False), language="json", wrap_lines=True)
                result = d.get("result")
                if isinstance(result, dict):
                    st.json(result, expanded=False)
                else:
                    st.caption(str(result)[:500])
            elif e["kind"] == "guardrail":
                detail = json.dumps(d, ensure_ascii=False)[:360] if d else ""
                st.html(f'<div class="gf-ev"><span class="gf-badge gf-b-guard">Guardrail</span><span><b>'
                        f'{esc(e["title"])}</b></span></div>'
                        + (f'<div class="gf-muted" style="margin-left:.2rem">{esc(detail)}</div>' if detail else ""))
            else:
                st.html(f'<div class="gf-ev"><span class="gf-badge gf-b-err">Error</span><span>{esc(e["title"])}: '
                        f'{esc(d.get("error", ""))}</span></div>')
        st.caption(f"{turn['prompt_tokens']} prompt + {turn['completion_tokens']} completion tokens this turn.")
