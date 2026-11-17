"""Reliability layer that makes an 8B model behave like a much larger one on tool use.

Every guard targets a failure we actually observed with Llama-3.1-8B / Qwen3-8B:

1. ``recover_text_tool_calls`` - the model writes a tool call as JSON in its reply instead of
   using the native tool-call channel.
2. ``repair_tool_name``       - near-miss tool names ("search_restaurant", "functions.hold").
3. ``coerce_arguments``        - "4" instead of 4, "veg, jain" instead of a list, camelCase keys,
   placeholder values like "unknown", double-wrapped {"parameters": {...}}.
4. ``is_grounded``             - invented guest names / phone numbers (must appear in the chat).
5. ``PolicyEngine``            - server-declared rules: two-phase commits and "confirm only in a
   later turn" (stops the model confirming bookings the guest never approved).
6. ``verify_reply``            - phantom confirmations and made-up booking codes in the final reply.

None of these decide *what the user wants*: intent and tool choice stay with the LLM. They only
repair malformed actions and block unsafe ones, with feedback the model can act on.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from goodfoods.agent.mcp_client import ToolSpec

# ======================================================================================
# 1-2. Tool calls emitted as text, and tool-name repair
# ======================================================================================
_FUNCTION_TAG = re.compile(r"<function=([\w.\-]+)>(.*?)</function>", re.S)
_TOOL_CALL_TAG = re.compile(r"<tool_call>(.*?)</tool_call>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def repair_tool_name(name: str, known: set[str] | list[str]) -> str | None:
    known = set(known)
    if name in known:
        return name
    cleaned = re.sub(r"[^a-z0-9_]", "_", name.strip().split(".")[-1].lower()).strip("_")
    if cleaned in known:
        return cleaned
    match = difflib.get_close_matches(cleaned, sorted(known), n=1, cutoff=0.72)
    return match[0] if match else None


def _json_objects(text: str) -> list[Any]:
    decoder, found, i = json.JSONDecoder(), [], 0
    while i < len(text):
        if text[i] in "{[":
            try:
                obj, end = decoder.raw_decode(text, i)
                found.append(obj)
                i = end
                continue
            except json.JSONDecodeError:
                pass
        i += 1
    return found


def recover_text_tool_calls(content: str, known: set[str]) -> list[tuple[str, dict]]:
    """Parse tool calls a model wrote into its text reply (Llama/Hermes/raw-JSON styles)."""
    if not content or ("{" not in content and "<function" not in content):
        return []
    text = content.replace("<|python_tag|>", "").replace("<|eom_id|>", "").replace("<|eot_id|>", "")
    fenced = _FENCE.findall(text)
    candidates: list[tuple[str, Any]] = []
    for name, body in _FUNCTION_TAG.findall(text):
        objs = _json_objects(body)
        candidates.append((name, objs[0] if objs else {}))
    for body in _TOOL_CALL_TAG.findall(text):
        for obj in _json_objects(body):
            if isinstance(obj, dict) and "name" in obj:
                candidates.append((obj["name"], obj.get("arguments", obj.get("parameters", {}))))
    if not candidates:
        for obj in _json_objects("\n".join(fenced) if fenced else text):
            for item in obj if isinstance(obj, list) else [obj]:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    candidates.append((item["name"], item.get("parameters", item.get("arguments", {}))))
                elif isinstance(item, dict) and isinstance(item.get("function"), dict):
                    fn = item["function"]
                    candidates.append((fn.get("name", ""), fn.get("arguments", fn.get("parameters", {}))))
    calls = []
    for name, args in candidates:
        fixed = repair_tool_name(str(name), known)
        if not fixed:
            continue
        if isinstance(args, str):
            args = _loads_lenient(args) or {}
        calls.append((fixed, args if isinstance(args, dict) else {}))
    return calls


# ======================================================================================
# 3. Argument coercion against the tool's JSON schema
# ======================================================================================
_DROP = object()
_EMPTY = {"", "null", "none", "n/a", "na", "unknown", "not specified", "not provided", "not given", "tbd", "?"}
WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "couple": 2, "a couple": 2, "pair": 2, "solo": 1,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8,
}
KEY_SYNONYMS = {
    "restaurant_id": "restaurant", "restaurant_name": "restaurant", "venue": "restaurant",
    "name": "guest_name", "customer_name": "guest_name", "full_name": "guest_name", "guest": "guest_name",
    "phone_number": "phone", "mobile": "phone", "mobile_number": "phone", "contact": "phone",
    "people": "party_size", "guests": "party_size", "num_guests": "party_size", "number_of_people": "party_size",
    "pax": "party_size", "covers": "party_size", "party": "party_size", "size": "party_size",
    "location": "area", "neighbourhood": "area", "neighborhood": "area", "locality": "area",
    "code": "confirmation_code", "booking_id": "confirmation_code", "reservation_id": "confirmation_code",
    "booking_code": "confirmation_code", "reservation_code": "confirmation_code",
    "cuisine_type": "cuisine", "food": "cuisine", "budget": "budget_for_two", "diet": "dietary",
    "special_request": "special_requests", "notes": "special_requests", "hold": "hold_id",
}


@dataclass
class Coercion:
    args: dict
    notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _loads_lenient(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return {}
    for attempt in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            value = json.loads(attempt)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            continue
    fixed = (text.replace("'", '"').replace("None", "null").replace("True", "true").replace("False", "false"))
    try:
        value = json.loads(re.sub(r",\s*([}\]])", r"\1", fixed))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _types(prop: dict) -> set[str]:
    if "type" in prop:
        return {prop["type"]} if isinstance(prop["type"], str) else set(prop["type"])
    return {o.get("type") for o in prop.get("anyOf", []) if o.get("type") and o.get("type") != "null"}


def _snake(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", re.sub(r"([a-z])([A-Z])", r"\1_\2", key).lower()).strip("_")


def _match_key(key: str, props: dict, taken: set[str]) -> str | None:
    snake = _snake(key)
    for candidate in (snake, KEY_SYNONYMS.get(snake)):
        if candidate and candidate in props and candidate not in taken:
            return candidate
    free = [p for p in props if p not in taken]
    close = difflib.get_close_matches(snake, free, n=1, cutoff=0.8)
    if close:
        return close[0]
    # "guest_phone_number" -> phone, "customer_full_name" -> guest_name, "restaurant_slug" -> restaurant
    for word, target in (("phone", "phone"), ("mobile", "phone"), ("contact", "phone"), ("name", "guest_name"),
                         ("restaurant", "restaurant"), ("people", "party_size"), ("guests", "party_size")):
        if word in snake and target in free:
            return target
    return None


def _coerce_value(value: Any, prop: dict, key: str, notes: list[str]) -> Any:
    types = _types(prop)
    if value is None or (isinstance(value, str) and value.strip().lower() in _EMPTY):
        return _DROP
    if "integer" in types and not isinstance(value, bool):
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            s = value.strip().lower()
            if s in WORD_NUMBERS:
                notes.append(f"{key}: '{value}' -> {WORD_NUMBERS[s]}")
                return WORD_NUMBERS[s]
            digits = re.search(r"\d+", s)
            if digits:
                if s != digits.group():
                    notes.append(f"{key}: '{value}' -> {int(digits.group())}")
                return int(digits.group())
            for word, n in WORD_NUMBERS.items():
                if re.search(rf"\b{word}\b", s):
                    notes.append(f"{key}: '{value}' -> {n}")
                    return n
        return value
    if "boolean" in types:
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "yes", "y", "1", "confirm", "confirmed"):
            return True
        if s in ("false", "no", "n", "0"):
            return False
        return value
    if "array" in types:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            parts = [p.strip() for p in re.split(r",|;|/|\band\b", value) if p.strip()]
            notes.append(f"{key}: '{value}' -> {parts}")
            return parts
        return [value]
    if "string" in types:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, list):
            return ", ".join(map(str, value))
    return value


def coerce_arguments(raw: Any, schema: dict) -> Coercion:
    notes: list[str] = []
    args = raw
    if isinstance(raw, str):
        args = _loads_lenient(raw)
        if args is None:
            notes.append("arguments were not valid JSON")
            args = {}
    if not isinstance(args, dict):
        args = {}
    props: dict = schema.get("properties", {})
    if len(args) == 1 and next(iter(args)) in ("parameters", "arguments", "args", "input", "kwargs") \
            and isinstance(next(iter(args.values())), dict):
        args = next(iter(args.values()))
        notes.append("unwrapped nested arguments")
    out: dict = {}
    for key, value in args.items():
        target = key if key in props else _match_key(key, props, set(out) | set(args))
        if target is None:
            notes.append(f"dropped unknown argument '{key}'")
            continue
        if target != key:
            notes.append(f"renamed '{key}' -> '{target}'")
        coerced = _coerce_value(value, props[target], target, notes)
        if coerced is _DROP:
            if value not in (None, ""):
                notes.append(f"dropped placeholder {target}={value!r}")
            continue
        out[target] = coerced
    missing = [r for r in schema.get("required", []) if r not in out]
    return Coercion(out, notes, missing)


# ======================================================================================
# 4. Provenance: values the guest must have provided
# ======================================================================================
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "sri", "prof"}
_MONTHS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_PHRASE = re.compile(
    r"\b(day after tomorrow|today|tonight|tomorrow|tmrw|tmr|kal|aaj|parso|parson|naale|nale|ivattu|"
    r"(?:this |next |coming )?weekend|"
    r"(?:this |next |coming )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|"
    r"thurs|fri|sat|sun|somvar|mangalvar|budhvar|guruvar|shukravar|shanivar|ravivar)|"
    rf"in \d{{1,2}} days?|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})[a-z]*|(?:{_MONTHS})[a-z]*\s+\d{{1,2}}(?:st|nd|rd|th)?|"
    r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})\b", re.I)
_DEVANAGARI_DATES = ("आज", "कल", "परसों")
TIME_PHRASE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.|baje|o'?clock|hrs)|\d{1,2}[:.]\d{2}|at \d{1,2}|noon|lunch|"
    r"dinner|breakfast|brunch|tonight|evening|night|raat|dopahar|shaam|midnight)\b", re.I)
COUNT_WORDS = {
    1: ["solo", "alone", "just me", "myself", "one", "ek"], 2: ["two", "couple", "both of us", "date night", "do log"],
    3: ["three", "teen"], 4: ["four", "char", "chaar"], 5: ["five", "paanch", "panch"], 6: ["six", "chhe"],
    7: ["seven", "saat"], 8: ["eight", "aath"], 10: ["ten", "das"], 12: ["twelve", "dozen"], 20: ["twenty"],
}


def find_date_phrases(text: str) -> list[str]:
    found = [m.group(0) for m in DATE_PHRASE.finditer(text or "")]
    found += [w for w in _DEVANAGARI_DATES if w in (text or "")]
    return list(dict.fromkeys(found))


def is_grounded(arg: str, value: Any, corpus: str, corpus_digits: list[str], *, kind: str | None = None,
                user_corpus: str | None = None) -> bool:
    """True if ``value`` can be traced to the conversation.

    Names and phone numbers may come from the guest *or* a tool result (e.g. a booking lookup).
    Dates, times and head-counts must come from the guest's own words (``user_corpus``).
    """
    if value in (None, ""):
        return True
    text = str(value)
    kind = kind or ("phone" if arg == "phone" else "name")
    user = (user_corpus if user_corpus is not None else corpus).lower()
    if kind == "phone":
        digits = re.sub(r"\D", "", text)[-10:]
        return len(digits) >= 7 and any(digits in chunk for chunk in corpus_digits)
    if kind == "date":
        return bool(find_date_phrases(user)) or text.lower() in user
    if kind == "time":
        return bool(TIME_PHRASE.search(user)) or text.lower() in user
    if kind == "count":
        try:
            n = int(float(text))
        except ValueError:
            return False
        # "for 4." counts; "4.7 stars" and "14" don't; "₹1,500" counts as 1500
        user = re.sub(r"(?<=\d),(?=\d{2,3}\b)", "", user)
        return bool(re.search(rf"(?<!\d)(?<!\d[.,:]){n}(?!\d)(?![.,:]\d)", user)) or any(
            re.search(rf"\b{re.escape(w)}\b", user) for w in COUNT_WORDS.get(n, []))
    tokens = [t for t in re.findall(r"[^\W\d_]+", text.lower()) if len(t) >= 2 and t not in _HONORIFICS]
    return all(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", corpus) for t in tokens)


def date_note(text: str, today) -> str | None:
    """Temporal grounding: resolve the guest's date words deterministically, so the model doesn't have to."""
    from goodfoods.core.errors import DomainError
    from goodfoods.core.timeparse import fmt_date, parse_date

    resolved = []
    for phrase in find_date_phrases(text):
        try:
            d = parse_date(phrase, today)
        except DomainError:
            continue
        resolved.append(f"'{phrase}' = {fmt_date(d)} ({d.isoformat()})")
    return "; ".join(resolved) or None


_AMBIGUOUS = re.compile(r"'(.+?)' matches several [^:]+: (.+?)\.(?: Hint|\s*$)", re.S)


def resolve_ambiguity(error_text: str, args: dict, guest_text: str) -> dict | None:
    """If a tool says a reference matches several candidates and the guest's own words single one out, use it.

    Small models often drop a qualifier the guest gave ("Ember & Oak" for "Ember & Oak in Whitefield"). The
    candidates are listed as ``Name (id), Name (id)``; a candidate wins only if the words unique to it all appear in
    the conversation and no other candidate qualifies.
    """
    m = _AMBIGUOUS.search(error_text or "")
    if not m:
        return None
    ref, options = m.group(1), re.findall(r"\s*(.+?) \(([\w-]+)\),?", m.group(2))
    key = next((k for k, v in args.items() if str(v) == ref), None)
    if not key or len(options) < 2:
        return None
    words = [set(re.findall(r"[a-z0-9]+", name.lower())) for name, _ in options]
    shared = set.intersection(*words)
    said = set(re.findall(r"[a-z0-9]+", guest_text.lower()))
    winners = [cid for (_, cid), w in zip(options, words, strict=True) if (w - shared) and (w - shared) <= said]
    return {**args, key: winners[0]} if len(winners) == 1 else None


# ======================================================================================
# 5. Policy engine (rules declared by the MCP server in tool _meta)
# ======================================================================================
@dataclass
class PolicyState:
    produced: dict[tuple[str, str], int] = field(default_factory=dict)  # (tool, id) -> turn it was created
    previews: dict[tuple[str, str], int] = field(default_factory=dict)  # (tool, key) -> turn it was previewed


@dataclass
class Decision:
    action: str  # allow | modify | block
    args: dict
    message: str = ""


def _key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


class PolicyEngine:
    def __init__(self, specs: dict[str, ToolSpec]) -> None:
        self.specs = specs

    def check(self, tool: str, args: dict, *, state: PolicyState, turn: int, corpus: str,
              corpus_digits: list[str], user_corpus: str | None = None) -> Decision:
        policy = self.specs[tool].policy

        def ungrounded(rule) -> list[str]:
            kinds = rule if isinstance(rule, dict) else {a: None for a in rule}
            return [a for a, kind in kinds.items() if a in args and not is_grounded(
                a, args[a], corpus, corpus_digits, kind=kind, user_corpus=user_corpus)]

        missing = ungrounded(policy.get("grounded_args", []))
        if missing:
            label = " and ".join(m.replace("_", " ") for m in missing)
            values = ", ".join(f"{m}={args[m]!r}" for m in missing)
            return Decision("block", args, (
                f"POLICY BLOCK: the guest never gave {label} ({values}), so it may be invented. "
                f"Do not guess: ask the guest for their {label}."))
        dropped = ungrounded(policy.get("grounded_optional", {}))
        if dropped:
            args = {k: v for k, v in args.items() if k not in dropped}
            return Decision("modify", args, f"Dropped details the guest hasn't given: {', '.join(dropped)}.")
        rule = policy.get("requires_prior_turn")
        if rule:
            created = state.produced.get((rule["tool"], _key(args.get(rule["arg"]))))
            if created is not None and created >= turn:
                return Decision("block", args, (
                    "POLICY BLOCK: the guest has not confirmed yet. You created this hold in this same turn. Show the "
                    "booking summary and ask 'Shall I confirm?'. Call confirm_reservation only after the guest says "
                    "yes in their next message."))
        rule = policy.get("two_phase")
        if rule and args.get(rule["flag"]) is True:
            previewed = state.previews.get((tool, _key(args.get(rule["key"]))))
            if previewed is None or previewed >= turn:
                return Decision("modify", {**args, rule["flag"]: False}, (
                    f"{tool} switched to preview mode: the guest must see and approve the change first."))
        return Decision("allow", args)

    def record(self, tool: str, args: dict, structured: dict | None, *, state: PolicyState, turn: int) -> None:
        if not structured:
            return
        for spec in self.specs.values():
            rule = spec.policy.get("requires_prior_turn")
            if rule and rule["tool"] == tool and structured.get(rule["field"]):
                state.produced[(tool, _key(structured[rule["field"]]))] = turn
        rule = self.specs[tool].policy.get("two_phase")
        if rule and structured.get("status") == "preview":
            state.previews[(tool, _key(args.get(rule["key"])))] = turn
        # A lookup that showed the guest the exact booking can count as the preview (e.g. for cancelling).
        for name, spec in self.specs.items():
            rule = spec.policy.get("two_phase") or {}
            if tool in rule.get("also_satisfied_by", []):
                for booking in structured.get("bookings", []):
                    if booking.get(rule["key"]):
                        state.previews.setdefault((name, _key(booking[rule["key"]])), turn)


# ======================================================================================
# 6. Final-reply verification
# ======================================================================================
CODE_RE = re.compile(r"\bGF-[A-Z0-9]{5}\b")
HOLD_RE = re.compile(r"\bHOLD-\d{4}\b")
CLAIM_RE = re.compile(
    r"\b(your|the)\s+(booking|reservation|table|cancellation)\b[^.!?\n]{0,40}?\b(is|has been|was)\s+"
    r"(now\s+|successfully\s+|officially\s+)?(confirmed|booked|cancell?ed)\b"
    r"|\bbooking\s+confirmed\b|\byou(?:'re| are) all set\b|\bsuccessfully\s+(booked|confirmed|cancell?ed)\b"
    r"|\bI(?:'ve| have)\s+(booked|confirmed|cancell?ed)\b|\b(confirm|book|cancel)\s+ho\s+gay[ai]\b",
    re.I,
)


_GENERIC_WORDS = {"booking", "booked", "restaurant", "restaurants", "date", "time", "party", "size", "guest", "guests",
                  "phone", "mobile", "summary", "confirm", "confirmed", "update", "updated", "option", "options", "note",
                  "table", "tables", "dinner", "lunch", "breakfast", "brunch", "waitlist", "available", "full", "tonight",
                  "tomorrow", "today", "people", "code", "hold", "held", "price", "rating", "cuisine", "area"}

ASK_CONFIRM_RE = re.compile(
    r"\b(shall|should|can|may) i (confirm|book|finali[sz]e|lock)\b|\bwant me to (confirm|book)\b"
    r"|\bconfirm (your|the|this) (booking|reservation)\?", re.I)


@dataclass
class ToolOutcome:
    name: str
    ok: bool
    status: str | None


def verify_reply(reply: str, turn_outcomes: list[ToolOutcome], tool_corpus: str, guest_text: str = "") -> str | None:
    """Return a correction for the model, or None if the reply is grounded in tool results.

    A code the guest typed themselves may be repeated back (``guest_text``); anything else must come from a tool.
    """
    problems: list[str] = []
    said = guest_text.upper()
    for code in sorted(set(CODE_RE.findall(reply))):
        if code not in tool_corpus and code not in said:
            problems.append(f"The code {code} does not appear in any tool result; never invent booking codes.")
    for hold in sorted(set(HOLD_RE.findall(reply))):
        if hold not in tool_corpus:
            problems.append(f"The hold id {hold} does not appear in any tool result.")
    pending = any(o.ok and o.status in ("held", "preview") for o in turn_outcomes)
    if ASK_CONFIRM_RE.search(reply) and not pending and not any(o.ok and o.status in (
            "confirmed", "modified", "cancelled") for o in turn_outcomes):
        problems.append("You asked the guest to confirm a booking, but nothing is on hold yet. If you know the "
                        "restaurant, date, time, party size, name and mobile, call hold_table now; its summary is "
                        "what the guest confirms. Otherwise ask only for the missing details.")
    if CLAIM_RE.search(reply):
        finalized = any(o.ok and o.status in ("confirmed", "modified", "cancelled", "already_cancelled")
                        for o in turn_outcomes)
        pending = any(o.ok and o.status in ("held", "preview") for o in turn_outcomes)
        if not finalized and pending:
            problems.append("Your reply says it is confirmed/done, but it is only on HOLD or in PREVIEW. Nothing is "
                            "final until the guest agrees: summarise it and ask them to confirm.")
        elif not finalized and '"status":"confirmed"' not in tool_corpus and '"status":"cancelled"' not in tool_corpus:
            problems.append("Your reply claims a booking was confirmed/cancelled, but no tool did that. Only state "
                            "what tool results show.")
    # Venue names the model bolds must come from a tool result (catches invented restaurants). Conservative: a bold
    # label is flagged only if *none* of its distinctive words appear in any tool result.
    corpus_lower = tool_corpus.lower().replace("_", " ")  # "valet_parking" should match "Valet parking"
    for raw in re.findall(r"\*\*([^*\n]{3,60})\*\*", reply):
        label = raw.strip()
        if label.endswith(":") or len(label.split()) > 7:
            continue
        words = [w for w in re.findall(r"[a-z]+", label.lower()) if len(w) >= 4 and w not in _GENERIC_WORDS]
        if words and not any(re.search(rf"\b{re.escape(w)}\b", corpus_lower) for w in words):
            problems.append(f"'{label}' does not come from any tool result. Only suggest restaurants returned by "
                            "search_restaurants; never invent names.")
            break
    return " ".join(problems) or None
