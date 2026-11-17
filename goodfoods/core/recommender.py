"""Explainable venue recommendations.

Pipeline: understand the request -> hard filters (with graceful, *announced* relaxation when
nothing matches) -> score -> attach live availability -> explain each pick ("why").

Business objectives are encoded in the score, not hidden: live availability dominates
(a great venue you can't get into is a bad recommendation), and a small bonus for quieter
locations nudges demand across the chain instead of piling onto one hot spot.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

from goodfoods.core.catalog import DIETARY, FEATURES, OCCASIONS, Area, Catalog, Restaurant, norm

_QUERY_STOP = {"with", "and", "the", "for", "place", "restaurant", "good", "nice", "best", "some", "want",
               "looking", "somewhere", "near", "food", "table", "dinner", "lunch", "a", "an", "in", "at", "me"}


@dataclass
class Criteria:
    area: Area | None = None
    cuisine_text: str | None = None
    cuisine_terms: set[str] = field(default_factory=set)
    dietary: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    occasion: str | None = None
    budget_for_two: int | None = None
    query_terms: list[str] = field(default_factory=list)
    day: date | None = None
    start: int | None = None
    party_size: int | None = None
    notes: list[str] = field(default_factory=list)


def build_criteria(catalog: Catalog, *, area: str | None, cuisine: str | None, dietary, features, occasion,
                   budget_for_two: int | None, query: str | None) -> Criteria:
    c = Criteria(budget_for_two=budget_for_two if budget_for_two and budget_for_two > 0 else None)
    if area:
        c.area = catalog.resolve_area(area)
        if not c.area:
            c.notes.append(f"'{area}' is not a neighbourhood we serve, so all of Bengaluru was searched. "
                           f"Served areas include: {', '.join(list(catalog.areas)[:12])}…")
    if cuisine:
        c.cuisine_terms, unknown = catalog.match_cuisine(cuisine)
        c.cuisine_text = cuisine
        if not c.cuisine_terms:
            c.notes.append(f"GoodFoods doesn't serve '{cuisine}'. Our cuisines: {', '.join(catalog.cuisines)}.")
    c.dietary, unknown_diet = Catalog.canonicalize(dietary, DIETARY)
    c.features, unknown_feat = Catalog.canonicalize(features, FEATURES)
    occ, _ = Catalog.canonicalize(occasion, OCCASIONS)
    c.occasion = occ[0] if occ else None
    for label, unknown in (("dietary", unknown_diet), ("feature", unknown_feat)):
        if unknown:
            c.notes.append(f"Ignored unknown {label} value(s): {', '.join(unknown)}.")
    if query:
        # free text can also carry features/diet ("rooftop with live music", "pure veg")
        extra_feat, _ = Catalog.canonicalize(query, FEATURES)
        extra_diet, _ = Catalog.canonicalize(query, DIETARY)
        c.features += [f for f in extra_feat if f not in c.features]
        c.dietary += [d for d in extra_diet if d not in c.dietary and d == "pure_veg"]
        c.query_terms = [w for w in norm(query).split() if len(w) >= 3 and w not in _QUERY_STOP]
    return c


def _passes(r: Restaurant, c: Criteria, catalog: Catalog, *, radius: float | None, features: bool,
            budget: bool, cuisine: bool) -> bool:
    if c.dietary and not all(d in r.dietary for d in c.dietary):
        return False  # dietary needs are never relaxed
    if features and c.features and not all(f in r.features for f in c.features):
        return False
    if budget and c.budget_for_two and r.avg_cost_for_two > c.budget_for_two * 1.1:
        return False
    if cuisine and c.cuisine_terms and not Catalog.restaurant_has_cuisine(r, c.cuisine_terms):
        return False
    if c.area and radius is not None and catalog.distance_km(r, c.area) > radius:
        return False
    return True


def candidates(catalog: Catalog, c: Criteria, *, radius: float | None) -> list[Restaurant]:
    """All venues matching every constraint within ``radius`` km of the requested area."""
    return [r for r in catalog.restaurants
            if _passes(r, c, catalog, radius=radius if c.area else None, features=True, budget=True, cuisine=True)]


def filter_with_relaxation(catalog: Catalog, c: Criteria) -> list[Restaurant]:
    """Strict filters first; if nothing matches, relax one constraint at a time and say so."""
    where = f"near {c.area.name}" if c.area else "right now"
    # (kind, radius km, keep features, keep budget, keep cuisine, note announced when this step is used)
    ladder = [
        ("strict", 2.0, True, True, True, None),
        ("area", 5.0, True, True, True, f"Nothing matching inside {where.removeprefix('near ')}; included places within 5 km."),
        ("area", 9.0, True, True, True, f"Nothing matching {where}; included places within 9 km."),
        ("features", 9.0, False, True, True, "No venue has every requested feature; showing the closest matches."),
        ("budget", 9.0, False, False, True, f"Nothing within ₹{c.budget_for_two} for two; showing slightly pricier options."),
        ("anywhere", None, False, False, True, "Showing matches from across Bengaluru."),
        ("cuisine", 2.0, False, False, False, f"No {c.cuisine_text} {where}; showing other options instead."),
    ]
    for kind, radius, keep_features, keep_budget, keep_cuisine, note in ladder:
        if (kind in ("area", "anywhere") and not c.area) or (kind == "features" and not c.features) \
                or (kind == "budget" and not c.budget_for_two) or (kind == "cuisine" and not c.cuisine_terms):
            continue
        matches = [r for r in catalog.restaurants
                   if _passes(r, c, catalog, radius=radius if c.area else None, features=keep_features,
                              budget=keep_budget, cuisine=keep_cuisine)]
        if matches:
            if note:
                c.notes.append(note)
            return matches
    if c.dietary:
        c.notes.append(f"No venue offers all of: {', '.join(c.dietary)}.")
    return []


def score(r: Restaurant, c: Criteria, catalog: Catalog, *, availability: dict | None) -> float:
    s = 0.35 * (r.rating - 3.5) / 1.5 + 0.03 * r.popularity
    if c.area:
        s += 0.25 * math.exp(-catalog.distance_km(r, c.area) / 2.5)
    if c.occasion and c.occasion in r.good_for:
        s += 0.12
    if c.features:
        s += 0.05 * sum(f in r.features for f in c.features)
    if c.query_terms:
        text = r.search_text()
        hits = sum(1 for w in c.query_terms if re.search(rf"\b{re.escape(w)}", text))
        s += 0.15 * hits / len(c.query_terms)
    if availability is not None:
        if availability["status"] == "available":
            s += 0.25 + 0.05 * (1 - availability.get("_occupancy", 0.5))
        elif availability.get("other_times"):
            s += 0.08
        else:
            s -= 0.2
    return s


def reasons(r: Restaurant, c: Criteria, catalog: Catalog, *, availability: dict | None) -> list[str]:
    why: list[str] = []
    if c.cuisine_terms and Catalog.restaurant_has_cuisine(r, c.cuisine_terms):
        why.append(r.cuisine)
    if c.area:
        d = catalog.distance_km(r, c.area)
        why.append(f"in {r.area}" if r.area == c.area.name else f"{d:.1f} km from {c.area.name}")
    why.append(f"{r.rating}★ from {r.reviews:,} reviews" if r.rating >= 4.5 else f"{r.rating}★")
    if c.occasion and c.occasion in r.good_for:
        why.append(f"great for {c.occasion.replace('_', ' ')}")
    for f in c.features:
        if f in r.features:
            why.append(f.replace("_", " "))
    for d in c.dietary:
        why.append({"pure_veg": "pure vegetarian", "jain": "Jain menu", "vegan": "vegan options",
                    "gluten_free": "gluten-free options", "halal": "halal"}.get(d, d))
    if c.budget_for_two and r.avg_cost_for_two <= c.budget_for_two:
        why.append(f"~₹{r.avg_cost_for_two:,} for two")
    if availability and availability["status"] == "available" and availability.get("_occupancy", 1) < 0.5:
        why.append("quieter at that time")
    return why[:5]
