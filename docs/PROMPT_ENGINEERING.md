# Prompt engineering & reliability for an 8B model

A frontier model needs a good prompt. An **8B model** needs a good prompt, good tool design, good context
hygiene, and a safety net in code. This document covers each layer and the **experiments, run on this machine, that
shaped them**. Every change below was triggered by an observed failure and checked against the eval suite
([EVALS.md](EVALS.md)).

---

## 1. Principles

1. **The LLM decides; code repairs and guards.** Intent detection and tool choice are always the model's job;
   there are no keyword routers. Code only fixes malformed actions (wrong types, near-miss names) and blocks unsafe
   ones (unapproved bookings, invented phone numbers), and it always tells the model *why*, so the model can
   recover in its own words.
2. **Don't ask a small model to do what code does better.** Calendar arithmetic, fuzzy entity matching and table
   allocation are deterministic. Tools accept the guest's words ("this sat", "Tandor House", "8 baje") and resolve
   them on the server.
3. **Every token is a prompt.** Tool descriptions, argument descriptions, error messages and a `next_step` field
   in results are all instructions to the model, so they are written like instructions.
4. **Measure, don't vibe.** 17 scripted conversations with database-level checks, run on two models, with the
   guardrails on and off.

---

## 2. Prompt architecture: three layers

```
┌───────────────────────────────────────────────────────────────┐
│ 1. AGENT CORE (goodfoods/agent/prompts.py)                    │  same for any reservation business
│    role · 9 numbered rules · style guide                      │
├───────────────────────────────────────────────────────────────┤
│ 2. DOMAIN BRIEF (MCP prompt "concierge_brief", from server)   │  GoodFoods specifics; the business can
│    cuisines · neighbourhoods · booking flow · policies        │  change it without redeploying the agent
├───────────────────────────────────────────────────────────────┤
│ 3. CLOCK: today + a 7-day date table (hour-level, cached)     │  small models can't do date maths
└───────────────────────────────────────────────────────────────┘
   + per message: [Calendar note: 'this saturday' = Sat, 21 Nov 2026 (2026-11-21)]
```

The agent core is short, numbered and imperative. The rules small models break most often come first:

```text
# Rules
1. Facts come from tools. Restaurants, availability, prices, hold ids and confirmation codes must come from
   tool results. Never invent them.
2. Greetings, thanks, and questions about what you can do need no tool: just reply.
3. If details are missing, ask for them in ONE short question. Never guess the guest's name or phone number.
4. Give tools dates and times the way the guest said them ("tomorrow", "friday", "8pm"). In your reply, use
   the date the tool returns (e.g. "Fri, 20 Nov").
5. Use restaurant ids exactly as tools return them.
6. If a tool result has a "next_step", follow it. If a tool returns an error, read its hint: fix the input or
   ask the guest. Never repeat a failing call unchanged.
7. Bookings, changes and cancellations take two steps: first hold/preview and show the guest a short summary,
   then ask "Shall I confirm?". Finalize only after the guest says yes in a new message.
8. If a slot is full, never just say no: offer the alternatives the tool gives (another time, a sister
   restaurant nearby, or the waitlist).
9. Only help with GoodFoods restaurants and bookings. Politely decline anything else. Never ask for payment.
```

A **style section** fixes the output shape: at most 3 options, as `**Name** - cuisine, area, price, one reason`,
times like `8:00 PM`, money in `₹`, reply in the guest's language (tool arguments stay in English). Short and
formulaic is a feature: it makes replies easy to scan and easy to verify.

---

## 3. Tool design: descriptions are prompts

| Rule | Example |
|---|---|
| One job per tool, verb-first names that don't collide | `search_restaurants` vs `get_reservation`: we avoided `find_restaurants` / `find_reservation`, which an 8B model confuses |
| Say *when* to call, and when **not** to | `confirm_reservation`: "Call ONLY after the guest saw the hold summary and said yes." |
| Accept what the guest said | `date`: "As the guest said it, e.g. 'tomorrow', 'friday', '2026-11-20'." The server parses it |
| Accept ids **or** names | `restaurant`: "id from search results (or exact name)". The server fuzzy-resolves "Tandor House Koramangala" |
| Put enumerations in the description, not the schema | `dietary`: "Any of: pure_veg, vegan, jain, gluten_free, halal". Strict `enum`s turn "veg" into a hard failure; the server canonicalises synonyms |
| Results carry a `next_step` | hold → "NOT booked yet. Read this summary to the guest and ask them to confirm…" |
| Errors are instructions | `'9876543210' looks like a placeholder phone number. Hint: Never invent contact details. Ask the guest for their real mobile number.` |
| Results are compact text for the model, `structuredContent` for the UI | MCP `CallToolResult` carries both: the LLM never sees lat/lng or UI-only fields |
| Schemas are slimmed before sending | JSON-schema `title`s and `anyOf: [T, null]` wrappers are stripped (they cost tokens and confuse small models) |

Total tool payload: **10 tools ≈ 1.9k tokens** (trimmed from 2.1k).

---

## 4. Context engineering

- **Stable prefix → KV-cache reuse.** The system prompt is the cached prefix of every call, so it contains nothing
  that changes per minute (see experiment E2).
- **Old tool payloads are trimmed.** Tool results older than three guest turns are cut to 400 characters; recent ones
  stay intact.
- **A calendar note on every guest message.** Date words are resolved deterministically and appended:
  `[Calendar note: 'this saturday' = Sat, 21 Nov 2026 (2026-11-21)]` (E8).
- **Correction notes are ephemeral.** When the verifier rejects a draft, the draft and the correction live only in
  that turn's scratch context, so they never pollute the conversation history.

---

## 5. The reliability layer (code, not prompt)

| Guard | Failure it targets | Mechanism |
|---|---|---|
| Text tool-call recovery | Tool call written as JSON in the reply (Llama/Hermes formats, code fences, preambles) | Parse `{"name":…}`, `<function=…>`, `<tool_call>`; execute only if the name is a known tool |
| Tool-name repair | `search_restaurant`, `functions.hold_table` | Fuzzy match against declared tools |
| Argument coercion | `"four"`, `"2 people"`, `"veg, jain"`, `partySize`, `{"parameters": {...}}`, `"unknown"` | Schema-driven type coercion, key-synonym mapping, placeholder dropping |
| Provenance (names, phones) | Invented `John Doe` / `9876543210` | Value must appear in the guest's words or a tool result (server-declared `grounded_args`) |
| Provenance (date, time, party) | Assumed "tomorrow 8 PM for 2" | Must come from the guest's own words; **dropped** on search, **blocked** on bookings (`grounded_optional` / `grounded_args` with kinds) |
| Two-step commits | Confirming a hold in the same turn; cancelling without a preview | Server-declared `requires_prior_turn` and `two_phase`: block, or force a preview |
| Duplicate-call suppression | Loops that repeat the same call | Same tool and args within a turn return the cached result with a note |
| Reply verifier | "Your booking is confirmed" while only held; fake `GF-` codes; invented venue names | Checked against this conversation's tool results; one corrective retry, then the reply is withheld (fail closed) |
| Ambiguity resolution | A qualifier the guest gave is dropped ("Ember & Oak" for "Ember & Oak in Whitefield") | If the guest's words single out exactly one listed candidate, retry with it |
| Step limit | Tool loops | 6 steps, then a forced tool-free answer |

**The policies live in the MCP server, not the agent:**

```python
@mcp.tool(..., meta={"goodfoods/policy": {"requires_prior_turn": {"arg": "hold_id", "tool": "hold_table", "field": "hold_id"}}})
def confirm_reservation(hold_id: str): ...
```

The agent reads `_meta` from `tools/list` and enforces whatever the server declares. A salon's MCP server could
declare its own rules and reuse the same agent unchanged.

---

## 6. Experiment log

All runs: local Ollama on an Apple M4 (16 GB), Llama-3.1-8B-Instruct Q4_K_M unless noted.

| # | Observation (evidence) | Change | Result |
|---|---|---|---|
| **E1** | Raw Llama called `search_restaurants` (with an invented party of 4) for *"hello, who are you?"*. Ollama's stock template appends *"please respond with a JSON for a function call"* to the latest user message. | Rule 2: *"Greetings… need no tool: just reply."* | Greeting scenario passes on both models |
| **E2** | 20–27 s per call: the system prompt held a per-minute timestamp, so every request invalidated the KV cache | Hour-level clock in the system prompt | Prefix reuse restored |
| **E3** | Tried moving the tool schemas from the user message into the cacheable system prompt (custom Ollama template). Turns got **~2.5× faster (3–9 s)**, but Llama **stopped calling tools** and invented *"La Dolce Vita"*, *"Bella Italia"*, even *"Dhaba Estd 1986"* (a real restaurant elsewhere) | **Reverted.** Keep each model's trained tool format; the verifier now also rejects bolded venue names absent from tool results | Lesson: never trade grounding for latency |
| **E4** | Ollama runs models with a **4,096-token** context by default; prompt plus tools is already ~3k tokens, so longer chats were silently truncated | Derived local variants with `num_ctx = 12288` (same weights) | No truncation in 5+ turn conversations |
| **E5** | Qwen3-8B (raw): correct tool use, but mapped *"kal"* to today and answered Hinglish in English | Server-side Hindi date words; calendar note (E8); reply-language rule | — |
| **E6** | The model parroted a tool hint in the third person: *"share this code with the guest, Priya… she can show it…"* | Reworded `next_step` to address the guest; *"You are chatting directly with the guest: call them 'you'"* | Fixed |
| **E7** | Eval caught a **9 PM booking for a 9 AM breakfast**: the model sent `"09:00"` and the parser's "a bare 9 means dinner" heuristic made it 21:00 | Leading-zero and 4-digit times are literal 24-hour times; regression tests added | Fixed (unit-tested) |
| **E8** | The model did its own date maths: *"this Saturday"* became `2026-11-20` (a **Friday**), so it told the guest Saturday was free | **Calendar note**: date phrases in each guest message are resolved by code and appended for the model | Sold-out scenario fixed |
| **E9** | *"Book a table at Sakura Street"* → the model invented "tomorrow, 8 PM, 2 people", searched, and presented options instead of asking | `grounded_optional` on search: unsaid date, time and party size are dropped, the tool replies *"availability not checked: needs date, time and party size"*, and the model asks | Missing-details scenario fixed |
| **E10** | After the guest saw their booking (lookup) and said *"yes, cancel it"*, the two-phase rule forced a redundant second preview | A lookup that showed the exact booking satisfies the cancellation preview (`also_satisfied_by`) | Cancel scenario fixed |
| **E11** | `party_size: "4"`, `dietary: "veg, jain"`, `{"parameters": {...}}` wrappers, `location` instead of `area` | Schema-driven argument coercion | Tool-call validity up (see EVALS) |
| **E12** | Qwen3 filed an events call-back with `guest_name="you"`, `phone="you"` | The provenance guard blocked it; the model then asked for the real details on its own | Working as designed |
| **E13** | *"Does Ember & Oak **in Whitefield** have valet parking?"* → the model dropped the area, got "matches several locations… ask the guest which area", and asked for the area the guest had already given | Error hints now say: *"If the guest already named an area, use that location's id; otherwise ask."* | Prompt fix inside the error message |
| **E14** | After the guest said *"Yes"*, Llama looked the booking up by phone instead of confirming. The lookup returned the hold labelled as `confirmation_code: HOLD-2641`, and the model told the guest it was confirmed. The verifier rejected the first draft, but the second draft repeated the claim | (a) Lookups now return holds as `hold_id` with *"On hold only, NOT a booking yet… call confirm_reservation"*; (b) the agent **fails closed**: if a reply fails verification twice it is withheld and replaced by a neutral message | Unit-tested; a phantom confirmation can no longer reach the guest |
| **E15** | The verifier rejected *"Shall I cancel GF-FTK19?"* because the code had come from the guest, not a tool | Codes the guest typed may be repeated back; only codes from neither source are rejected | False positive removed |
| **E16** | Models dropped a qualifier the guest gave (*"Ember & Oak"* for *"Ember & Oak in Whitefield"*), hit *"matches several locations"*, and asked the guest again or stalled a booking | When a tool lists several candidates and the guest's own words single out exactly one, the agent retries with that candidate (`resolve_ambiguity`) | Venue questions and bookings no longer stall on a dropped area |
| **E17** | *"A table for 4"* in an availability result for a party of 3 was read as a mismatch: the model offered alternatives instead of asking for contact details | Table label reworded (*"a 4-seat table"*) and `next_step` made explicit (*"A table is free. To book it, ask the guest for their name and mobile…"*) | Tool-result wording is prompt engineering too |
| **E18** | The safety audit counted an approved cancellation as "unconfirmed" because it only recognised an explicit preview call | The audit now mirrors the product rule (E10): a lookup that showed the booking counts as the preview | Metric corrected for every configuration |

---

## 7. What we deliberately did *not* do

- **No keyword or regex intent routing.** It would score well on a demo and violates the brief: the LLM must decide.
  Our code never chooses a tool.
- **No few-shot transcripts in the prompt.** About 600 extra tokens per call on a model whose prompt processing is
  the bottleneck locally. Tool descriptions plus `next_step` hints gave the same behaviour more cheaply.
- **No JSON-mode or constrained decoding.** Native tool calling plus repair is more portable across providers
  (Ollama, Groq, OpenRouter, vLLM).
- **No bigger model as the fix.** The brief asks for an 8B-class model; the point is making a small model reliable.

---

## 8. Results

See **[EVALS.md](EVALS.md)** for the full matrix. Headline numbers are in the README.
