"""ReservationService: the business logic behind every MCP tool.

Design rules
------------
* Every state change is two-phase: ``hold -> confirm`` for new bookings, ``preview -> confirm``
  for modifications and cancellations. The guest always sees a summary before anything is final.
* "Full" is never a dead end: unavailable requests come back with ranked alternatives (other
  times at the same venue, sister locations nearby, the waitlist).
* Results are compact, LLM-readable dicts with a ``next_step`` instruction where it matters.
* Invalid input raises :class:`DomainError` with a hint telling the agent how to recover.
"""

from __future__ import annotations

import json
import random
import re
import string
from collections.abc import Callable
from datetime import date, datetime, timedelta

from goodfoods import config
from goodfoods.core import inventory as inv
from goodfoods.core import recommender as rec
from goodfoods.core.catalog import Catalog, Restaurant, get_catalog, haversine_km
from goodfoods.core.errors import DomainError
from goodfoods.core.store import Store
from goodfoods.core.timeparse import fmt_date, fmt_time, parse_date, parse_time

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PLACEHOLDER_PHONES = {"9876543210", "9123456789", "9000000000", "1234567890", "9999999990", "9012345678"}
PLACEHOLDER_NAMES = {"guest", "user", "customer", "unknown", "name", "john doe", "jane doe", "test", "na", "n/a",
                     "sir", "madam", "none", "null", "your name", "guest name", "anonymous", "tbd"}
CALLBACK_REASONS = {
    "large_event": "Events team", "special_request": "Guest relations", "complaint": "Guest relations",
    "accessibility": "Guest relations", "other": "Guest relations",
}
CANCELLATION_POLICY = ("Free cancellation up to 2 hours before the booking. Tables are held for 15 minutes "
                       "after the booking time.")


# --------------------------------------------------------------------------------------
# Input validation helpers (each raises DomainError with an agent-facing hint)
# --------------------------------------------------------------------------------------
def normalize_phone(raw: str | int | None) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not digits:
        raise DomainError("No mobile number given.", hint="Ask the guest for their 10-digit mobile number.")
    if len(digits) != 10 or digits[0] not in "6789":
        raise DomainError(f"'{raw}' isn't a valid Indian mobile number.",
                          hint="Ask the guest for a 10-digit mobile number (starting with 6-9).")
    if digits in PLACEHOLDER_PHONES or len(set(digits)) == 1:
        raise DomainError("That looks like a placeholder phone number.",
                          hint="Never invent contact details. Ask the guest for their real mobile number.")
    return digits


def display_phone(digits: str) -> str:
    return f"+91 {digits[:5]} {digits[5:]}"


def masked_phone(digits: str) -> str:
    return f"+91 ••••• •{digits[-4:]}"


def validate_name(raw: str | None) -> str:
    name = " ".join(str(raw or "").split())
    if len(name) < 2 or name.lower() in PLACEHOLDER_NAMES or not re.search(r"[^\W\d_]", name):
        raise DomainError("A real guest name is needed for the booking.",
                          hint="Ask the guest what name the booking should be under. Never invent names.")
    if len(name) > 60:
        raise DomainError("That name is too long.", hint="Ask for a shorter name for the booking.")
    return name.title() if name.islower() else name


def validate_party(party_size: int | str | None, r: Restaurant | None = None) -> int:
    try:
        n = int(str(party_size).strip())
    except (TypeError, ValueError) as exc:
        raise DomainError(f"'{party_size}' isn't a valid party size.", hint="Ask how many guests (a number).") from exc
    if n < 1:
        raise DomainError("Party size must be at least 1.", hint="Ask how many guests are coming.")
    if n > config.MAX_ONLINE_PARTY:
        raise DomainError(f"Groups of more than {config.MAX_ONLINE_PARTY} are handled by our events team.",
                          hint="Offer to arrange a call back: request_callback with reason='large_event'.")
    if r and n > r.max_party:
        raise DomainError(f"{r.name} can seat at most {r.max_party} guests in one booking.",
                          hint="Suggest a venue with a private dining room (search_restaurants with "
                               "features=['private_dining']) or request_callback(reason='large_event').")
    return n


class ReservationService:
    def __init__(self, store: Store, catalog: Catalog | None = None,
                 clock: Callable[[], datetime] = config.now) -> None:
        self.store = store
        self.catalog = catalog or get_catalog()
        self.clock = clock

    # ==================================================================================
    # Shared helpers
    # ==================================================================================
    def _when(self, date_text: str, time_text: str) -> tuple[date, int]:
        now = self.clock()
        day = parse_date(date_text, now.date())
        start = parse_time(time_text)
        if day < now.date():
            raise DomainError(f"{fmt_date(day)} is in the past.", hint="Ask the guest for a future date.")
        if day > now.date() + timedelta(days=config.BOOKING_WINDOW_DAYS):
            raise DomainError(f"We take bookings up to {config.BOOKING_WINDOW_DAYS} days ahead.",
                              hint="Ask for an earlier date.")
        if day == now.date() and start < now.hour * 60 + now.minute + 15:
            raise DomainError(f"{fmt_time(start)} today has already passed (it is {fmt_time(now.hour * 60 + now.minute)}).",
                              hint="Suggest a later time today or another day.")
        return day, start

    def _earliest_today(self, day: date) -> int | None:
        now = self.clock()
        return now.hour * 60 + now.minute + 30 if day == now.date() else None

    def _hours_problem(self, r: Restaurant, start: int) -> str | None:
        if inv.within_hours(r, start):
            return None
        windows = ", ".join(f"{fmt_time(a)}–{fmt_time(b)}" for a, b in inv.seating_windows(r))
        return f"{r.name} seats guests between {windows} (kitchen hours {r.hours_label})."

    @staticmethod
    def _table_label(r: Restaurant, tables: list[str]) -> str:
        if tables == ["PDR"]:
            return f"the private dining room (up to {r.private_dining_capacity})"
        sizes = [next(t.size for t in r.tables if t.id == tid) for tid in tables]
        return f"two joined tables ({' + '.join(map(str, sizes))} seats)" if len(sizes) > 1 else f"a {sizes[0]}-seat table"

    def _log_demand(self, kind: str, outcome: str, *, restaurant_id=None, area=None, cuisine=None, day=None,
                    start=None, party=None, session_id=None) -> None:
        with self.store.tx() as conn:
            conn.execute(
                """INSERT INTO demand_log (ts, kind, restaurant_id, area, cuisine, date, start_min, party_size,
                   outcome, session_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (self.clock().isoformat(), kind, restaurant_id, area, cuisine, day.isoformat() if day else None,
                 start, party, outcome, session_id))

    def _notify(self, conn, phone: str, kind: str, message: str) -> None:
        conn.execute("INSERT INTO notifications (phone, channel, kind, message, created_at) VALUES (?,?,?,?,?)",
                     (phone, "whatsapp", kind, message, self.clock().isoformat()))

    def _unique(self, conn, prefix: str, column: str, table: str, alphabet: str, length: int) -> str:
        rng = random.SystemRandom()
        while True:
            value = prefix + "".join(rng.choice(alphabet) for _ in range(length))
            if not conn.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (value,)).fetchone():
                return value

    def _summary(self, row) -> dict:
        r = self.catalog.by_id[row["restaurant_id"]]
        day = date.fromisoformat(row["date"])
        out = {
            "restaurant": r.name,
            "restaurant_id": r.id,
            "address": r.address,
            "date": fmt_date(day),
            "time": fmt_time(row["start_min"]),
            "party_size": row["party_size"],
            "guest_name": row["guest_name"],
            "table": self._table_label(r, json.loads(row["table_ids"])),
        }
        if row["phone"]:
            out["phone"] = display_phone(row["phone"])
        if row["occasion"]:
            out["occasion"] = row["occasion"]
        if row["special_requests"]:
            out["special_requests"] = row["special_requests"]
        return out

    def _alternatives(self, r: Restaurant, day: date, start: int, party: int, busy: inv.BusyMap) -> dict:
        times = inv.nearby_times(r, busy, start, party, earliest=self._earliest_today(day))
        nearby = []
        others = sorted((o for o in self.catalog.restaurants if o.id != r.id),
                        key=lambda o: (o.brand != r.brand, o.cuisine != r.cuisine,
                                       haversine_km(r.lat, r.lng, o.lat, o.lng)))
        for o in others:
            dist = haversine_km(r.lat, r.lng, o.lat, o.lng)
            if dist > 7 or not inv.within_hours(o, start) or party > o.max_party:
                continue
            if inv.find_tables(o, inv.busy_map(self.store, o.id, day, self.clock()), start, party):
                nearby.append({**o.card(), "distance_km": round(dist, 1),
                               "relation": "same restaurant brand" if o.brand == r.brand
                               else "same cuisine" if o.cuisine == r.cuisine else "nearby"})
            if len(nearby) == 3:
                break
        return {
            "same_restaurant_other_times": [fmt_time(t) for t in times],
            "nearby_at_requested_time": nearby,
            "waitlist": f"available: join_waitlist for {r.name} at {fmt_time(start)}",
        }

    # ==================================================================================
    # Discovery
    # ==================================================================================
    def search_restaurants(self, *, cuisine: str | None = None, area: str | None = None, date: str | None = None,
                           time: str | None = None, party_size: int | None = None, budget_for_two: int | None = None,
                           dietary=None, features=None, occasion: str | None = None, query: str | None = None,
                           limit: int = 3, session_id: str | None = None) -> dict:
        now = self.clock()
        c = rec.build_criteria(self.catalog, area=area, cuisine=cuisine, dietary=dietary, features=features,
                               occasion=occasion, budget_for_two=budget_for_two, query=query)
        if date:
            c.day = parse_date(date, now.date())
            if c.day < now.date():
                raise DomainError(f"{fmt_date(c.day)} is in the past.", hint="Ask the guest for a future date.")
        if time:
            c.start = parse_time(time)
        if party_size:
            c.party_size = validate_party(party_size)
        if c.day == now.date() and c.start is not None and c.start < now.hour * 60 + now.minute + 15:
            raise DomainError(f"{fmt_time(c.start)} today has already passed (it is {fmt_time(now.hour * 60 + now.minute)}).",
                              hint="Suggest a later time today or another day.")

        matches = rec.filter_with_relaxation(self.catalog, c)
        check = c.day is not None and c.start is not None and c.party_size is not None
        scored = []
        for r in matches:
            availability = None
            if check:
                availability = self._availability_brief(r, c.day, c.start, c.party_size)
            scored.append((rec.score(r, c, self.catalog, availability=availability), r, availability))
        limit = max(1, min(int(limit or 3), 5))

        # "Never just say no": if the best matches are full at that time, widen the net to similar
        # venues nearby that do have a table (sister outlets of the same brand come first).
        if check and c.area and sum(a["status"] == "available" for _, _, a in scored) < min(limit, 2):
            seen = {r.id for _, r, _ in scored}
            added = 0
            for r in rec.candidates(self.catalog, c, radius=9.0):
                if r.id in seen:
                    continue
                availability = self._availability_brief(r, c.day, c.start, c.party_size)
                if availability["status"] == "available":
                    scored.append((rec.score(r, c, self.catalog, availability=availability), r, availability))
                    added += 1
            if added:
                c.notes.append("The closest matches are full at that time, so similar places nearby with free "
                               "tables were added.")
        scored.sort(key=lambda t: -t[0])

        results = []
        for _, r, availability in scored[:limit]:
            item = {**r.card(), "why": rec.reasons(r, c, self.catalog, availability=availability),
                    "signature_dish": r.signature_dishes[0]}
            if availability:
                item["availability"] = {k: v for k, v in availability.items() if not k.startswith("_")}
            results.append(item)

        understood = {k: v for k, v in {
            "cuisine": c.cuisine_text if c.cuisine_terms else None,
            "area": c.area.name if c.area else None,
            "date": fmt_date(c.day) if c.day else None,
            "time": fmt_time(c.start) if c.start is not None else None,
            "party_size": c.party_size, "dietary": c.dietary or None, "features": c.features or None,
            "occasion": c.occasion, "budget_for_two": c.budget_for_two,
        }.items() if v}
        if not check:
            c.notes.append("Availability not checked yet: needs date, time and party size.")
        outcome = ("no_match" if not results else
                   "available" if any(i.get("availability", {}).get("status") == "available" for i in results)
                   else "full" if check else "partial")
        self._log_demand("search", outcome, area=c.area.name if c.area else area, cuisine=cuisine, day=c.day,
                         start=c.start, party=c.party_size, session_id=session_id)
        return {"understood": understood, "notes": c.notes, "results": results, "total_matches": len(scored)}

    def _availability_brief(self, r: Restaurant, day: date, start: int, party: int) -> dict:
        if party > r.max_party:
            return {"status": "too_large", "requested": fmt_time(start), "other_times": []}
        busy = inv.busy_map(self.store, r.id, day, self.clock())
        earliest = self._earliest_today(day)
        ok = inv.within_hours(r, start) and (earliest is None or start >= earliest - 15) \
            and inv.find_tables(r, busy, start, party)
        brief = {"requested": fmt_time(start), "status": "available" if ok else "full",
                 "_occupancy": round(inv.occupancy(r, busy, start), 2)}
        if not ok:
            brief["other_times"] = [fmt_time(t) for t in inv.nearby_times(r, busy, start, party, earliest=earliest)]
        return brief

    def get_restaurant_details(self, restaurant: str) -> dict:
        r = self.catalog.resolve_restaurant(restaurant)
        return r.details()

    def check_availability(self, restaurant: str, date: str, time: str, party_size: int,
                           session_id: str | None = None) -> dict:
        r = self.catalog.resolve_restaurant(restaurant)
        day, start = self._when(date, time)
        party = validate_party(party_size, r)
        busy = inv.busy_map(self.store, r.id, day, self.clock())
        base = {"restaurant": r.card(), "date": fmt_date(day), "time": fmt_time(start), "party_size": party}
        problem = self._hours_problem(r, start)
        tables = None if problem else inv.find_tables(r, busy, start, party)
        self._log_demand("availability", "available" if tables else "full", restaurant_id=r.id, area=r.area,
                         cuisine=r.cuisine, day=day, start=start, party=party, session_id=session_id)
        if tables:
            return {**base, "available": True, "table": self._table_label(r, tables),
                    "next_step": "A table is free. To book it, ask the guest for their name and 10-digit mobile number, "
                                 "then call hold_table."}
        return {**base, "available": False,
                "reason": problem or f"Fully booked at {fmt_time(start)} for {party}.",
                "alternatives": self._alternatives(r, day, start, party, busy),
                "next_step": "Tell the guest it's full and offer the closest alternatives (or the waitlist)."}

    # ==================================================================================
    # Booking: hold -> confirm
    # ==================================================================================
    def hold_table(self, restaurant: str, date: str, time: str, party_size: int, guest_name: str, phone: str,
                   occasion: str | None = None, special_requests: str | None = None,
                   session_id: str | None = None) -> dict:
        r = self.catalog.resolve_restaurant(restaurant)
        day, start = self._when(date, time)
        party = validate_party(party_size, r)
        name = validate_name(guest_name)
        digits = normalize_phone(phone)
        problem = self._hours_problem(r, start)
        now = self.clock()

        with self.store.tx() as conn:
            conn.execute("UPDATE reservations SET status='expired', updated_at=? WHERE status='held' AND hold_expires_at<?",
                         (now.isoformat(), now.isoformat()))
            existing = conn.execute(
                """SELECT * FROM reservations WHERE status='held' AND phone=? AND restaurant_id=? AND date=?
                   AND start_min=? AND party_size=?""", (digits, r.id, day.isoformat(), start, party)).fetchone()
            if existing:  # idempotent: the same hold requested twice returns the same hold
                return self._hold_payload(r, existing)
            busy = inv.busy_map(self.store, r.id, day, now)
            tables = None if problem else inv.find_tables(r, busy, start, party)
            if not tables:
                result = {"status": "unavailable", "restaurant": r.card(), "date": fmt_date(day),
                          "time": fmt_time(start), "party_size": party,
                          "reason": problem or f"Fully booked at {fmt_time(start)} for {party}.",
                          "alternatives": self._alternatives(r, day, start, party, busy),
                          "next_step": "No hold was created. Offer the alternatives or the waitlist."}
            else:
                hold_id = self._unique(conn, "HOLD-", "hold_id", "reservations", string.digits, 4)
                conn.execute(
                    """INSERT INTO reservations (hold_id, restaurant_id, table_ids, party_size, date, start_min, end_min,
                       status, guest_name, phone, occasion, special_requests, channel, source, session_id,
                       requested_restaurant_id, hold_expires_at, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,'held',?,?,?,?,'ai_concierge','live',?,?,?,?,?)""",
                    (hold_id, r.id, json.dumps(tables), party, day.isoformat(), start,
                     start + inv.duration_for(party), name, digits, occasion, special_requests, session_id,
                     self._redirected_from(conn, session_id, r.id),
                     (now + timedelta(minutes=config.HOLD_MINUTES)).isoformat(), now.isoformat(), now.isoformat()))
                row = conn.execute("SELECT * FROM reservations WHERE hold_id=?", (hold_id,)).fetchone()
                result = self._hold_payload(r, row)
        if result["status"] == "unavailable":
            self._log_demand("availability", "full", restaurant_id=r.id, area=r.area, cuisine=r.cuisine, day=day,
                             start=start, party=party, session_id=session_id)
        return result

    def _redirected_from(self, conn, session_id: str | None, restaurant_id: str) -> str | None:
        """If this session was just told another venue was full, remember it (recovered-demand analytics)."""
        if not session_id:
            return None
        row = conn.execute(
            """SELECT restaurant_id FROM demand_log WHERE session_id=? AND outcome='full' AND restaurant_id IS NOT NULL
               AND restaurant_id<>? ORDER BY id DESC LIMIT 1""", (session_id, restaurant_id)).fetchone()
        return row["restaurant_id"] if row else None

    def _hold_payload(self, r: Restaurant, row) -> dict:
        guest = self.store.one("SELECT * FROM guests WHERE phone=?", (row["phone"],))
        payload = {
            "status": "held",
            "hold_id": row["hold_id"],
            "expires_in_minutes": config.HOLD_MINUTES,
            "booking": self._summary(row),
            "next_step": ("NOT booked yet. Read this summary to the guest and ask them to confirm. "
                          "Only after they say yes in their next message, call confirm_reservation with this hold_id."),
        }
        if guest and guest["visits"] > 0:
            prefs = json.loads(guest["preferences"] or "{}")
            payload["returning_guest"] = {"name": guest["name"], "previous_visits": guest["visits"],
                                          **{k: v for k, v in prefs.items() if k in ("diet", "seating", "notes")}}
        return payload

    def confirm_reservation(self, hold_id: str) -> dict:
        hold_id = str(hold_id or "").strip().upper()
        if re.fullmatch(r"\d{4}", hold_id):
            hold_id = f"HOLD-{hold_id}"
        now = self.clock()
        with self.store.tx() as conn:
            row = conn.execute("SELECT * FROM reservations WHERE hold_id=?", (hold_id,)).fetchone()
            if not row:
                raise DomainError(f"No hold found with id '{hold_id}'.",
                                  hint="Call hold_table first and use the exact hold_id it returns (e.g. HOLD-1234).")
            if row["status"] == "confirmed":  # idempotent
                return {"status": "confirmed", "confirmation_code": row["code"], "booking": self._summary(row),
                        "note": "This hold was already confirmed earlier."}
            if row["status"] not in ("held", "expired"):
                raise DomainError(f"This hold is {row['status']} and can't be confirmed.",
                                  hint="Check availability again and create a new hold.")
            if row["status"] == "expired" or row["hold_expires_at"] < now.isoformat():
                r = self.catalog.by_id[row["restaurant_id"]]
                busy = inv.busy_map(self.store, r.id, date.fromisoformat(row["date"]), now,
                                    exclude_reservation_id=row["id"])
                if not inv.find_tables(r, busy, row["start_min"], row["party_size"]):
                    conn.execute("UPDATE reservations SET status='expired', updated_at=? WHERE id=?",
                                 (now.isoformat(), row["id"]))
                    raise DomainError("The hold expired and the table has since been taken.",
                                      hint="Apologise, then call check_availability again for alternatives.")
            code = self._unique(conn, "GF-", "code", "reservations", CODE_ALPHABET, 5)
            conn.execute("UPDATE reservations SET status='confirmed', code=?, hold_expires_at=NULL, updated_at=? WHERE id=?",
                         (code, now.isoformat(), row["id"]))
            conn.execute(
                """INSERT INTO guests (phone, name, visits, preferences, first_seen, last_seen) VALUES (?,?,0,'{}',?,?)
                   ON CONFLICT(phone) DO UPDATE SET name=excluded.name, last_seen=excluded.last_seen""",
                (row["phone"], row["guest_name"], now.isoformat(), now.isoformat()))
            row = conn.execute("SELECT * FROM reservations WHERE id=?", (row["id"],)).fetchone()
            summary = self._summary(row)
            self._notify(conn, row["phone"], "confirmation",
                         f"Confirmed: {summary['restaurant']}, {summary['date']} at {summary['time']} for "
                         f"{summary['party_size']}. Code {code}. Reply C to cancel.")
        return {
            "status": "confirmed",
            "confirmation_code": code,
            "booking": summary,
            "whatsapp": f"Confirmation sent to {masked_phone(row['phone'])} (simulated)",
            "policy": CANCELLATION_POLICY,
            "next_step": "Tell the guest the booking is confirmed and give them this confirmation code.",
        }

    # ==================================================================================
    # Manage existing bookings
    # ==================================================================================
    def _by_code(self, conn, code: str):
        code = str(code or "").strip().upper().replace(" ", "")
        if code and not code.startswith("GF-") and len(code) == 5:
            code = f"GF-{code}"
        row = conn.execute("SELECT * FROM reservations WHERE code=?", (code,)).fetchone()
        if not row:
            raise DomainError(f"No booking found with code '{code}'.",
                              hint="Check the code with the guest (format GF-XXXXX), or look it up by their mobile number.")
        return row

    def get_reservation(self, confirmation_code: str | None = None, phone: str | None = None) -> dict:
        today = self.clock().date().isoformat()
        if confirmation_code:
            with self.store.tx() as conn:
                row = self._by_code(conn, confirmation_code)
            return {"bookings": [{"confirmation_code": row["code"], "status": row["status"], **self._summary(row)}]}
        if phone:
            digits = normalize_phone(phone)
            rows = self.store.query(
                """SELECT * FROM reservations WHERE phone=? AND date>=? AND status IN ('confirmed','held')
                   ORDER BY date, start_min LIMIT 5""", (digits, today))
            if not rows:
                raise DomainError(f"No upcoming bookings for {masked_phone(digits)}.",
                                  hint="Double-check the number with the guest or ask for their confirmation code.")
            return {"bookings": [
                {"confirmation_code": r["code"], "status": r["status"], **self._summary(r)} if r["code"] else
                {"hold_id": r["hold_id"], "status": "held", **self._summary(r),
                 "note": "On hold only, NOT a booking yet. If the guest wants it, call confirm_reservation "
                         "with this hold_id."}
                for r in rows]}
        raise DomainError("Need a confirmation code or the mobile number used for the booking.",
                          hint="Ask the guest for their confirmation code (GF-XXXXX) or mobile number.")

    def modify_reservation(self, confirmation_code: str, date: str | None = None, time: str | None = None,
                           party_size: int | None = None, special_requests: str | None = None,
                           confirm: bool = False) -> dict:
        now = self.clock()
        with self.store.tx() as conn:
            row = self._by_code(conn, confirmation_code)
            if row["status"] != "confirmed":
                raise DomainError(f"Booking {row['code']} is {row['status']} and can't be changed.",
                                  hint="Offer to make a new booking instead.")
            r = self.catalog.by_id[row["restaurant_id"]]
            day = parse_date(date, now.date()) if date else datetime.strptime(row["date"], "%Y-%m-%d").date()
            start = parse_time(time) if time else row["start_min"]
            if date or time:
                day, start = self._when(day.isoformat(), f"{start // 60:02d}:{start % 60:02d}")
            party = validate_party(party_size, r) if party_size else row["party_size"]
            if (day.isoformat(), start, party) == (row["date"], row["start_min"], row["party_size"]) \
                    and not special_requests:
                raise DomainError("Nothing to change: that's the current booking.",
                                  hint="Ask the guest what they would like to change (date, time, party size or requests).")
            busy = inv.busy_map(self.store, r.id, day, now, exclude_reservation_id=row["id"])
            problem = self._hours_problem(r, start)
            tables = None if problem else inv.find_tables(r, busy, start, party)
            if not tables:
                return {"status": "unavailable", "current": self._summary(row),
                        "reason": problem or f"No table for {party} at {fmt_time(start)} on {fmt_date(day)}.",
                        "alternatives": self._alternatives(r, day, start, party, busy),
                        "next_step": "The booking is unchanged. Offer the alternatives."}
            proposed = {**self._summary(row), "date": fmt_date(day), "time": fmt_time(start), "party_size": party,
                        "table": self._table_label(r, tables)}
            if special_requests:
                proposed["special_requests"] = special_requests
            if not confirm:
                return {"status": "preview", "confirmation_code": row["code"], "current": self._summary(row),
                        "proposed": proposed,
                        "next_step": "Nothing changed yet. Ask the guest to confirm; if they agree, call "
                                     "modify_reservation again with the same values and confirm=true."}
            conn.execute(
                """UPDATE reservations SET date=?, start_min=?, end_min=?, party_size=?, table_ids=?,
                   special_requests=COALESCE(?, special_requests), updated_at=? WHERE id=?""",
                (day.isoformat(), start, start + inv.duration_for(party), party, json.dumps(tables), special_requests,
                 now.isoformat(), row["id"]))
            row = conn.execute("SELECT * FROM reservations WHERE id=?", (row["id"],)).fetchone()
            summary = self._summary(row)
            self._notify(conn, row["phone"], "modification",
                         f"Updated: {summary['restaurant']}, {summary['date']} at {summary['time']} for "
                         f"{summary['party_size']}. Code {row['code']}.")
        return {"status": "modified", "confirmation_code": row["code"], "booking": summary,
                "whatsapp": f"Update sent to {masked_phone(row['phone'])} (simulated)"}

    def cancel_reservation(self, confirmation_code: str, confirm: bool = False, reason: str | None = None) -> dict:
        now = self.clock()
        with self.store.tx() as conn:
            row = self._by_code(conn, confirmation_code)
            if row["status"] == "cancelled":
                return {"status": "already_cancelled", "confirmation_code": row["code"], "booking": self._summary(row)}
            if row["status"] != "confirmed":
                raise DomainError(f"Booking {row['code']} is {row['status']} and can't be cancelled.")
            if row["date"] < now.date().isoformat():
                raise DomainError("That booking is in the past.", hint="Nothing to cancel.")
            if not confirm:
                return {"status": "preview", "confirmation_code": row["code"], "booking": self._summary(row),
                        "policy": CANCELLATION_POLICY,
                        "next_step": "Nothing cancelled yet. Ask the guest to confirm the cancellation; if they "
                                     "agree, call cancel_reservation again with confirm=true."}
            conn.execute("UPDATE reservations SET status='cancelled', special_requests=COALESCE(special_requests, ?), "
                         "updated_at=? WHERE id=?", (f"cancel reason: {reason}" if reason else None,
                                                    now.isoformat(), row["id"]))
            summary = self._summary(row)
            self._notify(conn, row["phone"], "cancellation", f"Cancelled: {summary['restaurant']}, {summary['date']} "
                                                              f"at {summary['time']}. Code {row['code']}.")
            offered = self._offer_to_waitlist(conn, row)
        result = {"status": "cancelled", "confirmation_code": row["code"], "booking": summary}
        if offered:
            result["waitlist_backfill"] = f"The freed table was offered to {offered} waitlisted guest(s)."
        return result

    def _offer_to_waitlist(self, conn, row) -> int:
        """A cancellation is inventory: offer it to the first matching waitlisted guest."""
        match = conn.execute(
            """SELECT * FROM waitlist WHERE restaurant_id=? AND date=? AND status='waiting'
               AND ABS(start_min-?)<=60 AND party_size<=? ORDER BY created_at LIMIT 1""",
            (row["restaurant_id"], row["date"], row["start_min"], row["party_size"])).fetchone()
        if not match:
            return 0
        conn.execute("UPDATE waitlist SET status='offered', notified_at=? WHERE id=?",
                     (self.clock().isoformat(), match["id"]))
        r = self.catalog.by_id[row["restaurant_id"]]
        self._notify(conn, match["phone"], "waitlist_offer",
                     f"Good news! A table at {r.name} on {fmt_date(date.fromisoformat(row['date']))} around "
                     f"{fmt_time(match['start_min'])} just opened up. Reply YES within 15 minutes to grab it.")
        return 1

    # ==================================================================================
    # Waitlist & human hand-off
    # ==================================================================================
    def join_waitlist(self, restaurant: str, date: str, time: str, party_size: int, guest_name: str,
                      phone: str) -> dict:
        r = self.catalog.resolve_restaurant(restaurant)
        day, start = self._when(date, time)
        party = validate_party(party_size, r)
        name = validate_name(guest_name)
        digits = normalize_phone(phone)
        busy = inv.busy_map(self.store, r.id, day, self.clock())
        if not self._hours_problem(r, start) and inv.find_tables(r, busy, start, party):
            return {"status": "available_now", "restaurant": r.card(), "date": fmt_date(day), "time": fmt_time(start),
                    "next_step": "A table is actually free: offer to hold it with hold_table instead."}
        with self.store.tx() as conn:
            ahead = conn.execute(
                "SELECT COUNT(*) FROM waitlist WHERE restaurant_id=? AND date=? AND status='waiting' AND ABS(start_min-?)<=30",
                (r.id, day.isoformat(), start)).fetchone()[0]
            wl_id = self._unique(conn, "WL-", "id", "waitlist", string.digits, 4)
            conn.execute("INSERT INTO waitlist VALUES (?,?,?,?,?,?,?,'waiting',?,NULL)",
                         (wl_id, r.id, day.isoformat(), start, party, name, digits, self.clock().isoformat()))
        chance = "good" if ahead == 0 else "fair" if ahead <= 2 else "low"
        return {"status": "waitlisted", "waitlist_id": wl_id, "restaurant": r.name, "date": fmt_date(day),
                "time": fmt_time(start), "party_size": party, "position": ahead + 1, "chance": chance,
                "how_it_works": "If a table frees up we WhatsApp the guest; they get 15 minutes to accept."}

    def request_callback(self, reason: str, guest_name: str, phone: str, details: str,
                         restaurant: str | None = None, date: str | None = None,
                         party_size: int | None = None) -> dict:
        key = re.sub(r"[^a-z]+", "_", str(reason or "other").lower()).strip("_")
        if key not in CALLBACK_REASONS:
            key = "large_event" if any(w in key for w in ("event", "party", "group", "corporate", "wedding")) else "other"
        name = validate_name(guest_name)
        digits = normalize_phone(phone)
        r = self.catalog.resolve_restaurant(restaurant) if restaurant else None
        day = parse_date(date, self.clock().date()) if date else None
        with self.store.tx() as conn:
            ticket = self._unique(conn, "CB-", "id", "callbacks", string.digits, 4)
            conn.execute("INSERT INTO callbacks VALUES (?,?,?,?,?,?,?,?, 'open', ?)",
                         (ticket, key, name, digits, details, r.id if r else None,
                          day.isoformat() if day else None, party_size, self.clock().isoformat()))
        return {"status": "logged", "ticket_id": ticket, "team": CALLBACK_REASONS[key],
                "expected_response": "a call back within 2 hours (10 AM – 9 PM)",
                "summary": {"reason": key, "guest_name": name, "phone": masked_phone(digits), "details": details}}
