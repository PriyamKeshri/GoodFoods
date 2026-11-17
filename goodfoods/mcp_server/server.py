"""GoodFoods MCP server: the reservation domain exposed as MCP tools, resources and prompts.

Any MCP host can use it (our agent, desktop AI assistants, MCP Inspector, other agents).

    python -m goodfoods.mcp_server                 # stdio (default)
    python -m goodfoods.mcp_server --http --port 8765   # streamable HTTP

Tool descriptions are written for small models: one job per tool, imperative wording, explicit
"call this only when" guidance, and tolerant inputs (dates like "tomorrow" are fine).

Agent-side policies are declared as tool metadata (``_meta["goodfoods/policy"]``) rather than
hard-coded in the agent, which keeps the agent domain-agnostic:

* ``two_phase``       state change must be previewed in an earlier user turn before ``flag=true``
* ``requires_prior_turn``  the referenced id must come from a tool result of an earlier user turn
* ``grounded_args``   argument values must appear in the conversation (anti-hallucination for PII)
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from goodfoods import __version__, config
from goodfoods.core.catalog import DIETARY, FEATURES, OCCASIONS, get_catalog
from goodfoods.core.errors import DomainError
from goodfoods.core.seed import ensure_database
from goodfoods.core.service import CANCELLATION_POLICY, ReservationService
from goodfoods.core.store import Store

POLICY_KEY = "goodfoods/policy"
SESSION_META_KEY = "goodfoods/session"

mcp = MCPServer(
    name="goodfoods-reservations",
    title="GoodFoods Reservations",
    version=__version__,
    instructions=(
        "Reservation system for GoodFoods, a Bengaluru restaurant group (76 restaurants). Search & recommend venues, "
        "check live table availability, hold/confirm bookings, modify/cancel them, manage the waitlist and hand off "
        "to humans. Read the 'concierge_brief' prompt for the booking flow and policies."
    ),
)

_service: ReservationService | None = None


def service() -> ReservationService:
    global _service
    if _service is None:
        ensure_database(config.DB_PATH)
        _service = ReservationService(Store(config.DB_PATH))
    return _service


def _session_id(ctx: Context | None) -> str | None:
    """Conversation id the client may attach as request ``_meta`` (used for demand analytics)."""
    try:
        meta = ctx.request_context.meta if ctx else None
    except Exception:  # noqa: BLE001 - context is best-effort metadata
        return None
    if meta is None:
        return None
    if isinstance(meta, dict):
        return meta.get(SESSION_META_KEY)
    data = {**(getattr(meta, "model_extra", None) or {})}
    if hasattr(meta, "model_dump"):
        data.update(meta.model_dump(by_alias=True, exclude_none=True))
    return data.get(SESSION_META_KEY)


def _result(payload: dict[str, Any]) -> CallToolResult:
    """Compact JSON text for the model + the same data as structuredContent for UIs."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=payload)


def _call(fn, /, **kwargs) -> CallToolResult:
    try:
        return _result(fn(**kwargs))
    except DomainError as exc:
        raise ToolError(str(exc)) from exc


READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

# ======================================================================================
# Tools (descriptions are prompts: short, imperative, "when to call", tolerant inputs)
# ======================================================================================
RESTAURANT = Field(description="Restaurant id from search results (or exact name).")
DATE = Field(description="As the guest said it, e.g. 'tomorrow', 'friday', '2026-11-20'.")
TIME = Field(description="As the guest said it, e.g. '8pm', '19:30'.")
PARTY = Field(description="Number of guests.")
NAME = Field(description="Guest's name, exactly as they gave it.")
PHONE = Field(description="Guest's 10-digit mobile, exactly as they gave it.")
CODE = Field(description="Booking code, e.g. 'GF-7KQ2X'.")

# Agent policies (enforced by the client's policy engine, invisible to the LLM). "kind" tells the agent how to
# check that a value came from the guest: names/phones may also come from tool results; dates, times and
# head-counts must come from the guest's own words.
SLOT_FROM_GUEST = {"date": "date", "time": "time", "party_size": "count"}
CONTACT_FROM_GUEST = {"guest_name": "name", "phone": "phone"}


@mcp.tool(
    title="Search & recommend restaurants",
    description="Recommend GoodFoods restaurants by cuisine, area, occasion, budget, diet or features. Add date, time "
                "and party_size when known to get live availability. Returns up to 3 ranked options with reasons.",
    annotations=READ_ONLY,
    meta={POLICY_KEY: {"grounded_optional": {**SLOT_FROM_GUEST, "budget_for_two": "count"}}},
)
def search_restaurants(
    cuisine: Annotated[str | None, Field(description="Cuisine or dish, e.g. 'Italian', 'biryani'.")] = None,
    area: Annotated[str | None, Field(description="Bengaluru neighbourhood, e.g. 'Indiranagar'.")] = None,
    date: Annotated[str | None, DATE] = None,
    time: Annotated[str | None, TIME] = None,
    party_size: Annotated[int | None, PARTY] = None,
    budget_for_two: Annotated[int | None, Field(description="Max spend for two, in rupees.")] = None,
    dietary: Annotated[list[str] | None, Field(description=f"Any of: {', '.join(DIETARY)}.")] = None,
    features: Annotated[list[str] | None, Field(description=f"Any of: {', '.join(FEATURES)}.")] = None,
    occasion: Annotated[str | None, Field(description=f"One of: {', '.join(OCCASIONS)}.")] = None,
    query: Annotated[str | None, Field(description="Any other wish, free text.")] = None,
    ctx: Context | None = None,
) -> CallToolResult:
    return _call(service().search_restaurants, cuisine=cuisine, area=area, date=date, time=time,
                 party_size=party_size, budget_for_two=budget_for_two, dietary=dietary, features=features,
                 occasion=occasion, query=query, session_id=_session_id(ctx))


@mcp.tool(
    title="Restaurant details",
    description="Details of ONE restaurant: address, hours, features, diet options, dishes, private dining.",
    annotations=READ_ONLY,
)
def get_restaurant_details(restaurant: Annotated[str, RESTAURANT]) -> CallToolResult:
    return _call(service().get_restaurant_details, restaurant=restaurant)


@mcp.tool(
    title="Check table availability",
    description="Check if one restaurant has a table at a date and time for a party size. If full, returns other "
                "times, nearby sister restaurants with space, and the waitlist option.",
    annotations=READ_ONLY,
    meta={POLICY_KEY: {"grounded_args": SLOT_FROM_GUEST}},
)
def check_availability(
    restaurant: Annotated[str, RESTAURANT],
    date: Annotated[str, DATE],
    time: Annotated[str, TIME],
    party_size: Annotated[int, PARTY],
    ctx: Context | None = None,
) -> CallToolResult:
    return _call(service().check_availability, restaurant=restaurant, date=date, time=time,
                 party_size=party_size, session_id=_session_id(ctx))


@mcp.tool(
    title="Hold a table (booking step 1)",
    description="Booking step 1: hold a table for 10 minutes. Call it as soon as you know restaurant, date, time, "
                "party_size, guest_name and phone, all given by the guest (never guess). Does NOT confirm: then show "
                "its summary and ask the guest to confirm.",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
    meta={POLICY_KEY: {"grounded_args": {**CONTACT_FROM_GUEST, **SLOT_FROM_GUEST}}},
)
def hold_table(
    restaurant: Annotated[str, RESTAURANT],
    date: Annotated[str, DATE],
    time: Annotated[str, TIME],
    party_size: Annotated[int, PARTY],
    guest_name: Annotated[str, NAME],
    phone: Annotated[str, PHONE],
    occasion: Annotated[str | None, Field(description="Optional, e.g. 'birthday'.")] = None,
    special_requests: Annotated[str | None, Field(description="Optional, e.g. allergies, high chair.")] = None,
    ctx: Context | None = None,
) -> CallToolResult:
    return _call(service().hold_table, restaurant=restaurant, date=date, time=time, party_size=party_size,
                 guest_name=guest_name, phone=phone, occasion=occasion, special_requests=special_requests,
                 session_id=_session_id(ctx))


@mcp.tool(
    title="Confirm a held table (booking step 2)",
    description="Booking step 2: confirm a held table and get the confirmation code. Call ONLY after the guest saw "
                "the hold summary and said yes.",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
    meta={POLICY_KEY: {"requires_prior_turn": {"arg": "hold_id", "tool": "hold_table", "field": "hold_id"}}},
)
def confirm_reservation(
    hold_id: Annotated[str, Field(description="hold_id from hold_table, e.g. 'HOLD-1234'.")],
) -> CallToolResult:
    return _call(service().confirm_reservation, hold_id=hold_id)


@mcp.tool(
    title="Look up bookings",
    description="Look up bookings by confirmation code (GF-XXXXX) or by the guest's mobile number.",
    annotations=READ_ONLY,
)
def get_reservation(
    confirmation_code: Annotated[str | None, CODE] = None,
    phone: Annotated[str | None, Field(description="Mobile number used for the booking.")] = None,
) -> CallToolResult:
    return _call(service().get_reservation, confirmation_code=confirmation_code, phone=phone)


@mcp.tool(
    title="Change a booking",
    description="Change a booking's date, time, party size or requests. confirm=false previews (nothing changes); "
                "use confirm=true only after the guest agrees.",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False),
    meta={POLICY_KEY: {"two_phase": {"flag": "confirm", "key": "confirmation_code",
                                     "also_satisfied_by": ["get_reservation"]}}},
)
def modify_reservation(
    confirmation_code: Annotated[str, CODE],
    date: Annotated[str | None, Field(description="New date, if changing.")] = None,
    time: Annotated[str | None, Field(description="New time, if changing.")] = None,
    party_size: Annotated[int | None, Field(description="New number of guests, if changing.")] = None,
    special_requests: Annotated[str | None, Field(description="New special requests.")] = None,
    confirm: Annotated[bool, Field(description="false = preview, true = apply.")] = False,
) -> CallToolResult:
    return _call(service().modify_reservation, confirmation_code=confirmation_code, date=date, time=time,
                 party_size=party_size, special_requests=special_requests, confirm=confirm)


@mcp.tool(
    title="Cancel a booking",
    description="Cancel a booking. confirm=false previews (nothing is cancelled); use confirm=true only after the "
                "guest agrees.",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False),
    meta={POLICY_KEY: {"two_phase": {"flag": "confirm", "key": "confirmation_code",
                                     "also_satisfied_by": ["get_reservation"]}}},
)
def cancel_reservation(
    confirmation_code: Annotated[str, CODE],
    confirm: Annotated[bool, Field(description="false = preview, true = cancel.")] = False,
    reason: Annotated[str | None, Field(description="Optional reason.")] = None,
) -> CallToolResult:
    return _call(service().cancel_reservation, confirmation_code=confirmation_code, confirm=confirm, reason=reason)


@mcp.tool(
    title="Join the waitlist",
    description="Add the guest to the waitlist for a full slot. They get a WhatsApp message if a table frees up.",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False),
    meta={POLICY_KEY: {"grounded_args": {**CONTACT_FROM_GUEST, **SLOT_FROM_GUEST}}},
)
def join_waitlist(
    restaurant: Annotated[str, RESTAURANT],
    date: Annotated[str, DATE],
    time: Annotated[str, TIME],
    party_size: Annotated[int, PARTY],
    guest_name: Annotated[str, NAME],
    phone: Annotated[str, PHONE],
) -> CallToolResult:
    return _call(service().join_waitlist, restaurant=restaurant, date=date, time=time, party_size=party_size,
                 guest_name=guest_name, phone=phone)


@mcp.tool(
    title="Request a human call back",
    description="Hand off to a human: events over 20 guests, complaints, special arrangements, or anything you can't "
                "handle.",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
    meta={POLICY_KEY: {"grounded_args": CONTACT_FROM_GUEST}},
)
def request_callback(
    reason: Annotated[str, Field(description="large_event, special_request, complaint, accessibility or other.")],
    guest_name: Annotated[str, NAME],
    phone: Annotated[str, PHONE],
    details: Annotated[str, Field(description="What the guest needs.")],
    restaurant: Annotated[str | None, Field(description="Restaurant id, if relevant.")] = None,
    date: Annotated[str | None, Field(description="Date, if relevant.")] = None,
    party_size: Annotated[int | None, Field(description="Number of guests, if relevant.")] = None,
) -> CallToolResult:
    return _call(service().request_callback, reason=reason, guest_name=guest_name, phone=phone, details=details,
                 restaurant=restaurant, date=date, party_size=party_size)


# ======================================================================================
# Resources (knowledge base) and prompts
# ======================================================================================
POLICIES_MD = f"""# GoodFoods booking policies
- Online bookings: up to {config.BOOKING_WINDOW_DAYS} days ahead, parties of 1-{config.MAX_ONLINE_PARTY}. Larger events: events team call back.
- A held table is kept for {config.HOLD_MINUTES} minutes while the guest confirms.
- {CANCELLATION_POLICY}
- No deposits or card details are ever taken in chat.
- Children are welcome everywhere; high chairs on request.
- Allergies: we note them on the booking, but kitchens handle common allergens, so allergen-free food can't be guaranteed.
- Dress code: smart casual at Saffron Terrace and Sakura Street; casual elsewhere.
- Wheelchair access varies by venue: check the restaurant's features.
"""


@mcp.resource("goodfoods://policies", name="policies", title="Booking policies", mime_type="text/markdown",
              description="House rules: booking window, holds, cancellations, allergies, dress code.")
def policies() -> str:
    return POLICIES_MD


@mcp.resource("goodfoods://areas", name="areas", title="Neighbourhoods served", mime_type="application/json",
              description="Bengaluru neighbourhoods with GoodFoods restaurants and how many in each.")
def areas() -> str:
    cat = get_catalog()
    counts: dict[str, int] = {}
    for r in cat.restaurants:
        counts[r.area] = counts.get(r.area, 0) + 1
    return json.dumps(dict(sorted(counts.items(), key=lambda kv: -kv[1])))


@mcp.resource("goodfoods://cuisines", name="cuisines", title="Cuisines & concepts", mime_type="application/json",
              description="Every GoodFoods dining concept with its cuisine, price band and locations.")
def cuisines() -> str:
    cat = get_catalog()
    brands: dict[str, dict] = {}
    for r in cat.restaurants:
        b = brands.setdefault(r.brand, {"cuisine": r.cuisine, "price_band": "₹" * r.price_band, "locations": []})
        b["locations"].append(r.area)
    return json.dumps(brands, ensure_ascii=False)


@mcp.resource("goodfoods://restaurants/{restaurant_id}", name="restaurant", title="Restaurant profile",
              mime_type="application/json", description="Full profile of one restaurant.")
def restaurant_profile(restaurant_id: str) -> str:
    r = get_catalog().resolve_restaurant(restaurant_id)
    return json.dumps(r.details(), ensure_ascii=False)


def build_brief() -> str:
    cat = get_catalog()
    return f"""## About {cat.brand_name}
{cat.brand_name} is a {cat.city} restaurant group: {len(cat.restaurants)} restaurants, {len(cat.brands)} dining concepts, {len(cat.areas)} neighbourhoods.
Cuisines: {", ".join(cat.cuisines)}.
Neighbourhoods: {", ".join(cat.areas)}.

## Booking flow
1. Understand the request. Use search_restaurants for suggestions; check_availability for one specific restaurant.
2. Before holding, you need: restaurant, date, time, party size, the guest's name and 10-digit mobile number.
3. The moment you have all six, call hold_table (no need to ask first). Then show its summary and ask "Shall I confirm?". Nothing is booked yet.
4. Only when the guest says yes: confirm_reservation(hold_id), then give them the GF- confirmation code.
- Slot full? Offer the alternatives the tool returns: other times, sister restaurants nearby, or the waitlist.
- Existing bookings: the GF- code alone is enough for get_reservation; never ask for anything else first.
- Changes or cancellations: get_reservation, then modify_reservation / cancel_reservation (preview first, then confirm=true after the guest agrees).
- More than {config.MAX_ONLINE_PARTY} guests, complaints or special arrangements: request_callback.

## Policies
- Bookings up to {config.BOOKING_WINDOW_DAYS} days ahead; parties of 1-{config.MAX_ONLINE_PARTY} online.
- Holds last {config.HOLD_MINUTES} minutes. {CANCELLATION_POLICY}
- Never take payment or card details. No deposits are needed.
- Allergies go in special_requests; tell the guest to also inform their server."""


@mcp.prompt(name="concierge_brief", title="Concierge domain brief",
            description="Domain knowledge, booking flow and policies for a reservation agent's system prompt.")
def concierge_brief() -> str:
    return build_brief()
