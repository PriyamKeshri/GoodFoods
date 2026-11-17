"""Shared fixtures: a frozen clock and a freshly seeded database per test (copied from a template)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FROZEN_NOW = "2026-11-16T10:00:00+05:30"  # a Monday morning; "saturday" -> 2026-11-21
os.environ["GOODFOODS_NOW"] = FROZEN_NOW

from goodfoods.core.seed import ensure_database  # noqa: E402
from goodfoods.core.service import ReservationService  # noqa: E402
from goodfoods.core.store import Store  # noqa: E402


@pytest.fixture(scope="session")
def template_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("template") / "goodfoods.db"
    ensure_database(path, force=True)
    return path


@pytest.fixture
def db_path(template_db: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "goodfoods.db"
    for suffix in ("", "-wal", "-shm"):
        src = Path(f"{template_db}{suffix}")
        if src.exists():
            shutil.copy(src, f"{dst}{suffix}")
    return dst


@pytest.fixture
def store(db_path: Path):
    s = Store(db_path)
    yield s
    s.close()


@pytest.fixture
def service(store: Store) -> ReservationService:
    return ReservationService(store)


@pytest.fixture
def mcp(db_path: Path):
    from goodfoods.agent.mcp_client import MCPStdioClient

    client = MCPStdioClient(env={"GOODFOODS_DB": str(db_path), "GOODFOODS_NOW": FROZEN_NOW,
                                 "GOODFOODS_TELEMETRY_DB": str(db_path.parent / "telemetry.db")},
                            log_path=db_path.parent / "mcp.log").connect()
    yield client
    client.close()
