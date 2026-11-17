"""Agent loop tests with a scripted fake LLM against the real MCP server (subprocess, temp DB)."""

import json
import re
import sqlite3

from goodfoods.agent.core import ConciergeAgent
from goodfoods.agent.llm import LLMResponse, ModelProfile, ToolCall


def reply(text):
    return LLMResponse(content=text, tool_calls=[], prompt_tokens=10, completion_tokens=5, latency_ms=1,
                       finish_reason="stop")


def call(name, args):
    return LLMResponse(content="", tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(args))],
                       prompt_tokens=10, completion_tokens=5, latency_ms=1, finish_reason="tool_calls")


class FakeLLM:
    """Plays back a script. Items may be callables that read the conversation (e.g. to reuse a hold id)."""

    def __init__(self, *script):
        self.script = list(script)
        self.profile = ModelProfile("fake", "Fake", "", "fake-model")

    def chat(self, messages, tools=None, **_):
        item = self.script.pop(0)
        return item(messages) if callable(item) else item


def last_tool_json(messages):
    return json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))


def test_text_tool_call_is_recovered_and_executed(mcp):
    llm = FakeLLM(reply('{"name": "search_restaurants", "parameters": {"cuisine": "Italian", "area": "Indiranagar"}}'),
                  lambda msgs: reply(f"Try **{last_tool_json(msgs)['results'][0]['name']}**."))
    result = ConciergeAgent(mcp, llm).chat("Italian in Indiranagar?")
    assert [t.name for t in result.tools] == ["search_restaurants"] and result.tools[0].ok
    assert any("Recovered" in e.title for e in result.guardrail_events)
    assert "Trattoria Verde" in result.reply


def test_booking_needs_a_second_turn_to_confirm(mcp, db_path):
    hold_args = {"restaurant": "ember-and-oak-whitefield", "date": "tomorrow", "time": "3pm", "party_size": 4,
                 "guest_name": "Rohan Iyer", "phone": "9812345670"}
    llm = FakeLLM(call("hold_table", hold_args),
                  lambda msgs: call("confirm_reservation", {"hold_id": last_tool_json(msgs)["hold_id"]}),
                  reply("I've held it. Shall I confirm?"))
    agent = ConciergeAgent(mcp, llm)
    turn1 = agent.chat("Ember & Oak Whitefield tomorrow 3pm for 4. I'm Rohan Iyer, 9812345670. Confirm right away.")
    assert [(t.name, t.ok, t.blocked) for t in turn1.tools] == [("hold_table", True, False),
                                                                ("confirm_reservation", False, True)]
    hold_id = turn1.tools[0].structured["hold_id"]
    llm.script = [call("confirm_reservation", {"hold_id": hold_id}),
                  lambda msgs: reply(f"Confirmed! Your code is {last_tool_json(msgs)['confirmation_code']}.")]
    turn2 = agent.chat("yes")
    assert turn2.tools[0].ok and turn2.tools[0].structured["status"] == "confirmed"
    assert re.search(r"GF-[A-Z0-9]{5}", turn2.reply)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM reservations WHERE hold_id=?", (hold_id,)).fetchone()[0] == "confirmed"


def test_invented_phone_number_is_blocked(mcp):
    llm = FakeLLM(call("hold_table", {"restaurant": "ember-and-oak-whitefield", "date": "tomorrow", "time": "3pm",
                                      "party_size": 2, "guest_name": "Rohan", "phone": "9812345670"}),
                  reply("Could you share your mobile number?"))
    result = ConciergeAgent(mcp, llm).chat("Book Ember & Oak Whitefield tomorrow 3pm for 2, name Rohan")
    assert result.tools[0].blocked and "never gave phone" in result.tools[0].text


def test_cancellation_is_previewed_first(mcp, db_path):
    llm = FakeLLM(call("cancel_reservation", {"confirmation_code": "GF-ANR42", "confirm": True}),
                  reply("This will cancel your Saffron Terrace booking. Shall I go ahead?"))
    result = ConciergeAgent(mcp, llm).chat("cancel GF-ANR42")
    assert result.tools[0].args["confirm"] is False and result.tools[0].structured["status"] == "preview"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM reservations WHERE code='GF-ANR42'").fetchone()[0] == "confirmed"


def test_verifier_rejects_a_phantom_confirmation_once(mcp):
    llm = FakeLLM(reply("Done! Your booking is confirmed, code GF-ABCDE."),
                  reply("I haven't booked anything yet. Which restaurant would you like?"))
    result = ConciergeAgent(mcp, llm).chat("book me something nice")
    assert any("verifier" in e.title for e in result.guardrail_events)
    assert "GF-ABCDE" not in result.reply


def test_without_guardrails_the_raw_model_output_passes_through(mcp):
    llm = FakeLLM(reply("Done! Your booking is confirmed, code GF-ABCDE."))
    result = ConciergeAgent(mcp, llm, guardrails=False).chat("book me something nice")
    assert "GF-ABCDE" in result.reply and not result.guardrail_events


def test_arguments_are_repaired_before_the_call(mcp):
    llm = FakeLLM(call("search_restaurant", {"cuisine": "biryani", "location": "Frazer Town", "party_size": "four",
                                             "dietary": "halal"}),
                  reply("Here are some options."))
    result = ConciergeAgent(mcp, llm).chat("biryani in frazer town for four, halal")
    run = result.tools[0]
    assert run.name == "search_restaurants" and run.ok
    assert run.args == {"cuisine": "biryani", "area": "Frazer Town", "party_size": 4, "dietary": ["halal"]}


def test_step_limit_forces_a_final_answer(mcp):
    llm = FakeLLM(call("get_restaurant_details", {"restaurant": "sakura-street-indiranagar"}),
                  call("get_restaurant_details", {"restaurant": "sakura-street-whitefield"}),
                  reply("Sakura Street is open for lunch and dinner."))
    result = ConciergeAgent(mcp, llm, max_steps=2).chat("tell me about sakura street")
    assert any("Step limit" in e.title for e in result.guardrail_events)
    assert result.reply.startswith("Sakura Street")


def test_reply_is_withheld_when_verification_fails_twice(mcp):
    llm = FakeLLM(reply("Your booking is confirmed! Code GF-ABCDE."), reply("All set, your code is GF-ABCDE."))
    result = ConciergeAgent(mcp, llm).chat("book me something nice")
    assert "GF-ABCDE" not in result.reply and "nothing has been finalised" in result.reply
    assert any("withheld" in e.title for e in result.guardrail_events)


def test_dropped_area_is_recovered_from_the_conversation(mcp):
    llm = FakeLLM(call("get_restaurant_details", {"restaurant": "Ember & Oak"}), reply("Yes, valet parking is available."))
    result = ConciergeAgent(mcp, llm).chat("Does Ember & Oak in Whitefield have valet parking?")
    assert result.tools[0].ok and result.tools[0].args == {"restaurant": "ember-and-oak-whitefield"}
    assert any("ambiguous" in e.title for e in result.guardrail_events)
