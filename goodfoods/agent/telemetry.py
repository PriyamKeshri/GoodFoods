"""Agent telemetry (SQLite): turns, tool calls, guardrail interventions and funnel events.

Feeds the Ops Dashboard. Personal data is redacted before it is written (phone numbers keep
their last two digits, names become initials), in the spirit of India's DPDP Act 2023.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from goodfoods import config

if TYPE_CHECKING:
    from goodfoods.agent.core import TurnResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session_id TEXT, turn INTEGER, model TEXT, guardrails INTEGER,
    latency_ms REAL, llm_calls INTEGER, tool_calls INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session_id TEXT, turn INTEGER, tool TEXT, ok INTEGER,
    blocked INTEGER, latency_ms REAL, status TEXT, args TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS guardrail_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session_id TEXT, turn INTEGER, kind TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS funnel (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session_id TEXT, event TEXT, restaurant_id TEXT, covers INTEGER
);
"""

_STATUS_EVENTS = {
    ("hold_table", "held"): "hold", ("hold_table", "unavailable"): "full",
    ("confirm_reservation", "confirmed"): "booked", ("modify_reservation", "modified"): "modified",
    ("cancel_reservation", "cancelled"): "cancelled", ("join_waitlist", "waitlisted"): "waitlisted",
    ("request_callback", "logged"): "handoff",
}


def redact(args: dict) -> dict:
    out = {}
    for key, value in args.items():
        if key == "phone" or (isinstance(value, str) and len(re.sub(r"\D", "", value)) >= 10
                              and re.fullmatch(r"[\d\s+()\-]+", value)):
            digits = re.sub(r"\D", "", str(value))
            out[key] = f"•••••••{digits[-2:]}" if digits else value
        elif key in ("guest_name", "name") and isinstance(value, str):
            out[key] = ".".join(w[0].upper() for w in value.split() if w) + "."
        else:
            out[key] = value
    return out


class Telemetry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or config.TELEMETRY_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=15)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def record_turn(self, session_id: str, turn: int, result: TurnResult, *, model: str, guardrails: bool) -> None:
        ts = config.now().isoformat()
        with self._lock:
            c = self.conn
            c.execute("BEGIN")
            c.execute("INSERT INTO turns (ts, session_id, turn, model, guardrails, latency_ms, llm_calls, tool_calls, "
                      "prompt_tokens, completion_tokens, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (ts, session_id, turn, model, int(guardrails), result.latency_ms, result.llm_calls,
                       len(result.tools), result.prompt_tokens, result.completion_tokens, result.error))
            for run in result.tools:
                status = (run.structured or {}).get("status")
                c.execute("INSERT INTO tool_calls (ts, session_id, turn, tool, ok, blocked, latency_ms, status, args, error) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (ts, session_id, turn, run.name, int(run.ok), int(run.blocked), run.ms, status,
                           json.dumps(redact(run.args), ensure_ascii=False), None if run.ok else run.text[:300]))
                event = _STATUS_EVENTS.get((run.name, status))
                if run.name == "check_availability" and run.ok and run.structured:
                    event = "available" if run.structured.get("available") else "full"
                if run.name == "search_restaurants" and run.ok:
                    event = "search"
                if event:
                    structured = run.structured or {}
                    booking = structured.get("booking") or {}
                    restaurant = structured.get("restaurant")
                    rid = booking.get("restaurant_id") or (restaurant.get("id") if isinstance(restaurant, dict) else None)
                    c.execute("INSERT INTO funnel (ts, session_id, event, restaurant_id, covers) VALUES (?,?,?,?,?)",
                              (ts, session_id, event, rid, booking.get("party_size") or run.args.get("party_size")))
            for ev in result.events:
                if ev.kind == "guardrail":
                    c.execute("INSERT INTO guardrail_events (ts, session_id, turn, kind, detail) VALUES (?,?,?,?,?)",
                              (ts, session_id, turn, ev.title, json.dumps(ev.detail, ensure_ascii=False, default=str)[:1000]))
            c.execute("COMMIT")
