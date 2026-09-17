"""Authoritative Country and ISO-3166-1 alpha-2 mapping for Tool 4."""
import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Complete ISO-3166-1 alpha-2 to Standard English Country Name lookup (all 249 official codes + standard UK alias)
ISO_TO_COUNTRY: Dict[str, str] = {
    "ad": "Andorra",
    "ae": "United Arab Emirates",
    "af": "Afghanistan",
    "ag": "Antigua and Barbuda",
    "ai": "Anguilla",
    "al": "Albania",
    "am": "Armenia",
    "ao": "Angola",
    "aq": "Antarctica",
    "ar": "Argentina",
    "as": "American Samoa",
    "at": "Austria",
    "au": "Australia",
    "aw": "Aruba",
    "ax": "Aland Islands",
    "az": "Azerbaijan",
    "ba": "Bosnia and Herzegovina",
    "bb": "Barbados",
    "bd": "Bangladesh",
    "be": "Belgium",
    "bf": "Burkina Faso",
    "bg": "Bulgaria",
    "bh": "Bahrain",
    "bi": "Burundi",
    "bj": "Benin",
    "bl": "Saint Barthelemy",
    "bm": "Bermuda",
    "bn": "Brunei",
    "bo": "Bolivia",
    "bq": "Bonaire, Sint Eustatius and Saba",
    "br": "Brazil",
    "bs": "Bahamas",
    "bt": "Bhutan",
    "bv": "Bouvet Island",
    "bw": "Botswana",
    "by": "Belarus",
    "bz": "Belize",
    "ca": "Canada",
    "cc": "Cocos Islands",
    "cd": "Democratic Republic of the Congo",
    "cf": "Central African Republic",
    "cg": "Republic of the Congo",
    "ch": "Switzerland",
    "ci": "Ivory Coast",
    "ck": "Cook Islands",
    "cl": "Chile",
    "cm": "Cameroon",
    "cn": "China",
    "co": "Colombia",
    "cr": "Costa Rica",
    "cu": "Cuba",
    "cv": "Cape Verde",
    "cw": "Curacao",
    "cx": "Christmas Island",
    "cy": "Cyprus",
    "cz": "Czech Republic",
    "de": "Germany",
    "dj": "Djibouti",
    "dk": "Denmark",
    "dm": "Dominica",
    "do": "Dominican Republic",
    "dz": "Algeria",
    "ec": "Ecuador",
    "ee": "Estonia",
    "eg": "Egypt",
    "eh": "Western Sahara",
    "er": "Eritrea",
    "es": "Spain",
    "et": "Ethiopia",
    "fi": "Finland",
    "fj": "Fiji",
    "fk": "Falkland Islands",
    "fm": "Micronesia",
    "fo": "Faroe Islands",
    "fr": "France",
    "ga": "Gabon",
    "gb": "United Kingdom",
    "gd": "Grenada",
    "ge": "Georgia",
    "gf": "French Guiana",
    "gg": "Guernsey",
    "gh": "Ghana",
    "gi": "Gibraltar",
    "gl": "Greenland",
    "gm": "Gambia",
    "gn": "Guinea",
    "gp": "Guadeloupe",
    "gq": "Equatorial Guinea",
    "gr": "Greece",
    "gs": "South Georgia and the South Sandwich Islands",
    "gt": "Guatemala",
    "gu": "Guam",
    "gw": "Guinea-Bissau",
    "gy": "Guyana",
    "hk": "Hong Kong",
    "hm": "Heard Island and McDonald Islands",
    "hn": "Honduras",
    "hr": "Croatia",
    "ht": "Haiti",
    "hu": "Hungary",
    "id": "Indonesia",
    "ie": "Ireland",
    "il": "Israel",
    "im": "Isle of Man",
    "in": "India",
    "io": "British Indian Ocean Territory",
    "iq": "Iraq",
    "ir": "Iran",
    "is": "Iceland",
    "it": "Italy",
    "je": "Jersey",
    "jm": "Jamaica",
    "jo": "Jordan",
    "jp": "Japan",
    "ke": "Kenya",
    "kg": "Kyrgyzstan",
    "kh": "Cambodia",
    "ki": "Kiribati",
    "km": "Comoros",
    "kn": "Saint Kitts and Nevis",
    "kp": "North Korea",
    "kr": "South Korea",
    "kw": "Kuwait",
    "ky": "Cayman Islands",
    "kz": "Kazakhstan",
    "la": "Laos",
    "lb": "Lebanon",
    "lc": "Saint Lucia",
    "li": "Liechtenstein",
    "lk": "Sri Lanka",
    "lr": "Liberia",
    "ls": "Lesotho",
    "lt": "Lithuania",
    "lu": "Luxembourg",
    "lv": "Latvia",
    "ly": "Libya",
    "ma": "Morocco",
    "mc": "Monaco",
    "md": "Moldova",
    "me": "Montenegro",
    "mf": "Saint Martin",
    "mg": "Madagascar",
    "mh": "Marshall Islands",
    "mk": "North Macedonia",
    "ml": "Mali",
    "mm": "Myanmar",
    "mn": "Mongolia",
    "mo": "Macau",
    "mp": "Northern Mariana Islands",
    "mq": "Martinique",
    "mr": "Mauritania",
    "ms": "Montserrat",
    "mt": "Malta",
    "mu": "Mauritius",
    "mv": "Maldives",
    "mw": "Malawi",
    "mx": "Mexico",
    "my": "Malaysia",
    "mz": "Mozambique",
    "na": "Namibia",
    "nc": "New Caledonia",
    "ne": "Niger",
    "nf": "Norfolk Island",
    "ng": "Nigeria",
    "ni": "Nicaragua",
    "nl": "Netherlands",
    "no": "Norway",
    "np": "Nepal",
    "nr": "Nauru",
    "nu": "Niue",
    "nz": "New Zealand",
    "om": "Oman",
    "pa": "Panama",
    "pe": "Peru",
    "pf": "French Polynesia",
    "pg": "Papua New Guinea",
    "ph": "Philippines",
    "pk": "Pakistan",
    "pl": "Poland",
    "pm": "Saint Pierre and Miquelon",
    "pn": "Pitcairn",
    "pr": "Puerto Rico",
    "ps": "Palestine",
    "pt": "Portugal",
    "pw": "Palau",
    "py": "Paraguay",
    "qa": "Qatar",
    "re": "Reunion",
    "ro": "Romania",
    "rs": "Serbia",
    "ru": "Russia",
    "rw": "Rwanda",
    "sa": "Saudi Arabia",
    "sb": "Solomon Islands",
    "sc": "Seychelles",
    "sd": "Sudan",
    "se": "Sweden",
    "sg": "Singapore",
    "sh": "Saint Helena",
    "si": "Slovenia",
    "sj": "Svalbard and Jan Mayen",
    "sk": "Slovakia",
    "sl": "Sierra Leone",
    "sm": "San Marino",
    "sn": "Senegal",
    "so": "Somalia",
    "sr": "Suriname",
    "ss": "South Sudan",
    "st": "Sao Tome and Principe",
    "sv": "El Salvador",
    "sx": "Sint Maarten",
    "sy": "Syria",
    "sz": "Eswatini",
    "tc": "Turks and Caicos Islands",
    "td": "Chad",
    "tf": "French Southern Territories",
    "tg": "Togo",
    "th": "Thailand",
    "tj": "Tajikistan",
    "tk": "Tokelau",
    "tl": "East Timor",
    "tm": "Turkmenistan",
    "tn": "Tunisia",
    "to": "Tonga",
    "tr": "Turkey",
    "tt": "Trinidad and Tobago",
    "tv": "Tuvalu",
    "tw": "Taiwan",
    "tz": "Tanzania",
    "ua": "Ukraine",
    "ug": "Uganda",
    "uk": "United Kingdom",
    "um": "United States Minor Outlying Islands",
    "us": "United States",
    "uy": "Uruguay",
    "uz": "Uzbekistan",
    "va": "Vatican City",
    "vc": "Saint Vincent and the Grenadines",
    "ve": "Venezuela",
    "vg": "British Virgin Islands",
    "vi": "U.S. Virgin Islands",
    "vn": "Vietnam",
    "vu": "Vanuatu",
    "wf": "Wallis and Futuna",
    "ws": "Samoa",
    "ye": "Yemen",
    "yt": "Mayotte",
    "za": "South Africa",
    "zm": "Zambia",
    "zw": "Zimbabwe",
}

# Country name aliases for normalization & equivalence comparison
COUNTRY_ALIASES: Dict[str, str] = {
    "deutschland": "germany",
    "ceska republika": "czech republic",
    "czechia": "czech republic",
    "usa": "united states",
    "united states of america": "united states",
    "great britain": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "the netherlands": "netherlands",
    "holland": "netherlands",
    "sverige": "sweden",
    "norge": "norway",
    "srbija": "serbia",
    "slovenija": "slovenia",
    "slovensko": "slovakia",
    "polska": "poland",
    "espana": "spain",
    "oesterreich": "austria",
    "hrvatska": "croatia",
    "bosnia": "bosnia and herzegovina",
    "bosnia and herzegovina": "bosnia and herzegovina",
    "suomi": "finland",
    "brasil": "brazil",
    "rossiya": "russia",
    "russian federation": "russia",
}

# Load assets/country_codes.json dynamically as expansion
_ASSETS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "assets" / "country_codes.json"
if _ASSETS_FILE.exists():
    try:
        with open(_ASSETS_FILE, "r", encoding="utf-8") as f:
            _loaded = json.load(f)
            for c_name, c_code in _loaded.items():
                code_low = c_code.strip().lower()
                name_clean = c_name.strip()
                if code_low not in ISO_TO_COUNTRY:
                    ISO_TO_COUNTRY[code_low] = name_clean.title()
                COUNTRY_ALIASES[name_clean.lower()] = ISO_TO_COUNTRY.get(code_low, name_clean).lower()
    except Exception as e:
        logger.warning(f"Could not load assets/country_codes.json: {e}")


def get_country_name_for_iso(iso_code: Optional[str]) -> Optional[str]:
    """Return standard English country name for a given 2-letter ISO code.
    Returns None if the code is not a recognized ISO-3166-1 alpha-2 code.
    Never returns the raw two-letter code.
    """
    if not iso_code:
        return None
    code = iso_code.strip().lower()
    return ISO_TO_COUNTRY.get(code)


def normalize_country_name(name_or_iso: Optional[str]) -> Optional[str]:
    """Normalize country string (or ISO code) to canonical lowercase representation.
    Returns None if an unrecognized two-letter code is provided.
    """
    if not name_or_iso:
        return None
    raw = name_or_iso.strip().lower()
    if len(raw) == 2:
        if raw in ISO_TO_COUNTRY:
            raw = ISO_TO_COUNTRY[raw].lower()
        else:
            return None
    # Baserow's established options use both spaces and hyphens for country
    # names. Treat those presentation variants as the same country.
    raw = re.sub(r"[-_]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return COUNTRY_ALIASES.get(raw, raw)


def are_countries_equivalent(c1: Optional[str], c2: Optional[str]) -> bool:
    """Return True if c1 and c2 represent the same country semantically (e.g. 'se' vs 'Sweden')."""
    if not c1 or not c2:
        return False
    norm1 = normalize_country_name(c1)
    norm2 = normalize_country_name(c2)
    if not norm1 or not norm2:
        return False
    return norm1 == norm2


_VALID_COUNTRY_NAMES = {c.strip().lower() for c in ISO_TO_COUNTRY.values()} | set(COUNTRY_ALIASES.keys())


def is_valid_country_display_name(name: Optional[str]) -> bool:
    """Return True if name is a recognized legitimate country display name and NOT a raw 2-letter code."""
    if not name or len(name.strip()) <= 2:
        return False
    norm = name.strip().lower()
    return norm in _VALID_COUNTRY_NAMES or norm in COUNTRY_ALIASES
