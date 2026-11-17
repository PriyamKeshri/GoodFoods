"""Protocol-level tests: our hand-written MCP client against the SDK-based server over stdio."""

import json
import sqlite3


def test_handshake_and_discovery(mcp):
    assert mcp.server_info["name"] == "goodfoods-reservations"
    assert mcp.protocol_version == "2025-11-25"
    tools = {t.name: t for t in mcp.list_tools()}
    assert len(tools) == 10
    assert tools["confirm_reservation"].policy["requires_prior_turn"]["tool"] == "hold_table"
    assert tools["search_restaurants"].annotations.get("readOnlyHint") is True
    assert {r["uri"] for r in mcp.list_resources()} >= {"goodfoods://policies", "goodfoods://areas"}
    assert mcp.list_resource_templates()[0]["uriTemplate"] == "goodfoods://restaurants/{restaurant_id}"
    assert [p["name"] for p in mcp.list_prompts()] == ["concierge_brief"]


def test_resources_and_prompts(mcp):
    assert "Free cancellation" in mcp.read_resource("goodfoods://policies")
    assert json.loads(mcp.read_resource("goodfoods://restaurants/sakura-street-indiranagar"))["cuisine"] == "Japanese"
    assert "Booking flow" in mcp.get_prompt("concierge_brief")


def test_tool_results_carry_text_for_the_model_and_structured_data_for_the_ui(mcp):
    out = mcp.call_tool("search_restaurants", {"cuisine": "biryani", "area": "Frazer Town"})
    assert not out.is_error and out.structured["results"]
    assert json.loads(out.text) == out.structured


def test_domain_errors_come_back_as_instructive_tool_errors(mcp):
    out = mcp.call_tool("hold_table", {"restaurant": "ember-and-oak-whitefield", "date": "tomorrow", "time": "8pm",
                                       "party_size": 2, "guest_name": "Rohan Iyer", "phone": "12345"})
    assert out.is_error and "Hint:" in out.text


def test_session_id_travels_in_request_meta(mcp, db_path):
    mcp.call_tool("check_availability", {"restaurant": "trattoria-verde-indiranagar", "date": "saturday",
                                         "time": "8pm", "party_size": 4}, meta={"goodfoods/session": "sess-42"})
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT outcome FROM demand_log WHERE session_id='sess-42'").fetchone()
    assert row == ("full",)
