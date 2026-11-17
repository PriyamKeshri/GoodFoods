"""Deterministic generator for the GoodFoods restaurant catalog (``restaurants.json``).

GoodFoods is modelled as a Bengaluru restaurant *group* running 17 dining concepts across
27 neighbourhoods (76 locations). Every location gets a realistic table inventory, opening
hours, price band, rating, features, dietary options and signature dishes.

All brands, addresses and numbers are fictional. Re-generate with:

    python -m goodfoods.data.generate
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).with_name("restaurants.json")

# name -> (lat, lng, pincode, hotness 0..1, streets)
AREAS: dict[str, tuple[float, float, str, float, list[str]]] = {
    "Indiranagar": (12.9719, 77.6412, "560038", 1.00, ["100 Feet Road", "12th Main Road", "CMH Road", "Double Road"]),
    "Koramangala": (12.9352, 77.6245, "560034", 1.00, ["80 Feet Road", "5th Block", "Jyoti Nivas College Road", "1st Main, 7th Block"]),
    "HSR Layout": (12.9116, 77.6474, "560102", 0.85, ["27th Main Road", "Sector 7", "14th Main Road"]),
    "Whitefield": (12.9698, 77.7500, "560066", 0.80, ["ITPL Main Road", "Whitefield Main Road", "Varthur Road"]),
    "Jayanagar": (12.9308, 77.5838, "560041", 0.80, ["11th Main, 4th Block", "30th Cross Road", "9th Block"]),
    "JP Nagar": (12.9063, 77.5857, "560078", 0.65, ["24th Main Road", "15th Cross Road", "Ring Road"]),
    "MG Road": (12.9756, 77.6050, "560001", 0.85, ["MG Road", "Brigade Road", "Residency Road"]),
    "Church Street": (12.9745, 77.6022, "560001", 0.90, ["Church Street", "Museum Road"]),
    "Lavelle Road": (12.9707, 77.5975, "560001", 0.90, ["Lavelle Road", "Vittal Mallya Road"]),
    "Malleshwaram": (13.0031, 77.5643, "560003", 0.75, ["Sampige Road", "8th Cross", "Margosa Road"]),
    "Rajajinagar": (12.9910, 77.5520, "560010", 0.60, ["Dr Rajkumar Road", "West of Chord Road"]),
    "Basavanagudi": (12.9421, 77.5753, "560004", 0.65, ["DVG Road", "Gandhi Bazaar Main Road", "Bull Temple Road"]),
    "Banashankari": (12.9255, 77.5468, "560070", 0.60, ["Outer Ring Road", "50 Feet Road"]),
    "BTM Layout": (12.9166, 77.6101, "560076", 0.70, ["16th Main Road", "29th Main Road"]),
    "Electronic City": (12.8452, 77.6602, "560100", 0.60, ["Hosur Road", "Neeladri Road"]),
    "Marathahalli": (12.9591, 77.6974, "560037", 0.70, ["Outer Ring Road", "Marathahalli Bridge Road"]),
    "Bellandur": (12.9304, 77.6784, "560103", 0.75, ["Outer Ring Road", "Green Glen Layout"]),
    "Sarjapur Road": (12.9121, 77.6858, "560035", 0.70, ["Sarjapur Main Road", "Kaikondrahalli"]),
    "Brookefield": (12.9655, 77.7185, "560037", 0.65, ["Brookefield Main Road", "AECS Layout"]),
    "Hebbal": (13.0358, 77.5970, "560024", 0.65, ["Bellary Road", "Manyata Tech Park Road"]),
    "Yelahanka": (13.1007, 77.5963, "560064", 0.55, ["Yelahanka New Town", "Doddaballapur Road"]),
    "Frazer Town": (12.9980, 77.6150, "560005", 0.75, ["Mosque Road", "Coles Road"]),
    "Ulsoor": (12.9817, 77.6286, "560008", 0.70, ["Ulsoor Road", "Kensington Road"]),
    "Sadashivanagar": (13.0068, 77.5813, "560080", 0.75, ["Sankey Road", "RMV Extension"]),
    "Kalyan Nagar": (13.0221, 77.6403, "560043", 0.70, ["HRBR Layout", "Kammanahalli Main Road"]),
    "Domlur": (12.9610, 77.6387, "560071", 0.65, ["Old Airport Road", "Domlur Layout"]),
    "Vijayanagar": (12.9719, 77.5350, "560040", 0.60, ["Chord Road", "MC Layout"]),
}

AREA_ALIASES: dict[str, list[str]] = {
    "Indiranagar": ["indira nagar", "indranagar", "hal 2nd stage", "100 ft road", "100 feet road"],
    "Koramangala": ["kormangala", "koramangla", "kora", "forum mall"],
    "HSR Layout": ["hsr", "h s r"],
    "Whitefield": ["itpl", "white field", "varthur"],
    "Jayanagar": ["jaya nagar", "jayangar"],
    "JP Nagar": ["j p nagar", "jpnagar", "jp nagara"],
    "MG Road": ["m g road", "mg rd", "brigade road", "residency road"],
    "Church Street": ["church st"],
    "Lavelle Road": ["lavelle rd", "ub city", "vittal mallya road"],
    "Malleshwaram": ["malleswaram", "malleshwaram", "malleswara"],
    "Rajajinagar": ["rajaji nagar", "rajajinagara"],
    "Basavanagudi": ["basavangudi", "gandhi bazaar"],
    "Banashankari": ["bsk", "banashankari"],
    "BTM Layout": ["btm", "b t m"],
    "Electronic City": ["e city", "ecity", "ec", "electronics city", "electronic city phase 1"],
    "Marathahalli": ["marathalli", "marthahalli"],
    "Bellandur": ["orr", "outer ring road", "ecospace"],
    "Sarjapur Road": ["sarjapur", "sarjapura"],
    "Brookefield": ["brookfield"],
    "Hebbal": ["manyata", "manyata tech park"],
    "Yelahanka": ["yelahanka new town"],
    "Frazer Town": ["fraser town", "cox town", "pulikeshi nagar"],
    "Ulsoor": ["halasuru", "ulsoor lake"],
    "Sadashivanagar": ["sadashiv nagar", "sadashiva nagar"],
    "Kalyan Nagar": ["kalyan nagara", "hrbr", "kammanahalli"],
    "Domlur": ["old airport road"],
    "Vijayanagar": ["vijaynagar", "vijay nagar"],
}

# Each brand: cuisine, search tags, price, rating base, seat range, table mix (size -> share of seats),
# private-dining probability & capacity, features, dietary, occasions, dishes, hours, areas.
BRANDS: list[dict] = [
    dict(
        brand="Tandoor House", cuisine="North Indian", cuisines=["North Indian", "Mughlai"],
        tags=["north indian", "mughlai", "punjabi", "tandoori", "kebab", "butter chicken", "naan", "indian", "curry"],
        price_band=2, cost_for_two=1400, rating=4.4, seats=(96, 160), mix={2: .15, 4: .45, 6: .25, 8: .15},
        pdr=(.6, (16, 24)), features=["kid_friendly", "wheelchair_accessible"],
        optional=["outdoor_seating", "valet_parking", "bar"], dietary=["vegan", "jain"],
        good_for=["family", "friends", "birthday", "large_groups"],
        dishes=["Dal Makhani", "Murgh Malai Tikka", "Butter Garlic Naan"], hours=[("12:00", "23:00")],
        tagline="Clay-oven classics, slow-cooked dals and generous family platters.",
        areas=["Indiranagar", "Koramangala", "Jayanagar", "Whitefield", "Malleshwaram", "Marathahalli", "Yelahanka"],
    ),
    dict(
        brand="Dosa Republic", cuisine="South Indian", cuisines=["South Indian", "Udupi"],
        tags=["south indian", "dosa", "idli", "vada", "udupi", "filter coffee", "breakfast", "veg", "indian", "tiffin"],
        price_band=1, cost_for_two=500, rating=4.5, seats=(44, 80), mix={2: .3, 4: .6, 6: .1},
        pdr=(0, (0, 0)), features=["kid_friendly", "quiet"], optional=["outdoor_seating", "wheelchair_accessible"],
        dietary=["pure_veg", "jain", "vegan"], good_for=["family", "solo", "friends"],
        dishes=["Ghee Roast Dosa", "Benne Masala Dosa", "Filter Coffee"], hours=[("07:30", "22:30")],
        tagline="Crisp benne dosas, fluffy idlis and strong filter coffee from 7:30 am.",
        areas=["Jayanagar", "Basavanagudi", "Malleshwaram", "Indiranagar", "HSR Layout", "Electronic City", "Rajajinagar", "JP Nagar"],
    ),
    dict(
        brand="Coastal Tide", cuisine="Coastal Seafood", cuisines=["Mangalorean", "Kerala", "Seafood"],
        tags=["seafood", "fish", "coastal", "mangalorean", "kerala", "malabar", "prawns", "crab", "appam", "indian"],
        price_band=3, cost_for_two=1800, rating=4.5, seats=(70, 120), mix={2: .2, 4: .5, 6: .2, 8: .1},
        pdr=(.5, (12, 18)), features=["bar"], optional=["valet_parking", "outdoor_seating", "quiet"],
        dietary=["gluten_free"], good_for=["family", "business", "date_night"],
        dishes=["Prawn Ghee Roast", "Kori Gassi with Neer Dosa", "Appam & Stew"],
        hours=[("12:00", "15:30"), ("19:00", "23:00")],
        tagline="Mangalorean ghee roasts, Kerala stews and the day's catch from the west coast.",
        areas=["Indiranagar", "Koramangala", "Whitefield", "Ulsoor"],
    ),
    dict(
        brand="Red Lantern", cuisine="Pan-Asian", cuisines=["Chinese", "Thai", "Pan-Asian"],
        tags=["chinese", "pan asian", "pan-asian", "asian", "thai", "dim sum", "momos", "noodles", "wok", "indo chinese"],
        price_band=2, cost_for_two=1300, rating=4.3, seats=(70, 130), mix={2: .2, 4: .5, 6: .2, 8: .1},
        pdr=(.3, (12, 16)), features=["kid_friendly"], optional=["bar", "outdoor_seating", "wheelchair_accessible"],
        dietary=["vegan", "jain"], good_for=["friends", "family", "birthday"],
        dishes=["Crystal Dim Sum Basket", "Hakka Noodles", "Thai Green Curry"], hours=[("12:00", "23:00")],
        tagline="Wok-tossed noodles, steaming dim sum baskets and fragrant Thai curries.",
        areas=["Koramangala", "HSR Layout", "Indiranagar", "Bellandur", "Brookefield", "Kalyan Nagar"],
    ),
    dict(
        brand="Trattoria Verde", cuisine="Italian", cuisines=["Italian", "Pizza"],
        tags=["italian", "pizza", "pasta", "risotto", "wood fired", "european", "continental"],
        price_band=3, cost_for_two=2000, rating=4.5, seats=(56, 96), mix={2: .4, 4: .45, 6: .15},
        pdr=(.4, (12, 16)), features=["bar"], optional=["outdoor_seating", "quiet", "wheelchair_accessible", "valet_parking"],
        dietary=["vegan", "gluten_free"], good_for=["date_night", "friends", "birthday", "business"],
        dishes=["Wood-fired Margherita", "Truffle Mushroom Risotto", "Classic Tiramisu"], hours=[("12:00", "23:00")],
        tagline="Wood-fired pizzas, hand-rolled pasta and a candle-lit dining room.",
        areas=["Indiranagar", "Koramangala", "Lavelle Road", "Whitefield", "Sadashivanagar"],
    ),
    dict(
        brand="Ember & Oak", cuisine="BBQ & Grill", cuisines=["Barbecue", "Grill", "Steakhouse"],
        tags=["bbq", "barbecue", "grill", "steak", "steakhouse", "ribs", "smoked", "meat", "continental"],
        price_band=3, cost_for_two=2200, rating=4.4, seats=(90, 150), mix={2: .15, 4: .45, 6: .25, 8: .15},
        pdr=(.5, (16, 24)), features=["bar", "valet_parking"], optional=["outdoor_seating", "sports_screening", "live_music"],
        dietary=["gluten_free"], good_for=["friends", "business", "birthday", "large_groups"],
        dishes=["Smoked Pork Ribs", "Peri-Peri Chicken", "Charred Paneer Steak"], hours=[("12:00", "23:30")],
        tagline="Low-and-slow smoked meats, open-fire grills and a serious bar.",
        areas=["Whitefield", "Koramangala", "MG Road", "Hebbal"],
    ),
    dict(
        brand="Leaf & Ladle", cuisine="Vegan & Healthy", cuisines=["Vegan", "Healthy", "Salads"],
        tags=["vegan", "healthy", "salad", "plant based", "plant-based", "bowls", "gluten free", "keto", "veg"],
        price_band=2, cost_for_two=1100, rating=4.4, seats=(40, 70), mix={2: .5, 4: .45, 6: .05},
        pdr=(0, (0, 0)), features=["quiet", "pet_friendly"], optional=["outdoor_seating", "wheelchair_accessible"],
        dietary=["pure_veg", "vegan", "gluten_free", "jain"], good_for=["solo", "friends", "business"],
        dishes=["Millet Buddha Bowl", "Jackfruit Tacos", "Ragi Banana Pancakes"], hours=[("08:30", "22:30")],
        tagline="Plant-forward bowls, millet everything and cold-pressed juices.",
        areas=["Indiranagar", "HSR Layout", "Jayanagar", "Sarjapur Road"],
    ),
    dict(
        brand="Zaatar & Co.", cuisine="Lebanese & Mediterranean", cuisines=["Lebanese", "Mediterranean", "Middle Eastern"],
        tags=["lebanese", "mediterranean", "middle eastern", "arabic", "shawarma", "falafel", "hummus", "mezze", "grill"],
        price_band=2, cost_for_two=1300, rating=4.3, seats=(50, 90), mix={2: .3, 4: .5, 6: .2},
        pdr=(.2, (12, 14)), features=["kid_friendly"], optional=["outdoor_seating", "live_music", "wheelchair_accessible"],
        dietary=["halal", "vegan"], good_for=["friends", "family", "date_night"],
        dishes=["Grand Mezze Platter", "Chicken Shawarma Plate", "Kunafa"], hours=[("12:00", "23:30")],
        tagline="Mezze spreads, charcoal-grilled shawarma and warm kunafa.",
        areas=["Frazer Town", "Koramangala", "Church Street", "Bellandur"],
    ),
    dict(
        brand="Sakura Street", cuisine="Japanese", cuisines=["Japanese", "Sushi"],
        tags=["japanese", "sushi", "ramen", "katsu", "sashimi", "asian", "pan asian"],
        price_band=4, cost_for_two=3200, rating=4.6, seats=(40, 64), mix={2: .5, 4: .4, 6: .1},
        pdr=(.3, (8, 12)), features=["bar", "quiet"], optional=["valet_parking", "wheelchair_accessible"],
        dietary=["gluten_free"], good_for=["date_night", "business"],
        dishes=["Salmon Nigiri", "Tonkotsu Ramen", "Matcha Cheesecake"], hours=[("12:30", "15:00"), ("19:00", "23:00")],
        tagline="An omakase-style sushi counter, rich ramen and a sake list.",
        areas=["Indiranagar", "Lavelle Road", "Whitefield"],
    ),
    dict(
        brand="Nawab's Biryani House", cuisine="Hyderabadi & Awadhi", cuisines=["Biryani", "Hyderabadi", "Awadhi"],
        tags=["biryani", "hyderabadi", "awadhi", "lucknowi", "kebab", "haleem", "mughlai", "indian", "north indian"],
        price_band=2, cost_for_two=900, rating=4.3, seats=(60, 120), mix={2: .15, 4: .5, 6: .25, 8: .1},
        pdr=(.3, (12, 20)), features=["kid_friendly"], optional=["wheelchair_accessible", "outdoor_seating"],
        dietary=["halal"], good_for=["family", "friends", "large_groups"],
        dishes=["Hyderabadi Dum Biryani", "Galouti Kebab", "Double ka Meetha"], hours=[("12:00", "23:30")],
        tagline="Dum-cooked biryanis, melt-in-the-mouth galoutis and Hyderabadi desserts.",
        areas=["Frazer Town", "BTM Layout", "Marathahalli", "Electronic City", "Banashankari", "Rajajinagar"],
    ),
    dict(
        brand="Cantina Fuego", cuisine="Mexican", cuisines=["Mexican", "Tex-Mex"],
        tags=["mexican", "tacos", "burrito", "nachos", "tex mex", "quesadilla", "margarita"],
        price_band=2, cost_for_two=1500, rating=4.2, seats=(60, 100), mix={2: .25, 4: .5, 6: .25},
        pdr=(0, (0, 0)), features=["bar", "live_music"], optional=["outdoor_seating", "sports_screening"],
        dietary=["vegan", "gluten_free"], good_for=["friends", "birthday", "date_night"],
        dishes=["Birria Tacos", "Loaded Nachos", "Cinnamon Churros"], hours=[("12:00", "23:30")],
        tagline="Birria tacos, loaded nachos and frozen margaritas with a loud playlist.",
        areas=["Koramangala", "Indiranagar", "Whitefield"],
    ),
    dict(
        brand="The Brew Ledger", cuisine="Gastropub", cuisines=["Gastropub", "Continental", "Microbrewery"],
        tags=["pub", "gastropub", "beer", "craft beer", "brewery", "microbrewery", "bar", "drinks", "burgers", "continental"],
        price_band=3, cost_for_two=2000, rating=4.3, seats=(140, 240), mix={2: .1, 4: .45, 6: .3, 8: .15},
        pdr=(.7, (20, 30)), features=["bar", "live_music", "sports_screening"], optional=["rooftop", "outdoor_seating", "valet_parking"],
        dietary=["vegan"], good_for=["friends", "large_groups", "birthday", "business"],
        dishes=["Six-Beer Tasting Flight", "Beer-battered Fish & Chips", "Pulled Pork Burger"], hours=[("12:00", "23:30")],
        tagline="House-brewed craft beer, big-screen sport and live bands on weekends.",
        areas=["Koramangala", "Indiranagar", "Whitefield", "HSR Layout", "MG Road"],
    ),
    dict(
        brand="Thali Theory", cuisine="Rajasthani & Gujarati Thali", cuisines=["Thali", "Rajasthani", "Gujarati"],
        tags=["thali", "rajasthani", "gujarati", "marwari", "veg", "indian", "unlimited thali"],
        price_band=2, cost_for_two=800, rating=4.5, seats=(70, 110), mix={2: .15, 4: .55, 6: .2, 8: .1},
        pdr=(.3, (12, 18)), features=["kid_friendly", "wheelchair_accessible"], optional=["quiet"],
        dietary=["pure_veg", "jain"], good_for=["family", "large_groups"],
        dishes=["Unlimited Royal Thali", "Dal Baati Churma", "Gujarati Kadhi"], hours=[("11:30", "15:30"), ("19:00", "22:45")],
        tagline="Unlimited 28-item thalis served at your table, Jain versions on request.",
        areas=["Jayanagar", "Malleshwaram", "Basavanagudi", "Vijayanagar"],
    ),
    dict(
        brand="Morning Ritual Café", cuisine="Café & Brunch", cuisines=["Café", "Brunch", "Bakery"],
        tags=["cafe", "café", "coffee", "brunch", "breakfast", "bakery", "desserts", "continental", "sandwich"],
        price_band=1, cost_for_two=700, rating=4.4, seats=(30, 60), mix={2: .6, 4: .4},
        pdr=(0, (0, 0)), features=["pet_friendly", "quiet"], optional=["outdoor_seating", "wheelchair_accessible"],
        dietary=["vegan", "gluten_free"], good_for=["solo", "friends", "business", "date_night"],
        dishes=["Avocado Sourdough Toast", "Eggs Benedict", "Almond Croissant"], hours=[("08:00", "22:00")],
        tagline="Slow brunches, single-origin pour-overs and a bakery counter.",
        areas=["Indiranagar", "Koramangala", "HSR Layout", "Jayanagar", "Malleshwaram", "Whitefield"],
    ),
    dict(
        brand="Chettinad Courtyard", cuisine="Chettinad", cuisines=["Chettinad", "Tamil"],
        tags=["chettinad", "tamil", "south indian", "kothu parotta", "spicy", "indian", "biryani"],
        price_band=2, cost_for_two=1000, rating=4.4, seats=(50, 90), mix={2: .2, 4: .55, 6: .25},
        pdr=(.2, (12, 14)), features=["kid_friendly"], optional=["quiet", "wheelchair_accessible"],
        dietary=["halal"], good_for=["family", "friends"],
        dishes=["Chettinad Pepper Chicken", "Kothu Parotta", "Mushroom Pepper Fry"], hours=[("12:00", "15:30"), ("19:00", "23:00")],
        tagline="Fiery Karaikudi spice blends served in a tiled courtyard.",
        areas=["JP Nagar", "Banashankari", "Domlur"],
    ),
    dict(
        brand="Mishti & Mustard", cuisine="Bengali", cuisines=["Bengali"],
        tags=["bengali", "kolkata", "fish curry", "kosha mangsho", "indian", "mishti"],
        price_band=2, cost_for_two=1100, rating=4.3, seats=(40, 70), mix={2: .3, 4: .55, 6: .15},
        pdr=(0, (0, 0)), features=["quiet"], optional=["kid_friendly"],
        dietary=["gluten_free"], good_for=["family", "friends"],
        dishes=["Kosha Mangsho", "Shorshe Ilish", "Mishti Doi"], hours=[("12:00", "15:30"), ("19:00", "22:45")],
        tagline="Home-style Bengali cooking, from shorshe ilish to mishti doi.",
        areas=["HSR Layout", "Kalyan Nagar"],
    ),
    dict(
        brand="Saffron Terrace", cuisine="Modern Indian Fine Dining", cuisines=["Modern Indian", "Fine Dining"],
        tags=["fine dining", "tasting menu", "modern indian", "rooftop", "indian", "luxury", "romantic"],
        price_band=4, cost_for_two=3800, rating=4.7, seats=(48, 72), mix={2: .5, 4: .4, 6: .1},
        pdr=(1.0, (10, 14)), features=["rooftop", "bar", "valet_parking", "quiet", "private_dining"], optional=["live_music"],
        dietary=["vegan", "jain", "gluten_free"], good_for=["date_night", "business", "birthday"],
        dishes=["Nine-course Tasting Menu", "Lamb Shank Nihari", "Deconstructed Gulab Jamun"],
        hours=[("12:30", "15:00"), ("19:00", "23:00")],
        tagline="A rooftop tasting-menu restaurant reimagining regional Indian food.",
        areas=["Lavelle Road", "Whitefield"],
    ),
]

FLOURISH = {
    "rooftop": "This outpost has a breezy rooftop deck.",
    "outdoor_seating": "Ask for the garden courtyard tables.",
    "live_music": "Live acoustic sets on Friday and Saturday nights.",
    "quiet": "A calmer outpost, good for conversation.",
    "kid_friendly": "Kids' menu and high chairs available.",
    "sports_screening": "Big screens for cricket and football nights.",
    "pet_friendly": "Pets welcome in the outdoor section.",
}


def _address(number: int, street: str, area: str, pin: str) -> str:
    if area.lower() in street.lower() or street.lower() in area.lower():
        return f"{number}, {street}, Bengaluru {pin}"
    return f"{number}, {street}, {area}, Bengaluru {pin}"


def _tables(rng: random.Random, seats_range: tuple[int, int], mix: dict[int, float]) -> dict[str, int]:
    target = rng.randint(*seats_range)
    counts: dict[str, int] = {}
    for size, share in mix.items():
        counts[str(size)] = max(1, round(target * share / size))
    return counts


def build() -> list[dict]:
    rng = random.Random(7)
    restaurants: list[dict] = []
    for b in BRANDS:
        for area in b["areas"]:
            lat, lng, pin, hotness, streets = AREAS[area]
            table_mix = _tables(rng, b["seats"], b["mix"])
            seats = sum(int(size) * n for size, n in table_mix.items())
            pdr_prob, (pdr_lo, pdr_hi) = b["pdr"]
            pdr_capacity = rng.randint(pdr_lo, pdr_hi) if pdr_lo and rng.random() < pdr_prob else None

            features = list(b["features"])
            for f in b["optional"]:
                if rng.random() < 0.5 and f not in features:
                    features.append(f)
            if pdr_capacity and "private_dining" not in features:
                features.append("private_dining")
            if not pdr_capacity and "private_dining" in features:
                features.remove("private_dining")

            rating = round(min(4.9, max(3.7, b["rating"] + rng.gauss(0, 0.15))), 1)
            popularity = round(min(1.0, max(0.3, 0.55 * hotness + 0.6 * (rating - 3.8) + rng.uniform(-0.05, 0.08))), 2)
            reviews = int(200 + popularity * rng.randint(1500, 4800))

            flourish = [FLOURISH[f] for f in features if f in FLOURISH]
            rng.shuffle(flourish)
            extra = flourish[:1]
            if pdr_capacity:
                extra.append(f"Private dining room for up to {pdr_capacity} guests.")
            description = " ".join([b["tagline"], *extra])

            slug = (b["brand"].lower().replace("&", "and").replace("'", "").replace(".", "").replace(" ", "-")
                    + "-" + area.lower().replace(" ", "-"))
            restaurants.append({
                "id": slug,
                "name": f"{b['brand']}, {area}",
                "brand": b["brand"],
                "cuisine": b["cuisine"],
                "cuisines": b["cuisines"],
                "tags": b["tags"],
                "area": area,
                "address": _address(rng.randint(1, 480), rng.choice(streets), area, pin),
                "lat": round(lat + rng.uniform(-0.004, 0.004), 5),
                "lng": round(lng + rng.uniform(-0.004, 0.004), 5),
                "price_band": b["price_band"],
                "avg_cost_for_two": int(round(b["cost_for_two"] * rng.uniform(0.9, 1.12), -1)),
                "rating": rating,
                "reviews": reviews,
                "seats": seats,
                "table_mix": table_mix,
                "private_dining_capacity": pdr_capacity,
                "hours": [list(h) for h in b["hours"]],
                "features": sorted(features),
                "dietary": sorted(b["dietary"]),
                "good_for": b["good_for"],
                "signature_dishes": b["dishes"],
                "description": description,
                "popularity": popularity,
            })
    return restaurants


def main() -> None:
    restaurants = build()
    payload = {
        "brand": "GoodFoods",
        "city": "Bengaluru",
        "areas": {name: {"lat": v[0], "lng": v[1], "pincode": v[2], "aliases": AREA_ALIASES.get(name, [])}
                  for name, v in AREAS.items()},
        "restaurants": restaurants,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    seats = sum(r["seats"] for r in restaurants)
    print(f"wrote {len(restaurants)} restaurants ({len({r['brand'] for r in restaurants})} brands, "
          f"{len({r['area'] for r in restaurants})} areas, {seats} seats) -> {OUT}")


if __name__ == "__main__":
    main()
