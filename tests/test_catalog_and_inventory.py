import dataclasses

import pytest

from goodfoods.core.catalog import DIETARY, FEATURES, Catalog, Restaurant, get_catalog
from goodfoods.core.errors import DomainError
from goodfoods.core.inventory import duration_for, find_tables, nearby_times, occupancy

catalog = get_catalog()


def test_catalog_size_matches_brief():
    assert 50 <= len(catalog.restaurants) <= 100
    assert len({r.id for r in catalog.restaurants}) == len(catalog.restaurants)
    assert len(catalog.brands) == 17 and len(catalog.areas) == 27


@pytest.mark.parametrize("text, area", [
    ("Indiranagar", "Indiranagar"), ("indira nagar", "Indiranagar"), ("kormangala", "Koramangala"),
    ("ITPL", "Whitefield"), ("near hsr", "HSR Layout"), ("Malleswaram", "Malleshwaram"), ("UB City", "Lavelle Road"),
])
def test_resolve_area_handles_aliases_and_typos(text, area):
    assert catalog.resolve_area(text).name == area


def test_resolve_area_unknown_returns_none():
    assert catalog.resolve_area("Mumbai") is None


@pytest.mark.parametrize("ref, rid", [
    ("tandoor-house-indiranagar", "tandoor-house-indiranagar"),
    ("Tandoor House, Indiranagar", "tandoor-house-indiranagar"),
    ("tandoor house indiranagar", "tandoor-house-indiranagar"),
    ("Tandor House Koramangala", "tandoor-house-koramangala"),
    ("brew ledger whitefield", "the-brew-ledger-whitefield"),
    ("Nawab's Biryani House Frazer Town", "nawabs-biryani-house-frazer-town"),
])
def test_resolve_restaurant_is_forgiving(ref, rid):
    assert catalog.resolve_restaurant(ref).id == rid


def test_ambiguous_brand_asks_which_location():
    with pytest.raises(DomainError) as err:
        catalog.resolve_restaurant("Tandoor House")
    assert "several locations" in err.value.message and err.value.hint


def test_unknown_restaurant_says_never_invent_ids():
    with pytest.raises(DomainError) as err:
        catalog.resolve_restaurant("La Dolce Vita")
    assert "Never invent" in err.value.hint


def test_cuisine_and_vocabulary_matching():
    terms, unknown = catalog.match_cuisine("pizza or sushi")
    assert {"pizza", "sushi"} <= terms and not unknown
    assert catalog.match_cuisine("korean")[0] == set()
    assert Catalog.canonicalize(["veg", "Jain"], DIETARY)[0] == ["pure_veg", "jain"]
    assert Catalog.canonicalize("rooftop with live music", FEATURES)[0] == ["rooftop", "live_music"]


def _toy(mix, pdr=None) -> Restaurant:
    return dataclasses.replace(catalog.restaurants[0], id="toy", table_mix=mix, private_dining_capacity=pdr, tables=[])


def test_smallest_fitting_table_is_chosen():
    r = _toy({"2": 2, "4": 2, "6": 1})
    assert find_tables(r, {}, 20 * 60, 2) == ["T2-1"]
    assert find_tables(r, {}, 20 * 60, 3) == ["T4-1"]


def test_large_parties_get_joined_tables_or_private_room():
    r = _toy({"2": 2, "4": 2, "6": 1}, pdr=14)
    assert len(find_tables(r, {}, 20 * 60, 7)) == 2
    assert find_tables(r, {}, 20 * 60, 12) == ["PDR"]
    assert find_tables(_toy({"2": 2, "4": 2}), {}, 20 * 60, 12) is None


def test_overlapping_bookings_block_a_table_but_back_to_back_is_fine():
    r = _toy({"2": 1})
    busy = {"T2-1": [(19 * 60, 19 * 60 + duration_for(2))]}
    assert find_tables(r, busy, 20 * 60, 2) is None
    assert find_tables(r, busy, 19 * 60 + 90, 2) == ["T2-1"]
    assert nearby_times(r, busy, 20 * 60, 2)
    assert occupancy(r, busy, 19 * 60 + 30) == 1.0
