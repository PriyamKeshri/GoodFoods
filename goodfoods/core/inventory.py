"""Table-level inventory: who sits where, when.

A booking occupies one table (or two joined tables, or the private dining room) for a
party-size dependent duration. Availability is computed from live reservations plus
unexpired holds, so two guests can never be promised the same table.
"""

from __future__ import annotations

import itertools
import json
from datetime import date, datetime

from goodfoods.core.catalog import Restaurant
from goodfoods.core.store import Store

LAST_SEATING_BEFORE_CLOSE = 45  # minutes


def duration_for(party_size: int) -> int:
    """Expected dining time in minutes."""
    if party_size <= 2:
        return 90
    if party_size <= 6:
        return 120
    return 150


def last_seating(close_min: int) -> int:
    return close_min - LAST_SEATING_BEFORE_CLOSE


def within_hours(r: Restaurant, start: int) -> bool:
    return any(open_ <= start <= last_seating(close) for open_, close in r.service_windows)


def seating_windows(r: Restaurant) -> list[tuple[int, int]]:
    """(first, last) bookable start minute per service window."""
    return [(o, last_seating(c)) for o, c in r.service_windows]


BusyMap = dict[str, list[tuple[int, int]]]


def busy_map(store: Store, restaurant_id: str, day: date, now: datetime,
             exclude_reservation_id: int | None = None) -> BusyMap:
    """Occupied intervals per table for one restaurant-day (confirmed + live holds)."""
    rows = store.query(
        """SELECT id, table_ids, start_min, end_min, status, hold_expires_at FROM reservations
           WHERE restaurant_id=? AND date=? AND status IN ('confirmed','held','completed')""",
        (restaurant_id, day.isoformat()),
    )
    busy: BusyMap = {}
    now_iso = now.isoformat()
    for row in rows:
        if exclude_reservation_id is not None and row["id"] == exclude_reservation_id:
            continue
        if row["status"] == "held" and (row["hold_expires_at"] or "") < now_iso:
            continue  # expired hold: the table is free again
        for tid in json.loads(row["table_ids"]):
            busy.setdefault(tid, []).append((row["start_min"], row["end_min"]))
    return busy


def _free(busy: BusyMap, table_id: str, start: int, end: int) -> bool:
    return all(e <= start or s >= end for s, e in busy.get(table_id, ()))


def find_tables(r: Restaurant, busy: BusyMap, start: int, party_size: int) -> list[str] | None:
    """Pick the best table(s) for a party, or None if nothing fits.

    Policy: the smallest single table that fits without wasting too many seats; large parties
    may get the private dining room or two joined tables.
    """
    end = start + duration_for(party_size)
    free = [t for t in r.tables if _free(busy, t.id, start, end)]
    regular = [t for t in free if not t.private]
    private = [t for t in free if t.private and t.size >= party_size]

    if party_size >= 10 and private:
        return [private[0].id]
    waste = 2 if party_size <= 4 else 3
    singles = sorted((t for t in regular if party_size <= t.size <= party_size + waste), key=lambda t: t.size)
    if singles:
        return [singles[0].id]
    if party_size >= 5:
        pairs = [(a, b) for a, b in itertools.combinations(regular, 2)
                 if party_size <= a.size + b.size <= party_size + waste]
        if pairs:
            a, b = min(pairs, key=lambda p: (p[0].size + p[1].size, p[0].id))
            return [a.id, b.id]
    if party_size >= 6 and private:
        return [private[0].id]
    return None


def candidate_starts(r: Restaurant, step: int = 15) -> list[int]:
    starts: list[int] = []
    for first, last in seating_windows(r):
        starts.extend(range(first, last + 1, step))
    return starts


def nearby_times(r: Restaurant, busy: BusyMap, start: int, party_size: int, *, earliest: int | None = None,
                 window: int = 150, limit: int = 3) -> list[int]:
    """Closest bookable start times to ``start`` (prefers :00 / :30, never in the past)."""
    options = []
    for s in candidate_starts(r):
        if s == start or abs(s - start) > window:
            continue
        if earliest is not None and s < earliest:
            continue
        if find_tables(r, busy, s, party_size):
            options.append(s)
    options.sort(key=lambda s: (abs(s - start) + (0 if s % 30 == 0 else 10), s))
    return sorted(options[:limit])


def occupancy(r: Restaurant, busy: BusyMap, minute: int) -> float:
    """Fraction of regular seats in use at a given minute (0..1)."""
    total = sum(t.size for t in r.tables if not t.private) or 1
    used = sum(t.size for t in r.tables if not t.private
               and any(s <= minute < e for s, e in busy.get(t.id, ())))
    return used / total
