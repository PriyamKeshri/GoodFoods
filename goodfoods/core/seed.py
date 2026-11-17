"""Builds the demo database: ~5 weeks of realistic, simulated booking history and forward book.

Demand follows day-of-week and meal-time curves (weekend dinners fill up, weekday lunches
don't), scaled by each location's popularity. Bookings are allocated with the same table
engine the live system uses, so the inventory is always physically consistent.

Deterministic demo fixtures are added on top (fixed confirmation codes, a sold-out Saturday),
so the demo script and the evals behave identically on every machine.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from goodfoods import config
from goodfoods.core.catalog import Catalog, Restaurant, get_catalog
from goodfoods.core.inventory import BusyMap, candidate_starts, duration_for, find_tables
from goodfoods.core.store import SCHEMA_VERSION, Store
from goodfoods.core.timeparse import parse_date

PAST_DAYS = 14
FUTURE_DAYS = 21

FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Karthik", "Siddharth", "Nikhil", "Rahul", "Varun",
         "Ananya", "Diya", "Isha", "Kavya", "Meera", "Priya", "Sneha", "Pooja", "Nandini", "Shreya",
         "Imran", "Farhan", "Ayesha", "Zoya", "Joseph", "Maria", "Deepak", "Lakshmi", "Suresh", "Anjali"]
LAST = ["Sharma", "Iyer", "Reddy", "Nair", "Rao", "Gowda", "Menon", "Patel", "Shetty", "Kulkarni",
        "Khan", "Das", "Mukherjee", "Pillai", "Hegde", "Joshi", "Bhat", "Kapoor", "D'Souza", "Singh"]
PARTY_SIZES = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15]
PARTY_WEIGHTS = [4, 42, 10, 24, 6, 6, 2, 3, 1.5, 1, 0.5]
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

DEMO_GUESTS = {
    "9845012345": ("Ananya Rao", 7, {"diet": "vegetarian", "likes": ["South Indian", "Modern Indian Fine Dining"],
                                     "seating": "quiet corner", "notes": "Wedding anniversary in November"}),
    "9900112233": ("Rahul Mehta", 12, {"likes": ["Gastropub", "BBQ & Grill"], "usual_party": "6-10",
                                        "notes": "Books team dinners; likes sports screening"}),
    "9731122334": ("Fatima Khan", 4, {"diet": "halal", "likes": ["Hyderabadi & Awadhi", "Lebanese & Mediterranean"],
                                       "notes": "Family with a toddler, needs a high chair"}),
}


def _demand(dow: int, minute: int) -> float:
    """Target share of tables occupied at a given weekday/minute (before popularity scaling)."""
    h = minute / 60
    weekend = dow >= 5
    dinner = [0.55, 0.55, 0.6, 0.68, 0.95, 1.0, 0.8][dow]
    lunch = 0.75 if weekend else 0.5
    breakfast = 0.8 if weekend else 0.45
    return max(0.08,
               breakfast * math.exp(-((h - 9.5) / 1.1) ** 2),
               lunch * math.exp(-((h - 13.4) / 1.0) ** 2),
               dinner * math.exp(-((h - 20.4) / 1.25) ** 2))


def _lead_fraction(days_ahead: int, dow: int, minute: int) -> float:
    """Share of eventual demand that is already booked this many days in advance."""
    if days_ahead <= 0:
        return 1.0
    base = 0.85 if days_ahead == 1 else 0.65 if days_ahead <= 3 else 0.5 if days_ahead <= 7 else 0.32 if days_ahead <= 14 else 0.18
    if dow in (4, 5) and minute >= 19 * 60:
        base *= 1.4  # weekend prime time books out early
    return min(1.0, base)


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _code(rng: random.Random, used: set[str]) -> str:
    while True:
        code = "GF-" + "".join(rng.choice(CODE_ALPHABET) for _ in range(5))
        if code not in used:
            used.add(code)
            return code


def _phone(rng: random.Random) -> str:
    return rng.choice("6789") + "".join(rng.choice("0123456789") for _ in range(9))


def _simulate(store: Store, catalog: Catalog, now: datetime, rng: random.Random) -> int:
    today = now.date()
    now_min = now.hour * 60 + now.minute
    stamp = now.isoformat()
    used_codes: set[str] = set()
    rows: list[tuple] = []
    for r in catalog.restaurants:
        n_tables = sum(1 for t in r.tables if not t.private)
        scale = 0.45 + 0.6 * r.popularity
        for offset in range(-PAST_DAYS, FUTURE_DAYS + 1):
            day = today + timedelta(days=offset)
            busy: BusyMap = {}
            noise = rng.uniform(0.85, 1.12)
            for start in candidate_starts(r):
                past = offset < 0 or (offset == 0 and start < now_min)
                lead = 1.0 if past else _lead_fraction(offset, day.weekday(), start)
                lam = _demand(day.weekday(), start) * scale * noise * n_tables * 15 / 110 * lead
                for _ in range(_poisson(rng, lam)):
                    party = rng.choices(PARTY_SIZES, PARTY_WEIGHTS)[0]
                    tables = find_tables(r, busy, start, party)
                    if not tables:
                        continue
                    end = start + duration_for(party)
                    status, channel = _status_channel(rng, past, offset)
                    if status != "cancelled":
                        for tid in tables:
                            busy.setdefault(tid, []).append((start, end))
                    code = None if channel == "walk_in" else _code(rng, used_codes)
                    created = (datetime.combine(day, datetime.min.time(), tzinfo=config.TIMEZONE)
                               - timedelta(days=rng.randint(0, 6), minutes=rng.randint(0, 600))).isoformat()
                    rows.append((code, r.id, json.dumps(tables), party, day.isoformat(), start, end, status,
                                 f"{rng.choice(FIRST)} {rng.choice(LAST)}", _phone(rng), channel, "seed",
                                 created, stamp))
    with store.tx() as conn:
        conn.executemany(
            """INSERT INTO reservations (code, restaurant_id, table_ids, party_size, date, start_min, end_min,
               status, guest_name, phone, channel, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    return len(rows)


def _status_channel(rng: random.Random, past: bool, offset: int) -> tuple[str, str]:
    if past:
        pilot = offset >= -7  # the (simulated) AI-concierge pilot started a week ago
        channels = ["walk_in", "phone", "aggregator", "website", "ai_concierge"]
        weights = [24, 30, 28, 12, 6 if pilot else 0]
        channel = rng.choices(channels, weights)[0]
        no_show = {"walk_in": 0.0, "phone": 0.09, "aggregator": 0.10, "website": 0.07, "ai_concierge": 0.07}[channel]
        roll = rng.random()
        status = "cancelled" if roll < 0.05 and channel != "walk_in" else "no_show" if roll < 0.05 + no_show else "completed"
        return status, channel
    channel = rng.choices(["phone", "aggregator", "website", "ai_concierge"], [36, 32, 22, 10])[0]
    return ("cancelled" if rng.random() < 0.04 else "confirmed"), channel


def _force_booking(store: Store, r: Restaurant, day: date, start: int, party: int, **fields) -> None:
    """Insert a fixture booking, evicting simulated bookings from one suitable table if needed."""
    end = start + duration_for(party)
    candidates = sorted((t for t in r.tables if t.size >= party and not t.private), key=lambda t: t.size)
    if not candidates:
        candidates = [t for t in r.tables if t.size >= party]
    table = candidates[0]
    with store.tx() as conn:
        rows = conn.execute(
            "SELECT id, table_ids, start_min, end_min FROM reservations WHERE restaurant_id=? AND date=? AND source='seed'",
            (r.id, day.isoformat())).fetchall()
        for row in rows:
            if table.id in json.loads(row["table_ids"]) and not (row["end_min"] <= start or row["start_min"] >= end):
                conn.execute("DELETE FROM reservations WHERE id=?", (row["id"],))
        stamp = config.now().isoformat()
        conn.execute(
            """INSERT INTO reservations (code, restaurant_id, table_ids, party_size, date, start_min, end_min, status,
               guest_name, phone, occasion, special_requests, channel, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,'confirmed',?,?,?,?,?, 'seed', ?, ?)""",
            (fields["code"], r.id, json.dumps([table.id]), party, day.isoformat(), start, end, fields["guest_name"],
             fields["phone"], fields.get("occasion"), fields.get("special_requests"), fields.get("channel", "phone"),
             stamp, stamp),
        )


def _fixtures(store: Store, catalog: Catalog, now: datetime) -> dict:
    today = now.date()
    stamp = now.isoformat()
    with store.tx() as conn:
        for phone, (name, visits, prefs) in DEMO_GUESTS.items():
            conn.execute("INSERT OR REPLACE INTO guests VALUES (?,?,?,?,?,?)",
                         (phone, name, visits, json.dumps(prefs), "2025-02-11T19:30:00+05:30", stamp))

    # Fixed bookings referenced by the demo script and the evals.
    _force_booking(store, catalog.by_id["saffron-terrace-lavelle-road"], today + timedelta(days=3), 20 * 60, 2,
                   code="GF-ANR42", guest_name="Ananya Rao", phone="9845012345", occasion="anniversary",
                   special_requests="Window table if possible", channel="website")
    _force_booking(store, catalog.by_id["the-brew-ledger-koramangala"], parse_date("friday", today), 21 * 60, 8,
                   code="GF-RHL77", guest_name="Rahul Mehta", phone="9900112233", occasion="friends",
                   channel="phone")
    _force_booking(store, catalog.by_id["nawabs-biryani-house-frazer-town"], today + timedelta(days=1), 13 * 60, 5,
                   code="GF-FTK19", guest_name="Fatima Khan", phone="9731122334", occasion="family",
                   special_requests="High chair for a toddler", channel="ai_concierge")

    # A deterministic sell-out: Trattoria Verde Indiranagar is full this Saturday evening
    # (only a couple of 2-tops free up at 9 PM) -> exercises the "never just say no" flow.
    saturday = parse_date("saturday", today)
    r = catalog.by_id["trattoria-verde-indiranagar"]
    with store.tx() as conn:
        conn.execute("DELETE FROM reservations WHERE restaurant_id=? AND date=? AND end_min>? AND source='seed'",
                     (r.id, saturday.isoformat(), 17 * 60))
        rows = []
        twos_left_open = 0
        rng = random.Random(99)
        for t in r.tables:
            blocks = [(19 * 60, 21 * 60), (21 * 60, 23 * 60)]
            if t.size == 2 and twos_left_open < 2:
                blocks = blocks[:1]
                twos_left_open += 1
            for s, e in blocks:
                rows.append((r.id, json.dumps([t.id]), min(t.size, 12), saturday.isoformat(), s, e,
                             f"{rng.choice(FIRST)} {rng.choice(LAST)}", _phone(rng), stamp, stamp))
        conn.executemany(
            """INSERT INTO reservations (restaurant_id, table_ids, party_size, date, start_min, end_min, status,
               guest_name, phone, channel, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,'confirmed',?,?,'aggregator','seed',?,?)""", rows)
    return {"sold_out_restaurant": r.id, "sold_out_date": saturday.isoformat()}


def ensure_database(path: Path | None = None, *, force: bool = False, catalog: Catalog | None = None) -> Path:
    """Create and seed the database if it doesn't exist (or is from an older schema)."""
    path = Path(path or config.DB_PATH)
    if path.exists() and not force:
        store = Store(path)
        try:
            if store.get_meta("schema_version") == str(SCHEMA_VERSION) and store.get_meta("seeded_at"):
                return path
        finally:
            store.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)

    catalog = catalog or get_catalog()
    now = config.now()
    store = Store(path)
    try:
        rng = random.Random(config.SEED)
        count = _simulate(store, catalog, now, rng)
        fixtures = _fixtures(store, catalog, now)
        with store.tx() as conn:
            for key, value in {"schema_version": str(SCHEMA_VERSION), "seeded_at": now.isoformat(),
                               "seeded_on": now.date().isoformat(), "simulated_rows": str(count),
                               **fixtures}.items():
                conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
    finally:
        store.close()
    return path


if __name__ == "__main__":
    import sys
    import time

    t0 = time.time()
    p = ensure_database(force="--force" in sys.argv)
    s = Store(p)
    n = s.one("SELECT COUNT(*) AS n FROM reservations")["n"]
    print(f"database ready at {p} ({n:,} reservations, {time.time() - t0:.1f}s)")
