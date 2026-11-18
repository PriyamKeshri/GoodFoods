"""Long-lived resources shared by every Streamlit session (one MCP server process, one catalog)."""

from __future__ import annotations

import atexit
import os
from dataclasses import asdict

import streamlit as st

from goodfoods import config
from goodfoods.agent.core import ConciergeAgent, TurnResult
from goodfoods.agent.llm import DEFAULT_PROFILE, PROFILES, LLMClient
from goodfoods.agent.mcp_client import MCPStdioClient
from goodfoods.agent.telemetry import Telemetry
from goodfoods.core.catalog import Catalog, get_catalog
from goodfoods.core.seed import ensure_database


@st.cache_resource(show_spinner="Preparing the demo database (first run only)…")
def database():
    return ensure_database(config.DB_PATH)


@st.cache_resource(show_spinner="Starting the GoodFoods MCP server…")
def mcp_client() -> MCPStdioClient:
    database()
    client = MCPStdioClient().connect()
    atexit.register(client.close)
    return client


@st.cache_resource
def telemetry() -> Telemetry:
    return Telemetry()


@st.cache_resource
def catalog() -> Catalog:
    return get_catalog()


@st.cache_data(ttl=20, show_spinner=False)
def model_status(profile_key: str) -> tuple[bool, str]:
    return LLMClient(profile_key).check()


def default_profile() -> str:
    key = os.getenv("LLM_PROFILE", DEFAULT_PROFILE)
    return key if key in PROFILES else next(iter(PROFILES))


def profile_keys() -> list[str]:
    """Models worth offering here: local ones (unless SHOW_LOCAL_MODELS=false, e.g. on a server without Ollama),
    hosted ones whose API key is set, and a custom endpoint once LLM_MODEL is set. The default is always listed."""
    show_local = os.getenv("SHOW_LOCAL_MODELS", "true").strip().lower() not in ("0", "false", "no")
    keys = [key for key, p in PROFILES.items()
            if (p.local and show_local) or (key == "custom" and p.model)
            or (not p.local and key != "custom" and p.api_key)]
    default = default_profile()
    return keys if default in keys else [default, *keys]


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("messages", [])
    if ss.get("profile") not in profile_keys():
        ss.profile = default_profile()
    ss.setdefault("guardrails", True)
    ss.setdefault("show_trace", True)


def get_agent() -> ConciergeAgent:
    """One agent per browser session; switching models keeps the conversation."""
    ss = st.session_state
    agent: ConciergeAgent | None = ss.get("agent")
    if agent is None or agent.llm.profile.key != ss.profile:
        fresh = ConciergeAgent(mcp_client(), LLMClient(ss.profile), guardrails=ss.guardrails, telemetry=telemetry())
        if agent is not None:
            fresh.history, fresh.turn = agent.history, agent.turn
            fresh.policy_state, fresh.session_id = agent.policy_state, agent.session_id
        ss.agent = agent = fresh
    agent.guardrails = ss.guardrails
    return agent


def reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.pop("agent", None)


def pack(result: TurnResult) -> dict:
    """TurnResult -> plain dict kept in session state for re-rendering."""
    return {
        "latency_ms": result.latency_ms, "llm_calls": result.llm_calls, "error": result.error,
        "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
        "events": [asdict(e) for e in result.events],
        "tools": [{"name": t.name, "args": t.args, "ok": t.ok, "blocked": t.blocked, "structured": t.structured,
                   "text": t.text, "ms": t.ms} for t in result.tools],
    }
