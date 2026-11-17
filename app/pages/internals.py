"""Under the hood: architecture, live MCP introspection, a raw tool console, prompts and evals."""

from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from app.ui import runtime, theme
from goodfoods import config
from goodfoods.agent.prompts import build_system_prompt

theme.setup()
theme.hero("System Architecture",
           "A purpose-built agent communicating with a standards-compliant MCP server. All information on this "
           "page is retrieved live over the protocol.",
           ["No agent framework", "MCP 2025-11-25 over stdio", "Server-declared policies", "Evaluated"])

mcp = runtime.mcp_client()
tools = mcp.list_tools()

arch, live, console, prompts, evals = st.tabs([
    ":material/schema: Architecture", ":material/cable: MCP server", ":material/terminal: Tool console",
    ":material/description: Prompts", ":material/fact_check: Evals"])

with arch:
    st.graphviz_chart("""
digraph G {
  rankdir=TB; bgcolor="transparent"; nodesep=0.5; ranksep=0.35;
  node [shape=box, style="rounded,filled", fillcolor="#F6F5FB", color="#5B3FD6", fontname="Helvetica", fontsize=11];
  edge [color="#6B6880", fontname="Helvetica", fontsize=9];
  guest [label="Guest\\nweb today · WhatsApp / voice next", shape=oval, fillcolor="#FFFFFF"];
  ui [label="Streamlit UI\\ncards · trace · map"];
  agent [label="Concierge agent (from scratch)\\nlayered prompt · tool loop\\nguardrails · verifier", fillcolor="#EEEAFE"];
  llm [label="Small LLM (8B)\\nLlama 3.1 / Qwen3\\nOllama · Groq · OpenRouter"];
  client [label="MCP client\\nJSON-RPC 2.0 over stdio\\n(hand-written)"];
  server [label="GoodFoods MCP server\\n10 tools · 4 resources · 1 prompt\\npolicies in tool _meta", fillcolor="#EEEAFE"];
  core [label="Domain core\\nrecommender · table inventory\\nbookings · waitlist"];
  db [label="SQLite\\nreservations · guests\\ndemand log", shape=cylinder];
  tel [label="Telemetry\\n(PII-redacted)", shape=cylinder];
  dash [label="Operations overview\\nROI model · demand signals"];
  hosts [label="Any MCP host\\ndesktop assistants · other agents", shape=oval, fillcolor="#FFFFFF"];
  guest -> ui -> agent;
  agent -> llm [label="chat + tool schemas"];
  agent -> client -> server [label="tools/call"];
  server -> core -> db;
  agent -> tel -> dash; db -> dash;
  hosts -> server [style=dashed, label="same tools"];
  {rank=same; llm; client;} {rank=same; db; tel;}
}""", width="content")
    st.markdown("""
**Request lifecycle.** The model reviews the conversation and the tool schemas, then **determines** whether to
respond or to invoke tools; no request is routed by keywords. Each tool call passes through the reliability layer
(name and argument repair, provenance checks, server-declared policies), is executed over MCP, and returns compact
text for the model together with `structuredContent` for the interface. The final response is verified against tool
results before it is presented to the guest.

**Rationale for MCP.** The same server supports this interface and can be connected to any MCP-compatible assistant
or agent. A WhatsApp assistant, a voice line or a partner travel assistant could reserve GoodFoods tables through a
single integration.""")

with live:
    a, b, c, d = st.columns(4)
    a.metric(mcp.server_info.get("name", "server"), f"v{mcp.server_info.get('version', '?')}", border=True)
    b.metric("Protocol", mcp.protocol_version or "?", border=True)
    c.metric("Tools", len(tools), border=True)
    d.metric("Resources", len(mcp.list_resources()) + len(mcp.list_resource_templates()), border=True)
    rows = []
    for t in tools:
        policy = t.policy
        rule = ("two-step: preview, then confirm" if "two_phase" in policy else
                "only in a later turn than the hold" if "requires_prior_turn" in policy else
                "name & phone must come from the guest" if "grounded_args" in policy else "")
        rows.append({"tool": t.name, "description": t.description, "read-only": bool(t.annotations.get("readOnlyHint")),
                     "required": ", ".join(t.input_schema.get("required", [])), "agent policy": rule})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"description": st.column_config.TextColumn(width="large")})
    pick = st.selectbox("Inspect a tool schema (exactly as provided to the model)", [t.name for t in tools])
    st.json(next(t for t in tools if t.name == pick).to_openai(), expanded=True)
    st.markdown("**Resources**")
    uris = [r["uri"] for r in mcp.list_resources()] + ["goodfoods://restaurants/sakura-street-indiranagar"]
    uri = st.selectbox("Retrieve a resource", uris)
    content = mcp.read_resource(uri)
    if content.strip().startswith(("{", "[")):
        st.json(json.loads(content), expanded=False)
    else:
        st.markdown(content)

with console:
    st.caption("Invoke any tool directly over MCP, through the same path the agent uses, without model involvement.")
    examples = {
        "search_restaurants": {"cuisine": "biryani", "area": "Frazer Town", "date": "tomorrow", "time": "8pm",
                               "party_size": 4},
        "check_availability": {"restaurant": "trattoria-verde-indiranagar", "date": "saturday", "time": "8pm",
                               "party_size": 4},
        "get_restaurant_details": {"restaurant": "sakura street indiranagar"},
        "get_reservation": {"confirmation_code": "GF-ANR42"},
        "hold_table": {"restaurant": "ember-and-oak-whitefield", "date": "tomorrow", "time": "8pm", "party_size": 2,
                       "guest_name": "Test Guest", "phone": "9876543210"},
    }
    name = st.selectbox("Tool", [t.name for t in tools], index=0, key="console-tool")
    args = st.text_area("Arguments (JSON)", json.dumps(examples.get(name, {}), indent=2), height=170,
                        key=f"console-args-{name}")
    if st.button("Invoke via MCP", type="primary"):
        try:
            out = mcp.call_tool(name, json.loads(args or "{}"), meta={"goodfoods/session": "console"})
            (st.error if out.is_error else st.success)(f"isError={out.is_error} · {out.latency_ms:.0f} ms", icon=":material/cable:")
            if out.structured:
                st.json(out.structured)
            else:
                st.code(out.text, wrap_lines=True)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")
    st.caption("The hold_table example contains a placeholder telephone number, which the server rejects with "
               "corrective guidance for the model.")

with prompts:
    brief = mcp.get_prompt("concierge_brief")
    st.markdown("The system prompt comprises three layers: the **agent core** (behaviour common to any business), the "
                "**domain brief** supplied by the MCP server as the `concierge_brief` prompt, and a **calendar "
                "reference** covering the next seven days, since small models are unreliable at date arithmetic.")
    st.code(build_system_prompt(brief=brief, now=config.now()), language="markdown", wrap_lines=True)

with evals:
    summary_path = Path(config.ROOT) / "evals" / "results" / "summary.json"
    if not summary_path.exists():
        st.info("No eval results yet. Run `python -m evals.run_evals`.", icon=":material/bar_chart:")
    else:
        df = pd.DataFrame(json.loads(summary_path.read_text()))
        df["success"] = df["scenarios_passed"] / df["scenarios"]
        df["model"] = df["config"].str.extract(r"ollama-([^:]+):")[0]
        df["guardrails"] = df["config"].str.endswith(":on").map({True: "on", False: "off"})
        st.altair_chart(alt.Chart(df).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("success:Q", title="Scenarios passed", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("model:N", title=None), yOffset="guardrails:N",
            color=alt.Color("guardrails:N", scale=alt.Scale(domain=["on", "off"], range=["#5B3FD6", "#C9C6DA"])),
            tooltip=["config", "scenarios_passed", "scenarios", "avg_latency_s"]).properties(height=200),
            width="stretch")
        st.dataframe(df[["config", "scenarios_passed", "scenarios", "checks_passed", "checks", "unconfirmed_actions",
                         "invented_contact_executed", "ungrounded_replies", "avg_latency_s", "llm_calls_per_turn"]],
                     hide_index=True, width="stretch")
        st.caption("Full report: docs/EVALS.md · transcripts: evals/results/*.json")
