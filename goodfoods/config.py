"""Central configuration. Everything is overridable through environment variables (or a ``.env`` file)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

try:  # optional: python-dotenv is in requirements, but the core must not depend on it
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

PACKAGE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = PACKAGE_DIR / "data" / "restaurants.json"

DATA_DIR = Path(os.getenv("GOODFOODS_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.getenv("GOODFOODS_DB", DATA_DIR / "goodfoods.db"))
TELEMETRY_DB_PATH = Path(os.getenv("GOODFOODS_TELEMETRY_DB", DATA_DIR / "telemetry.db"))

TIMEZONE = ZoneInfo(os.getenv("GOODFOODS_TZ", "Asia/Kolkata"))
CITY = "Bengaluru"

HOLD_MINUTES = int(os.getenv("GOODFOODS_HOLD_MINUTES", "10"))
BOOKING_WINDOW_DAYS = int(os.getenv("GOODFOODS_BOOKING_WINDOW_DAYS", "60"))
MAX_ONLINE_PARTY = 20  # larger groups are routed to the events team

SEED = int(os.getenv("GOODFOODS_SEED", "42"))


def now() -> datetime:
    """Current local time. ``GOODFOODS_NOW=2026-11-15T18:30`` freezes the clock (used by evals & tests)."""
    frozen = os.getenv("GOODFOODS_NOW")
    if frozen:
        dt = datetime.fromisoformat(frozen)
        return dt if dt.tzinfo else dt.replace(tzinfo=TIMEZONE)
    return datetime.now(TIMEZONE)
