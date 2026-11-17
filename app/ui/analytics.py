"""Read-only analytics queries for the Ops Dashboard (reservations DB + agent telemetry DB)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from app.ui.runtime import catalog, database, telemetry
from goodfoods import config


def _read(path: Path, sql: str, params: tuple = ()) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def restaurants() -> pd.DataFrame:
    return pd.DataFrame([{"restaurant_id": r.id, "restaurant": r.name, "brand": r.brand, "area": r.area,
                          "cuisine": r.cuisine, "seats": r.seats} for r in catalog().restaurants])


@st.cache_data(ttl=15, show_spinner=False)
def reservations() -> pd.DataFrame:
    database()
    df = _read(config.DB_PATH, """SELECT code, restaurant_id, party_size, date, start_min, end_min, status, channel,
                                  source, requested_restaurant_id, created_at FROM reservations""")
    df = df.merge(restaurants(), on="restaurant_id", how="left")
    df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=15, show_spinner=False)
def db_table(sql: str) -> pd.DataFrame:
    database()
    return _read(config.DB_PATH, sql)


@st.cache_data(ttl=15, show_spinner=False)
def telemetry_table(sql: str) -> pd.DataFrame:
    telemetry()  # make sure the schema exists
    return _read(config.TELEMETRY_DB_PATH, sql)


def occupancy_by_weekday_hour(df: pd.DataFrame, seats: int, today: pd.Timestamp) -> pd.DataFrame:
    """Average share of seats in use per weekday x hour over the last 14 days (completed visits)."""
    past = df[(df["status"] == "completed") & (df["date"] < today) & (df["date"] >= today - pd.Timedelta(days=14))]
    if past.empty:
        return pd.DataFrame(columns=["weekday", "hour", "occupancy"])
    hours = np.arange(8, 24)
    lo = hours[None, :] * 60
    overlap = np.clip(np.minimum(past["end_min"].to_numpy()[:, None], lo + 60)
                      - np.maximum(past["start_min"].to_numpy()[:, None], lo), 0, 60)
    seat_minutes = pd.DataFrame(overlap * past["party_size"].to_numpy()[:, None], columns=hours)
    seat_minutes["date"] = past["date"].to_numpy()
    per_day = seat_minutes.groupby("date").sum() / (seats * 60)
    per_day["weekday"] = per_day.index.day_name().str[:3]
    out = per_day.groupby("weekday").mean().reset_index().melt(id_vars="weekday", var_name="hour",
                                                                 value_name="occupancy")
    out["hour"] = out["hour"].astype(int)
    return out


def occupancy_at(df: pd.DataFrame, day: pd.Timestamp, minute: int) -> pd.DataFrame:
    live = df[(df["date"] == day) & df["status"].isin(["confirmed", "completed", "held"])
              & (df["start_min"] <= minute) & (df["end_min"] > minute)]
    used = live.groupby("restaurant_id")["party_size"].sum()
    out = restaurants().set_index("restaurant_id")
    out["guests"] = used.reindex(out.index).fillna(0)
    out["occupancy"] = (out["guests"] / out["seats"]).clip(upper=1.0)
    return out.reset_index()


def guardrail_kind(title: str) -> str:
    """'Repaired arguments for hold_table' -> 'Repaired arguments'."""
    return re.split(r" for | '| -> |: ", title)[0].replace("Policy blocked", "Policy block") \
        .replace("Policy adjusted", "Policy: forced preview")
