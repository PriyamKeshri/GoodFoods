"""SQLite persistence: schema + a small thread-safe connection wrapper.

The catalog (restaurants, tables) is read-only master data living in ``restaurants.json``;
SQLite holds the transactional data: reservations, holds, waitlist, guests, callbacks,
simulated outbound notifications and a demand log used for analytics.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS reservations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hold_id         TEXT UNIQUE,
    code            TEXT UNIQUE,                 -- issued on confirmation (GF-XXXXX)
    restaurant_id   TEXT NOT NULL,
    table_ids       TEXT NOT NULL,               -- JSON list
    party_size      INTEGER NOT NULL,
    date            TEXT NOT NULL,               -- YYYY-MM-DD
    start_min       INTEGER NOT NULL,            -- minutes after midnight
    end_min         INTEGER NOT NULL,
    status          TEXT NOT NULL,               -- held|confirmed|cancelled|completed|no_show|expired
    guest_name      TEXT,
    phone           TEXT,
    occasion        TEXT,
    special_requests TEXT,
    channel         TEXT NOT NULL,               -- ai_concierge|phone|website|aggregator|walk_in
    source          TEXT NOT NULL DEFAULT 'live',-- live|seed (seed = simulated history)
    session_id      TEXT,
    requested_restaurant_id TEXT,                -- set when the guest was redirected from a full venue
    hold_expires_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_res_slot  ON reservations(restaurant_id, date, status);
CREATE INDEX IF NOT EXISTS idx_res_phone ON reservations(phone);
CREATE INDEX IF NOT EXISTS idx_res_date  ON reservations(date, status);

CREATE TABLE IF NOT EXISTS waitlist (
    id            TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    date          TEXT NOT NULL,
    start_min     INTEGER NOT NULL,
    party_size    INTEGER NOT NULL,
    guest_name    TEXT NOT NULL,
    phone         TEXT NOT NULL,
    status        TEXT NOT NULL,                 -- waiting|offered|converted|expired|cancelled
    created_at    TEXT NOT NULL,
    notified_at   TEXT
);

CREATE TABLE IF NOT EXISTS guests (
    phone       TEXT PRIMARY KEY,
    name        TEXT,
    visits      INTEGER NOT NULL DEFAULT 0,
    preferences TEXT,                            -- JSON
    first_seen  TEXT,
    last_seen   TEXT
);

CREATE TABLE IF NOT EXISTS callbacks (
    id            TEXT PRIMARY KEY,
    reason        TEXT NOT NULL,
    guest_name    TEXT,
    phone         TEXT,
    details       TEXT,
    restaurant_id TEXT,
    date          TEXT,
    party_size    INTEGER,
    status        TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT,
    channel    TEXT NOT NULL,                    -- whatsapp|sms (simulated outbox)
    kind       TEXT NOT NULL,                    -- confirmation|waitlist_offer|cancellation|modification
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demand_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    kind          TEXT NOT NULL,                 -- search|availability
    restaurant_id TEXT,
    area          TEXT,
    cuisine       TEXT,
    date          TEXT,
    start_min     INTEGER,
    party_size    INTEGER,
    outcome       TEXT NOT NULL,                 -- available|full|no_match|partial
    session_id    TEXT
);
"""


class Store:
    """A single SQLite connection guarded by a re-entrant lock (WAL mode for concurrent readers)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction (BEGIN IMMEDIATE takes the write lock up front)."""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def get_meta(self, key: str) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()
