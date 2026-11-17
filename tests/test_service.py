import json
from datetime import timedelta

import pytest

from goodfoods import config
from goodfoods.core.errors import DomainError

GUEST = dict(guest_name="Rohan Iyer", phone="98123 45670")


def _free_slot(service, restaurant="ember-and-oak-whitefield", party=4):
    """A slot that is bookable in the seeded data (tomorrow, a weekday)."""
    for time in ("8pm", "7pm", "9pm", "6pm", "3pm"):
        if service.check_availability(restaurant, "tomorrow", time, party)["available"]:
            return time
    pytest.skip("no free slot in seeded data")


def test_search_explains_and_backfills_when_first_choice_is_full(service):
    out = service.search_restaurants(cuisine="Italian", area="Indiranagar", date="saturday", time="8pm",
                                     party_size=2, occasion="date night")
    by_id = {r["id"]: r for r in out["results"]}
    assert by_id["trattoria-verde-indiranagar"]["availability"]["status"] == "full"
    assert any(r["availability"]["status"] == "available" for r in out["results"])
    assert all(r["why"] for r in out["results"])
    assert out["understood"]["date"] == "Sat, 21 Nov 2026"


def test_search_relaxes_constraints_and_says_so(service):
    out = service.search_restaurants(cuisine="japanese", area="HSR Layout")
    assert out["results"] and all(r["cuisine"] == "Japanese" for r in out["results"])
    assert any("within" in n for n in out["notes"])
    out = service.search_restaurants(cuisine="korean", area="Koramangala")
    assert any("doesn't serve" in n for n in out["notes"])


def test_dietary_needs_are_never_relaxed(service):
    out = service.search_restaurants(dietary=["jain"], area="Jayanagar")
    ids = {r["id"] for r in out["results"]}
    assert ids and all("jain" in service.catalog.by_id[i].dietary for i in ids)


def test_sold_out_slot_returns_ranked_alternatives(service):
    out = service.check_availability("trattoria-verde-indiranagar", "saturday", "8pm", 4)
    assert out["available"] is False
    nearby = out["alternatives"]["nearby_at_requested_time"]
    assert nearby and nearby[0]["relation"] == "same restaurant brand"
    assert "waitlist" in out["alternatives"]


def test_hold_then_confirm_is_idempotent(service):
    time = _free_slot(service)
    hold = service.hold_table("ember-and-oak-whitefield", "tomorrow", time, 4, **GUEST)
    assert hold["status"] == "held" and "NOT booked" in hold["next_step"]
    first = service.confirm_reservation(hold["hold_id"])
    second = service.confirm_reservation(hold["hold_id"])
    assert first["status"] == "confirmed" and first["confirmation_code"].startswith("GF-")
    assert second["confirmation_code"] == first["confirmation_code"]
    assert service.store.one("SELECT COUNT(*) n FROM notifications WHERE kind='confirmation'")["n"] == 1


def test_never_double_books_a_table(service):
    r = "morning-ritual-cafe-indiranagar"
    holds = []
    for i in range(60):
        out = service.hold_table(r, "tomorrow", "4pm", 2, guest_name=f"Guest Number {chr(65 + i % 26)}",
                                 phone=f"98{i:08d}")
        if out["status"] != "held":
            assert out["alternatives"]
            break
        holds.append(out["hold_id"])
    rows = service.store.query("SELECT table_ids, start_min, end_min FROM reservations WHERE restaurant_id=? AND "
                               "date='2026-11-17' AND status IN ('held','confirmed')", (r,))
    intervals: dict[str, list] = {}
    for row in rows:
        for t in json.loads(row["table_ids"]):
            for s, e in intervals.get(t, []):
                assert row["end_min"] <= s or row["start_min"] >= e, f"table {t} double-booked"
            intervals.setdefault(t, []).append((row["start_min"], row["end_min"]))
    assert holds


def test_expired_hold_releases_the_table(service):
    time = _free_slot(service, "sakura-street-indiranagar", 2)
    hold = service.hold_table("sakura-street-indiranagar", "tomorrow", time, 2, **GUEST)
    later = config.now() + timedelta(minutes=config.HOLD_MINUTES + 1)
    service.clock = lambda: later
    again = service.hold_table("sakura-street-indiranagar", "tomorrow", time, 2, guest_name="Meera Nair",
                               phone="9876001234")
    assert again["status"] == "held" and again["hold_id"] != hold["hold_id"]


@pytest.mark.parametrize("field, value", [("phone", "9876543210"), ("phone", "12345"), ("guest_name", "guest"),
                                          ("guest_name", "")])
def test_invented_or_invalid_contact_details_are_rejected(service, field, value):
    with pytest.raises(DomainError) as err:
        service.hold_table("ember-and-oak-whitefield", "tomorrow", "8pm", 2, **{**GUEST, field: value})
    assert err.value.hint


def test_past_times_and_large_groups_are_rejected_with_hints(service):
    with pytest.raises(DomainError, match="already passed"):
        service.check_availability("dosa-republic-jayanagar", "today", "8am", 2)
    with pytest.raises(DomainError) as err:
        service.check_availability("tandoor-house-indiranagar", "friday", "8pm", 40)
    assert "request_callback" in err.value.hint


def test_modify_previews_before_applying(service):
    preview = service.modify_reservation("GF-ANR42", time="9pm")
    assert preview["status"] == "preview"
    assert service.store.one("SELECT start_min FROM reservations WHERE code='GF-ANR42'")["start_min"] == 20 * 60
    done = service.modify_reservation("GF-ANR42", time="9pm", confirm=True)
    assert done["status"] == "modified"
    assert service.store.one("SELECT start_min FROM reservations WHERE code='GF-ANR42'")["start_min"] == 21 * 60


def test_cancel_previews_then_cancels_and_offers_table_to_waitlist(service):
    row = service.store.one("SELECT * FROM reservations WHERE code='GF-FTK19'")
    with service.store.tx() as conn:
        conn.execute("INSERT INTO waitlist VALUES ('WL-0001',?,?,?,2,'Arjun Rao','9876001234','waiting',?,NULL)",
                     (row["restaurant_id"], row["date"], row["start_min"], config.now().isoformat()))
    assert service.cancel_reservation("GF-FTK19")["status"] == "preview"
    out = service.cancel_reservation("GF-FTK19", confirm=True)
    assert out["status"] == "cancelled" and "waitlist_backfill" in out
    assert service.store.one("SELECT status FROM waitlist WHERE id='WL-0001'")["status"] == "offered"
    assert service.cancel_reservation("GF-FTK19", confirm=True)["status"] == "already_cancelled"


def test_waitlist_only_when_actually_full(service):
    full = service.join_waitlist("trattoria-verde-indiranagar", "saturday", "8pm", 4, **GUEST)
    assert full["status"] == "waitlisted" and full["position"] == 1
    time = _free_slot(service)
    free = service.join_waitlist("ember-and-oak-whitefield", "tomorrow", time, 4, **GUEST)
    assert free["status"] == "available_now"


def test_lookup_by_code_or_phone(service):
    assert service.get_reservation(confirmation_code="gf-anr42")["bookings"][0]["guest_name"] == "Ananya Rao"
    codes = [b["confirmation_code"] for b in service.get_reservation(phone="+91 98450 12345")["bookings"]]
    assert "GF-ANR42" in codes
    with pytest.raises(DomainError):
        service.get_reservation(confirmation_code="GF-ZZZZZ")


def test_callback_routes_large_events_to_events_team(service):
    out = service.request_callback("corporate event", "Rohan Iyer", "9812345670", "Office party for 40",
                                   date="friday", party_size=40)
    assert out["team"] == "Events team" and out["ticket_id"].startswith("CB-")


def test_a_hold_is_never_presented_as_a_confirmed_booking(service):
    time = _free_slot(service)
    hold = service.hold_table("ember-and-oak-whitefield", "tomorrow", time, 4, **GUEST)
    listed = service.get_reservation(phone=GUEST["phone"])["bookings"]
    mine = next(b for b in listed if b.get("hold_id") == hold["hold_id"])
    assert "confirmation_code" not in mine and mine["status"] == "held" and "NOT a booking" in mine["note"]
