"""The concierge agent: a hand-written tool-calling loop over an MCP server.

Per user turn:

    user msg ─► LLM (decides intent: reply, or call tools) ─► tool calls? ──no──► verify reply ─► done
                      ▲                                           │yes                │rejected (once)
                      │                                           ▼                   ▼
                      └──── tool results ◄── MCP tools/call ◄── guardrails      correction note
                                                               (repair, policy,
                                                                provenance, dedupe)

The LLM alone decides *what* to do (intent, tool choice, arguments). Code only repairs
malformed calls and enforces declared policies, always telling the model why.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from goodfoods import config
from goodfoods.agent.guardrails import (
    PolicyEngine,
    PolicyState,
    ToolOutcome,
    coerce_arguments,
    date_note,
    recover_text_tool_calls,
    repair_tool_name,
    resolve_ambiguity,
    verify_reply,
)
from goodfoods.agent.llm import LLMClient, LLMError
from goodfoods.agent.mcp_client import MCPError, MCPStdioClient
from goodfoods.agent.prompts import build_system_prompt
from goodfoods.agent.telemetry import Telemetry

WITHHELD_REPLY = ("I apologise, I was unable to verify that step, so nothing has been finalised yet. "
                  "Could you please tell me how you would like to proceed?")
FALLBACK_REPLY = "Sorry, I didn't quite catch that. Could you tell me a little more about what you're looking for?"


@dataclass
class TraceEvent:
    kind: str  # llm | tool | guardrail | error
    title: str
    detail: dict = field(default_factory=dict)
    ms: float = 0.0


@dataclass
class ToolRun:
    name: str
    args: dict
    ok: bool
    text: str
    structured: dict | None = None
    ms: float = 0.0
    blocked: bool = False


@dataclass
class TurnResult:
    reply: str
    events: list[TraceEvent]
    tools: list[ToolRun]
    llm_calls: int
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    error: str | None = None

    @property
    def guardrail_events(self) -> list[TraceEvent]:
        return [e for e in self.events if e.kind == "guardrail"]


class ConciergeAgent:
    def __init__(self, mcp: MCPStdioClient, llm: LLMClient, *, guardrails: bool = True, session_id: str | None = None,
                 max_steps: int = 6, telemetry: Telemetry | None = None,
                 clock: Callable[[], datetime] = config.now) -> None:
        self.mcp = mcp
        self.llm = llm
        self.guardrails = guardrails
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.max_steps = max_steps
        self.telemetry = telemetry
        self.clock = clock
        self.specs = {t.name: t for t in mcp.list_tools()}
        self.tool_schemas = [t.to_openai() for t in self.specs.values()]
        self.brief = mcp.get_prompt("concierge_brief")
        self.policy = PolicyEngine(self.specs)
        self.policy_state = PolicyState()
        self.history: list[dict] = []
        self.turn = 0

    # ------------------------------------------------------------------ prompt & context
    def system_prompt(self) -> str:
        return build_system_prompt(brief=self.brief, now=self.clock())

    def _messages(self, scratch: list[dict]) -> list[dict]:
        """System prompt + history (old tool payloads trimmed to save context) + per-turn scratch."""
        user_idx = [i for i, m in enumerate(self.history) if m["role"] == "user"]
        cutoff = user_idx[-3] if len(user_idx) >= 3 else 0
        msgs = []
        for i, m in enumerate(self.history):
            if i < cutoff and m["role"] == "tool" and len(m["content"]) > 400:
                m = {**m, "content": m["content"][:400] + "…(older result trimmed)"}
            msgs.append(m)
        return [{"role": "system", "content": self.system_prompt()}, *msgs, *scratch]

    def _corpus(self) -> tuple[str, list[str], str]:
        """(guest + tool text, digit runs per message, guest-only text): what arguments may be grounded in."""
        texts = [m["content"] for m in self.history if m["role"] in ("user", "tool") and m.get("content")]
        # the guest's own words only: our calendar notes must not "ground" a party of 17 or a 2026 phone digit run
        user = "\n".join(m["content"].split("\n\n[Calendar note:")[0] for m in self.history if m["role"] == "user")
        return "\n".join(texts).lower(), [re.sub(r"\D", "", t) for t in texts], user.lower()

    def _tool_corpus(self) -> str:
        return "\n".join(m["content"] for m in self.history if m["role"] == "tool")

    def reset(self) -> None:
        self.history.clear()
        self.policy_state = PolicyState()
        self.turn = 0
        self.session_id = uuid.uuid4().hex[:12]

    # ------------------------------------------------------------------ the loop
    def chat(self, user_text: str, on_event: Callable[[TraceEvent], None] | None = None) -> TurnResult:
        self.turn += 1
        turn = self.turn
        started = time.perf_counter()
        events: list[TraceEvent] = []
        runs: list[ToolRun] = []
        outcomes: list[ToolOutcome] = []
        cache: dict[str, ToolRun] = {}
        scratch: list[dict] = []
        retried = False
        stats = {"llm": 0, "pt": 0, "ct": 0}
        error: str | None = None

        def emit(event: TraceEvent) -> None:
            events.append(event)
            if on_event:
                on_event(event)

        def call_llm(messages: list[dict], tools: list[dict] | None) -> tuple[str, list[tuple[str, str, str]]]:
            resp = self.llm.chat(messages, tools)
            stats["llm"] += 1
            stats["pt"] += resp.prompt_tokens
            stats["ct"] += resp.completion_tokens
            emit(TraceEvent("llm", f"LLM call #{stats['llm']}", {
                "decision": [c.name for c in resp.tool_calls] or "reply",
                "prompt_tokens": resp.prompt_tokens, "completion_tokens": resp.completion_tokens,
                "text": resp.content[:400]}, resp.latency_ms))
            return resp.content, [(c.id, c.name, c.arguments) for c in resp.tool_calls]

        content = user_text
        if self.guardrails:  # temporal grounding: resolve "this saturday" etc. so the 8B model doesn't do date maths
            note = date_note(user_text, self.clock().date())
            if note:
                content = f"{user_text}\n\n[Calendar note: {note}]"
                emit(TraceEvent("guardrail", "Resolved dates for the model", {"note": note}))
        self.history.append({"role": "user", "content": content})
        reply = ""
        try:
            for step in range(self.max_steps):
                content, calls = call_llm(self._messages(scratch), self.tool_schemas)
                if not calls and self.guardrails:
                    recovered = recover_text_tool_calls(content, set(self.specs))
                    if recovered:
                        calls = [(f"rc_{turn}_{step}_{i}", n, json.dumps(a, ensure_ascii=False))
                                 for i, (n, a) in enumerate(recovered)]
                        emit(TraceEvent("guardrail", "Recovered a tool call written as text",
                                        {"calls": [n for n, _ in recovered], "raw": content[:300]}))
                        content = ""
                if not calls:
                    reply = content.strip()
                    problem = verify_reply(reply, outcomes, self._tool_corpus(), self._corpus()[2]) \
                        if self.guardrails and reply else None
                    if problem and retried:  # fail closed: never show the guest an unverified claim
                        emit(TraceEvent("guardrail", "Reply withheld after failing verification twice",
                                        {"problem": problem, "draft": reply}))
                        reply = WITHHELD_REPLY
                    elif problem:
                        retried = True
                        emit(TraceEvent("guardrail", "Reply rejected by verifier", {"problem": problem, "draft": reply}))
                        scratch = [{"role": "assistant", "content": reply},
                                   {"role": "user", "content": f"[Automatic check, not from the guest] {problem} "
                                                               "Write your reply to the guest again, fixed."}]
                        continue
                    break

                assistant = {"role": "assistant", "content": content or "", "tool_calls": []}
                tool_msgs = []
                for call_id, name, raw_args in calls:
                    run = self._run_tool(name, raw_args, turn, cache, emit)
                    runs.append(run)
                    outcomes.append(ToolOutcome(run.name, run.ok, (run.structured or {}).get("status") if run.ok else None))
                    assistant["tool_calls"].append({"id": call_id, "type": "function", "function": {
                        "name": run.name, "arguments": json.dumps(run.args, ensure_ascii=False)}})
                    tool_msgs.append({"role": "tool", "tool_call_id": call_id, "content": run.text})
                self.history.append(assistant)
                self.history.extend(tool_msgs)
                scratch = []
            else:
                emit(TraceEvent("guardrail", "Step limit reached: forcing a final answer", {"max_steps": self.max_steps}))
                reply, _ = call_llm(self._messages([{"role": "user", "content": "[Automatic note] Stop calling tools. "
                                                     "Answer the guest now using the results you have."}]), None)
        except LLMError as exc:
            error = str(exc)
            emit(TraceEvent("error", "Language model unavailable", {"error": error}))
            reply = "Sorry, I'm having trouble reaching my language model right now. Please try again in a moment."
        except MCPError as exc:
            error = str(exc)
            emit(TraceEvent("error", "Reservation system unavailable", {"error": error}))
            reply = "Sorry, the reservation system is briefly unavailable. Please try again in a minute."

        reply = reply.strip() or FALLBACK_REPLY
        self.history.append({"role": "assistant", "content": reply})
        result = TurnResult(reply=reply, events=events, tools=runs, llm_calls=stats["llm"],
                            latency_ms=(time.perf_counter() - started) * 1000, prompt_tokens=stats["pt"],
                            completion_tokens=stats["ct"], error=error)
        if self.telemetry:
            try:
                self.telemetry.record_turn(self.session_id, turn, result, model=self.llm.profile.model,
                                           guardrails=self.guardrails)
            except Exception:  # noqa: BLE001 - telemetry must never break the conversation
                pass
        return result

    # ------------------------------------------------------------------ one tool call
    def _run_tool(self, raw_name: str, raw_args: str, turn: int, cache: dict[str, ToolRun],
                  emit: Callable[[TraceEvent], None]) -> ToolRun:
        name = raw_name
        if name not in self.specs:
            fixed = repair_tool_name(name, self.specs) if self.guardrails else None
            if not fixed:
                text = f"Error: there is no tool named '{raw_name}'. Available tools: {', '.join(self.specs)}."
                emit(TraceEvent("tool", raw_name, {"args": raw_args, "ok": False, "result": text}))
                return ToolRun(raw_name, {}, False, text)
            emit(TraceEvent("guardrail", f"Repaired tool name '{raw_name}' -> '{fixed}'"))
            name = fixed
        spec = self.specs[name]

        if self.guardrails:
            coerced = coerce_arguments(raw_args, spec.input_schema)
            args = coerced.args
            if coerced.notes:
                emit(TraceEvent("guardrail", f"Repaired arguments for {name}", {"changes": coerced.notes}))
            if coerced.missing:
                text = (f"Error: {name} needs {', '.join(coerced.missing)}. Ask the guest for it "
                        "(never guess), or get it from another tool first.")
                emit(TraceEvent("guardrail", f"Blocked {name}: missing arguments", {"missing": coerced.missing}))
                return ToolRun(name, args, False, text, blocked=True)
            corpus, digits, user_corpus = self._corpus()
            decision = self.policy.check(name, args, state=self.policy_state, turn=turn, corpus=corpus,
                                         corpus_digits=digits, user_corpus=user_corpus)
            if decision.action == "block":
                emit(TraceEvent("guardrail", f"Policy blocked {name}", {"reason": decision.message, "args": args}))
                return ToolRun(name, args, False, decision.message, blocked=True)
            if decision.action == "modify":
                emit(TraceEvent("guardrail", f"Policy adjusted {name}", {"reason": decision.message}))
                args = decision.args
            key = name + json.dumps(args, sort_keys=True)
            if key in cache:
                emit(TraceEvent("guardrail", f"Skipped duplicate {name} call"))
                prior = cache[key]
                return ToolRun(name, args, prior.ok, prior.text + "\n(Duplicate call: same result as before. Use it.)",
                               prior.structured)
        else:
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
                args = args if isinstance(args, dict) else {}
            except json.JSONDecodeError:
                args = {}
            key = ""

        result = self.mcp.call_tool(name, args, meta={"goodfoods/session": self.session_id})
        if self.guardrails and result.is_error:
            fixed = resolve_ambiguity(result.text, args, self._corpus()[2])
            if fixed:
                emit(TraceEvent("guardrail", f"Resolved an ambiguous {name} argument from the conversation",
                                {"from": args, "to": fixed}))
                args = fixed
                result = self.mcp.call_tool(name, args, meta={"goodfoods/session": self.session_id})
        run = ToolRun(name, args, not result.is_error, result.text, result.structured, result.latency_ms)
        if run.ok:
            self.policy.record(name, args, result.structured, state=self.policy_state, turn=turn)
        if key:
            cache[key] = run
        emit(TraceEvent("tool", name, {"args": args, "ok": run.ok,
                                       "result": result.structured if result.structured else result.text[:600]},
                        result.latency_ms))
        return run
