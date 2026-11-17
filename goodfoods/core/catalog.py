"""Restaurant catalog: loading, vocabulary normalisation and forgiving entity resolution.

Small LLMs rarely pass perfectly canonical arguments. They write "Indira Nagar", "veg",
"rooftop with live music" or "Tandor House". This module turns such loose input into
canonical values, and raises an instructive :class:`DomainError` when it can't.
"""

from __future__ import annotations

import difflib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from goodfoods import config
from goodfoods.core.errors import DomainError

# --------------------------------------------------------------------------------------
# Controlled vocabularies (canonical value -> synonyms the guest / model might use)
# --------------------------------------------------------------------------------------
FEATURES: dict[str, list[str]] = {
    "outdoor_seating": ["outdoor", "outdoors", "al fresco", "open air", "garden", "terrace", "courtyard", "outside"],
    "rooftop": ["roof top", "roof", "skyline", "view"],
    "live_music": ["music", "live band", "band", "live performance", "acoustic", "dj"],
    "private_dining": ["private room", "private dining room", "pdr", "private area", "private space"],
    "kid_friendly": ["kids", "kid", "children", "child", "family friendly", "high chair", "kids menu"],
    "wheelchair_accessible": ["wheelchair", "accessible", "accessibility", "step free", "disabled access"],
    "valet_parking": ["parking", "valet", "car park"],
    "pet_friendly": ["pet", "pets", "dog", "dogs", "dog friendly"],
    "bar": ["alcohol", "drinks", "cocktails", "beer", "wine", "liquor", "full bar"],
    "sports_screening": ["sports", "match", "cricket", "football", "screening", "big screen", "ipl"],
    "quiet": ["calm", "peaceful", "quiet place", "not noisy", "conversation", "silent"],
}
DIETARY: dict[str, list[str]] = {
    "pure_veg": ["veg", "vegetarian", "pure vegetarian", "pure veg", "veg only", "no meat", "shakahari"],
    "vegan": ["vegan_options", "plant based", "plant-based", "dairy free", "no dairy"],
    "jain": ["jain_options", "jain food", "no onion garlic", "no onion no garlic"],
    "gluten_free": ["gluten free", "gluten_free_options", "celiac", "coeliac", "no gluten"],
    "halal": ["halal meat", "halal food"],
}
OCCASIONS: dict[str, list[str]] = {
    "date_night": ["date", "romantic", "anniversary", "couple", "proposal", "valentine"],
    "birthday": ["bday", "birthday party", "celebration", "celebrate"],
    "business": ["business meal", "client", "clients", "work", "meeting", "corporate", "office", "team lunch"],
    "family": ["family dinner", "family lunch", "parents", "kids", "with family"],
    "friends": ["friends", "hangout", "catch up", "casual", "gang"],
    "large_groups": ["large group", "big group", "group", "team", "party", "reunion", "farewell"],
    "solo": ["alone", "just me", "by myself", "work from cafe"],
}

_STOP = {"the", "and", "co", "of", "restaurant", "outlet", "branch", "at", "in", "near"}


def norm(text: str | None) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ").replace("'", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _words(text: str) -> set[str]:
    return set(norm(text).split())


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _hm(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


# --------------------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Table:
    id: str
    size: int
    private: bool = False


@dataclass
class Restaurant:
    id: str
    name: str
    brand: str
    cuisine: str
    cuisines: list[str]
    tags: list[str]
    area: str
    address: str
    lat: float
    lng: float
    price_band: int
    avg_cost_for_two: int
    rating: float
    reviews: int
    seats: int
    table_mix: dict[str, int]
    private_dining_capacity: int | None
    hours: list[list[str]]
    features: list[str]
    dietary: list[str]
    good_for: list[str]
    signature_dishes: list[str]
    description: str
    popularity: float
    tables: list[Table] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tables:
            tables: list[Table] = []
            for size, count in sorted(self.table_mix.items(), key=lambda kv: int(kv[0])):
                for i in range(1, count + 1):
                    tables.append(Table(f"T{size}-{i}", int(size)))
            if self.private_dining_capacity:
                tables.append(Table("PDR", self.private_dining_capacity, private=True))
            self.tables = tables
        self._search_text = norm(" ".join([self.name, self.cuisine, *self.cuisines, *self.tags,
                                           self.description, *self.signature_dishes, *self.features]))

    # ---- presentation helpers --------------------------------------------------------
    @property
    def price_label(self) -> str:
        return f"{'₹' * self.price_band} · ~₹{self.avg_cost_for_two:,} for two"

    @property
    def service_windows(self) -> list[tuple[int, int]]:
        """(opening minute, closing minute) pairs."""
        return [(_hm(o), _hm(c)) for o, c in self.hours]

    @property
    def hours_label(self) -> str:
        from goodfoods.core.timeparse import fmt_time

        return ", ".join(f"{fmt_time(o)}–{fmt_time(c)}" for o, c in self.service_windows)

    @property
    def max_party(self) -> int:
        regular = sorted((t.size for t in self.tables if not t.private), reverse=True)
        best_pair = sum(regular[:2]) if len(regular) >= 2 else (regular[0] if regular else 0)
        return max(best_pair, self.private_dining_capacity or 0)

    def search_text(self) -> str:
        return self._search_text

    def card(self) -> dict:
        """Compact representation for tool results (kept small: it goes into the LLM context)."""
        return {
            "id": self.id,
            "name": self.name,
            "cuisine": self.cuisine,
            "area": self.area,
            "rating": self.rating,
            "price": self.price_label,
        }

    def details(self) -> dict:
        return {
            **self.card(),
            "brand": self.brand,
            "address": self.address,
            "hours": self.hours_label,
            "seats": self.seats,
            "private_dining_room": (f"up to {self.private_dining_capacity} guests"
                                    if self.private_dining_capacity else "not available"),
            "max_party_online": min(self.max_party, config.MAX_ONLINE_PARTY),
            "features": self.features,
            "dietary_options": self.dietary,
            "good_for": self.good_for,
            "signature_dishes": self.signature_dishes,
            "reviews": self.reviews,
            "about": self.description,
        }


@dataclass
class Area:
    name: str
    lat: float
    lng: float
    aliases: list[str]


# --------------------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------------------
class Catalog:
    def __init__(self, path: Path = config.CATALOG_PATH) -> None:
        raw = json.loads(Path(path).read_text())
        self.brand_name: str = raw["brand"]
        self.city: str = raw["city"]
        self.areas: dict[str, Area] = {
            name: Area(name, v["lat"], v["lng"], v.get("aliases", [])) for name, v in raw["areas"].items()
        }
        self.restaurants: list[Restaurant] = [Restaurant(**r) for r in raw["restaurants"]]
        self.by_id: dict[str, Restaurant] = {r.id: r for r in self.restaurants}
        self.brands: list[str] = sorted({r.brand for r in self.restaurants})
        self.cuisines: list[str] = sorted({r.cuisine for r in self.restaurants})
        self._cuisine_vocab: list[str] = sorted({norm(t) for r in self.restaurants
                                                 for t in [*r.tags, *r.cuisines, r.cuisine]})

    # ---- areas -------------------------------------------------------------------------
    def resolve_area(self, text: str | None) -> Area | None:
        """Map loose area text to a known neighbourhood (None if it isn't one we serve)."""
        t = norm(text)
        if not t:
            return None
        for area in self.areas.values():
            if t == norm(area.name) or t in (norm(a) for a in area.aliases):
                return area
        # area mentioned inside a longer phrase ("near indiranagar metro")
        for area in sorted(self.areas.values(), key=lambda a: -len(a.name)):
            names = [norm(area.name), *(norm(a) for a in area.aliases)]
            if any(re.search(rf"\b{re.escape(n)}\b", t) for n in names if len(n) >= 3):
                return area
        keys = {norm(a.name): a for a in self.areas.values()}
        keys.update({norm(al): a for a in self.areas.values() for al in a.aliases if len(al) >= 4})
        close = difflib.get_close_matches(t, list(keys), n=1, cutoff=0.8)
        return keys[close[0]] if close else None

    # ---- cuisines ----------------------------------------------------------------------
    def match_cuisine(self, text: str | None) -> tuple[set[str], list[str]]:
        """Return (matched vocabulary terms, unrecognised fragments) for a cuisine phrase."""
        matched: set[str] = set()
        unknown: list[str] = []
        t = norm(text)
        if not t:
            return matched, unknown
        for part in re.split(r"\bor\b|\band\b|,|/", t):
            part = re.sub(r"\b(food|cuisine|restaurant|restaurants|place|places|dishes|style|good|best)\b", " ", part)
            part = re.sub(r"\s+", " ", part).strip()
            if not part:
                continue
            if part in self._cuisine_vocab:
                matched.add(part)
                continue
            hits = [v for v in self._cuisine_vocab if re.search(rf"\b{re.escape(v)}\b", part)]
            if hits:
                matched.update(hits)
                continue
            close = difflib.get_close_matches(part, self._cuisine_vocab, n=1, cutoff=0.78)
            if close:
                matched.add(close[0])
            else:
                unknown.append(part)
        return matched, unknown

    @staticmethod
    def restaurant_has_cuisine(r: Restaurant, terms: set[str]) -> bool:
        own = {norm(t) for t in [*r.tags, *r.cuisines, r.cuisine]}
        return bool(own & terms)

    # ---- controlled vocabularies ---------------------------------------------------------
    @staticmethod
    def canonicalize(values: list[str] | str | None, vocab: dict[str, list[str]]) -> tuple[list[str], list[str]]:
        """Map free-text values onto a controlled vocabulary. Returns (canonical, unknown)."""
        if values is None:
            return [], []
        if isinstance(values, str):
            values = [v for v in re.split(r",|\band\b|/", values) if v.strip()]
        out: list[str] = []
        unknown: list[str] = []
        for value in values:
            v = norm(value).replace(" ", "_")
            v_spaced = v.replace("_", " ")
            exact = next((canon for canon, syns in vocab.items()
                          if v == canon or v_spaced == canon.replace("_", " ")
                          or v_spaced in (norm(s) for s in syns)), None)
            # otherwise collect every term mentioned inside a longer phrase ("rooftop with live music")
            found = [exact] if exact else [
                canon for canon, syns in vocab.items()
                if any(re.search(rf"\b{re.escape(norm(s))}\b", v_spaced) for s in [canon.replace("_", " "), *syns])]
            out.extend(f for f in found if f not in out)
            if not found and v:
                unknown.append(str(value))
        return out, unknown

    # ---- restaurants -----------------------------------------------------------------------
    def get(self, restaurant_id: str) -> Restaurant | None:
        return self.by_id.get(restaurant_id)

    def resolve_restaurant(self, ref: str | None) -> Restaurant:
        """Resolve an id *or* a loosely written name ("tandoor house indiranagar") to one restaurant."""
        if not ref or not str(ref).strip():
            raise DomainError("No restaurant was given.",
                              hint="Pass a restaurant id from search_restaurants, or ask the guest which restaurant.")
        ref = str(ref).strip()
        if ref in self.by_id:
            return self.by_id[ref]
        t = norm(ref)
        slug = t.replace(" ", "-")
        if slug in self.by_id:
            return self.by_id[slug]
        for r in self.restaurants:
            if norm(r.name) == t:
                return r

        brands = self._brands_in(t)
        area = self.resolve_area(t)
        if brands:
            locs = [r for r in self.restaurants if r.brand in brands]
            if area:
                exact = [r for r in locs if r.area == area.name]
                if len(exact) == 1:
                    return exact[0]
            if len(locs) == 1:
                return locs[0]
            options = ", ".join(f"{r.name} ({r.id})" for r in locs[:8])
            raise DomainError(
                f"'{ref}' matches several locations: {options}.",
                hint="If the guest already named an area, call again with that location's id from this list; "
                     "otherwise ask the guest which area they prefer.",
            )

        names = {norm(r.name): r for r in self.restaurants}
        names.update({norm(r.id): r for r in self.restaurants})
        close = difflib.get_close_matches(t, list(names), n=3, cutoff=0.6)
        if len(close) == 1 or (close and difflib.SequenceMatcher(None, t, close[0]).ratio() > 0.9):
            return names[close[0]]
        suggestions = ", ".join(f"{names[c].name} ({names[c].id})" for c in close) or "none"
        raise DomainError(
            f"Unknown restaurant '{ref}'. Closest matches: {suggestions}.",
            hint="Use an exact id returned by search_restaurants. Never invent restaurant ids.",
        )

    def _brands_in(self, t: str) -> list[str]:
        tokens = t.split()
        hits = []
        for brand in self.brands:
            key = [w for w in norm(brand).split() if w not in _STOP] or norm(brand).split()
            if all(any(difflib.SequenceMatcher(None, k, tok).ratio() >= 0.8 for tok in tokens) for k in key):
                hits.append(brand)
        return hits

    def distance_km(self, r: Restaurant, area: Area) -> float:
        return haversine_km(r.lat, r.lng, area.lat, area.lng)


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return Catalog()
