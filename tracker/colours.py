import re


MOBILE_BG_COLOUR_TRANSLATIONS = {
    "бежов": "Beige",
    "бежов металик": "Beige metallic",
    "бял": "White",
    "бял перла": "Pearl white",
    "бордо": "Burgundy",
    "графит": "Graphite",
    "жълт": "Yellow",
    "зелен": "Green",
    "златист": "Gold",
    "кафяв": "Brown",
    "лилав": "Purple",
    "металик": "Metallic",
    "многоцветен": "Multicolor",
    "оранжев": "Orange",
    "перла": "Pearl",
    "светло бежов": "Light beige",
    "светло зелен": "Light green",
    "светло син": "Light blue",
    "светло сив": "Light gray",
    "сив": "Gray",
    "син": "Blue",
    "син металик": "Blue metallic",
    "сребрист": "Silver",
    "сребърен": "Silver",
    "т зелен": "Dark green",
    "тъмно бежов": "Dark beige",
    "тъмно зелен": "Dark green",
    "тъмно сив": "Dark gray",
    "тъмно син": "Dark blue",
    "тъмно син мет": "Dark blue metallic",
    "тъмно син металик": "Dark blue metallic",
    "тъмно червен": "Dark red",
    "хамелеон": "Chameleon",
    "шампанско": "Champagne",
    "червен": "Red",
    "черен": "Black",
    "черен металик": "Black metallic",
    "виолетов": "Violet",
}

_ENGLISH_COLOURS = {
    value.casefold(): value for value in MOBILE_BG_COLOUR_TRANSLATIONS.values()
}


def mobile_bg_colour(value):
    """Return a known Mobile.bg colour in English; ignore unrelated spec values."""
    key = re.sub(r"[.,]", " ", (value or "").casefold())
    key = " ".join(key.split())
    return MOBILE_BG_COLOUR_TRANSLATIONS.get(key, _ENGLISH_COLOURS.get(key, ""))
