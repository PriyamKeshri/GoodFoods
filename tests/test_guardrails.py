import pytest

from goodfoods.agent.guardrails import (
    PolicyEngine,
    PolicyState,
    ToolOutcome,
    coerce_arguments,
    date_note,
    is_grounded,
    recover_text_tool_calls,
    repair_tool_name,
    verify_reply,
)
from goodfoods.agent.mcp_client import ToolSpec

TOOLS = {"search_restaurants", "hold_table", "confirm_reservation", "cancel_reservation"}


@pytest.mark.parametrize("content", [
    '{"name": "search_restaurants", "parameters": {"cuisine": "Italian"}}',
    'Let me check.\n{"type":"function","name":"search_restaurants","parameters":{"cuisine":"Italian"}}',
    '<|python_tag|>{"name": "search_restaurants", "arguments": "{\\"cuisine\\": \\"Italian\\"}"}',
    '<function=search_restaurants>{"cuisine": "Italian"}</function>',
    '<tool_call>{"name": "search_restaurants", "arguments": {"cuisine": "Italian"}}</tool_call>',
    '```json\n{"name": "search_restaurant", "parameters": {"cuisine": "Italian"}}\n```',
])
def test_tool_calls_written_as_text_are_recovered(content):
    assert recover_text_tool_calls(content, TOOLS) == [("search_restaurants", {"cuisine": "Italian"})]


def test_plain_replies_are_not_mistaken_for_tool_calls():
    assert recover_text_tool_calls("Sure! Trattoria Verde has a table at 8 PM.", TOOLS) == []
    assert recover_text_tool_calls('{"name": "launch_rocket", "parameters": {}}', TOOLS) == []


def test_tool_name_repair():
    assert repair_tool_name("functions.hold_table", TOOLS) == "hold_table"
    assert repair_tool_name("confirm_reservations", TOOLS) == "confirm_reservation"
    assert repair_tool_name("delete_everything", TOOLS) is None


SCHEMA = {"type": "object", "required": ["restaurant", "party_size"], "properties": {
    "restaurant": {"type": "string"}, "party_size": {"type": "integer"}, "area": {"type": "string"},
    "dietary": {"type": "array", "items": {"type": "string"}}, "confirm": {"type": "boolean"},
    "phone": {"type": "string"}}}


def test_argument_coercion_fixes_common_small_model_mistakes():
    out = coerce_arguments('{"parameters": {"restaurant_id": "x", "partySize": "four", "location": "HSR", '
                           '"dietary": "veg, jain", "confirm": "yes", "phone": 9812345670, "mood": "happy", '
                           '"area_note": null}}', SCHEMA)
    assert out.args == {"restaurant": "x", "party_size": 4, "area": "HSR", "dietary": ["veg", "jain"],
                        "confirm": True, "phone": "9812345670"}
    assert out.missing == [] and any("mood" in n for n in out.notes)


def test_placeholders_count_as_missing():
    out = coerce_arguments({"restaurant": "unknown", "party_size": "2 people"}, SCHEMA)
    assert out.args == {"party_size": 2} and out.missing == ["restaurant"]


def test_grounding_of_names_and_phone_numbers():
    corpus = "book it under priya sharma, my number is 98450 11122"
    digits = ["984501112 2".replace(" ", "")]
    assert is_grounded("phone", "+91-9845011122", corpus, digits)
    assert not is_grounded("phone", "9876543210", corpus, digits)
    assert is_grounded("guest_name", "Ms. Priya Sharma", corpus, digits)
    assert not is_grounded("guest_name", "Priya Kapoor", corpus, digits)


def _engine():
    return PolicyEngine({
        "hold_table": ToolSpec("hold_table", "", {}, meta={"goodfoods/policy": {"grounded_args": ["guest_name", "phone"]}}),
        "confirm_reservation": ToolSpec("confirm_reservation", "", {}, meta={"goodfoods/policy": {
            "requires_prior_turn": {"arg": "hold_id", "tool": "hold_table", "field": "hold_id"}}}),
        "cancel_reservation": ToolSpec("cancel_reservation", "", {}, meta={"goodfoods/policy": {
            "two_phase": {"flag": "confirm", "key": "confirmation_code"}}}),
    })


def test_confirm_is_blocked_in_the_same_turn_as_the_hold_and_allowed_next_turn():
    engine, state = _engine(), PolicyState()
    engine.record("hold_table", {}, {"status": "held", "hold_id": "HOLD-1234"}, state=state, turn=3)
    kw = dict(state=state, corpus="", corpus_digits=[])
    assert engine.check("confirm_reservation", {"hold_id": "HOLD-1234"}, turn=3, **kw).action == "block"
    assert engine.check("confirm_reservation", {"hold_id": "hold-1234"}, turn=4, **kw).action == "allow"


def test_two_phase_actions_are_downgraded_to_preview_until_previewed():
    engine, state = _engine(), PolicyState()
    kw = dict(state=state, corpus="", corpus_digits=[])
    first = engine.check("cancel_reservation", {"confirmation_code": "GF-ANR42", "confirm": True}, turn=1, **kw)
    assert first.action == "modify" and first.args["confirm"] is False
    engine.record("cancel_reservation", first.args, {"status": "preview"}, state=state, turn=1)
    assert engine.check("cancel_reservation", {"confirmation_code": "GF-ANR42", "confirm": True}, turn=2,
                        **kw).action == "allow"


def test_invented_contact_details_are_blocked():
    engine, state = _engine(), PolicyState()
    decision = engine.check("hold_table", {"guest_name": "John Doe", "phone": "9812345670"}, state=state, turn=1,
                            corpus="book a table for 2 tomorrow", corpus_digits=["2"])
    assert decision.action == "block" and "never gave" in decision.message


@pytest.mark.parametrize("kind, value, said, expected", [
    ("count", 4, "kal raat 8 baje 4 logon ke liye", True),
    ("count", 2, "a table for two please", True),
    ("count", 2, "book a table at sakura street", False),
    ("count", 4, "rated 4.7 stars", False),
    ("count", 4, "tomorrow 3pm for 4. i'm rohan", True),
    ("count", 4, "a table for 14 people", False),
    ("count", 1500, "somewhere under ₹1,500 for two", True),
    ("count", 1000, "romantic italian for two", False),
    ("time", "20:00", "dinner tomorrow", True),
    ("time", "20:00", "book sakura street", False),
    ("date", "2026-11-17", "something for tomorrow", True),
    ("date", "2026-11-17", "book sakura street", False),
])
def test_dates_times_and_head_counts_must_come_from_the_guest(kind, value, said, expected):
    assert is_grounded("x", value, said, [], kind=kind, user_corpus=said) is expected


def test_date_note_resolves_relative_dates_for_the_model():
    from datetime import date

    note = date_note("table for 4 this saturday, or kal if that's full", date(2026, 11, 16))
    assert "'this saturday' = Sat, 21 Nov 2026 (2026-11-21)" in note and "(2026-11-17)" in note
    assert date_note("table for 4 at 8pm", date(2026, 11, 16)) is None


def test_invented_optional_details_are_dropped_not_blocked():
    engine = PolicyEngine({"search_restaurants": ToolSpec("search_restaurants", "", {}, meta={"goodfoods/policy": {
        "grounded_optional": {"date": "date", "time": "time", "party_size": "count"}}})})
    said = "book a table at sakura street indiranagar"
    decision = engine.check("search_restaurants", {"area": "Indiranagar", "date": "tomorrow", "time": "8pm",
                                                   "party_size": 2}, state=PolicyState(), turn=1, corpus=said,
                            corpus_digits=[], user_corpus=said)
    assert decision.action == "modify" and decision.args == {"area": "Indiranagar"}


def test_a_lookup_counts_as_the_preview_for_cancelling():
    engine = PolicyEngine({
        "get_reservation": ToolSpec("get_reservation", "", {}),
        "cancel_reservation": ToolSpec("cancel_reservation", "", {}, meta={"goodfoods/policy": {"two_phase": {
            "flag": "confirm", "key": "confirmation_code", "also_satisfied_by": ["get_reservation"]}}}),
    })
    state = PolicyState()
    engine.record("get_reservation", {}, {"bookings": [{"confirmation_code": "GF-FTK19"}]}, state=state, turn=1)
    kw = dict(state=state, corpus="", corpus_digits=[])
    assert engine.check("cancel_reservation", {"confirmation_code": "GF-FTK19", "confirm": True}, turn=1,
                        **kw).action == "modify"
    assert engine.check("cancel_reservation", {"confirmation_code": "GF-FTK19", "confirm": True}, turn=2,
                        **kw).action == "allow"


def test_verifier_catches_phantom_confirmations_and_fake_codes():
    held = [ToolOutcome("hold_table", True, "held")]
    assert verify_reply("Your booking is confirmed!", held, '{"status":"held"}')
    assert verify_reply("All done, code GF-ABCDE.", [], "") is not None
    assert verify_reply("Try **La Dolce Vita** in Indiranagar.", [], '{"name":"Trattoria Verde"}')


def test_verifier_tolerates_bold_labels_built_from_real_names():
    corpus = '{"results":[{"name":"Trattoria Verde, Indiranagar"},{"name":"Trattoria Verde, Lavelle Road"}]}'
    assert verify_reply("Or join the **Waitlist at Trattoria Verde, Indiranagar**.", [], corpus) is None
    assert verify_reply("Try **Bella Italia** instead.", [], corpus)


def test_odd_contact_keys_are_mapped():
    schema = {"properties": {"phone": {"type": "string"}, "guest_name": {"type": "string"}}, "required": ["phone"]}
    out = coerce_arguments({"guest_phone_number": "98450 11122", "customer_full_name": "Priya"}, schema)
    assert out.args == {"phone": "98450 11122", "guest_name": "Priya"} and not out.missing


def test_telemetry_redacts_phones_but_not_dates():
    from goodfoods.agent.telemetry import redact

    out = redact({"phone": "9845011122", "date": "2026-11-21", "guest_name": "Priya Sharma"})
    assert out == {"phone": "•••••••22", "date": "2026-11-21", "guest_name": "P.S."}


def test_verifier_catches_asking_to_confirm_before_holding():
    assert verify_reply("Here's a summary: Trattoria Verde, 8 PM, 2 guests. Shall I confirm your booking?",
                        [ToolOutcome("get_restaurant_details", True, None)], "{}")
    assert verify_reply("Shall I confirm?", [ToolOutcome("hold_table", True, "held")], '{"status":"held"}') is None
    assert verify_reply("Would you like me to hold it for you?", [], "") is None


def test_verifier_accepts_grounded_replies():
    corpus = '{"status":"confirmed","confirmation_code":"GF-SREJY","booking":{"restaurant":"Trattoria Verde, Lavelle Road"}}'
    done = [ToolOutcome("confirm_reservation", True, "confirmed")]
    assert verify_reply("Your booking is confirmed! Code **GF-SREJY** at **Trattoria Verde, Lavelle Road**.", done,
                        corpus) is None
    assert verify_reply("**Restaurant:** Trattoria Verde\nShall I confirm?", [ToolOutcome("hold_table", True, "held")],
                        corpus) is None


def test_codes_the_guest_typed_may_be_repeated_back():
    assert verify_reply("Shall I cancel reservation GF-FTK19?", [], "", guest_text="cancel my reservation gf-ftk19") is None
    assert verify_reply("Your code is GF-ZZZZZ.", [], "", guest_text="cancel my reservation gf-ftk19")


def test_ambiguous_reference_is_resolved_from_the_guests_words():
    from goodfoods.agent.guardrails import resolve_ambiguity

    error = ("Error executing tool hold_table: 'Ember & Oak' matches several locations: Ember & Oak, Whitefield "
             "(ember-and-oak-whitefield), Ember & Oak, Koramangala (ember-and-oak-koramangala), Ember & Oak, MG Road "
             "(ember-and-oak-mg-road). Hint: If the guest already named an area, use it.")
    args = {"restaurant": "Ember & Oak", "party_size": 4}
    fixed = resolve_ambiguity(error, args, "i'd like to book ember & oak in whitefield for 4")
    assert fixed == {"restaurant": "ember-and-oak-whitefield", "party_size": 4}
    assert resolve_ambiguity(error, args, "book ember & oak for 4") is None                      # nothing singles one out
    assert resolve_ambiguity(error, args, "whitefield or koramangala, either works") is None      # two candidates match
    assert resolve_ambiguity("Error: no table", args, "whitefield") is None


def test_verifier_recognises_feature_names_written_with_underscores():
    corpus = '{"name":"Ember & Oak, Whitefield","features":["bar","valet_parking"]}'
    assert verify_reply("Yes, **Ember & Oak, Whitefield** offers **Valet parking**.", [], corpus) is None
