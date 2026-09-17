"""Authoritative Country and ISO mapping for Tool 4."""
import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Complete ISO-2 to Standard English Country Name lookup
ISO_TO_COUNTRY: Dict[str, str] = {
    "in": "India",
    "it": "Italy",
    "cz": "Czech Republic",
    "us": "United States",
    "gb": "United Kingdom",
    "uk": "United Kingdom",
    "nl": "Netherlands",
    "de": "Germany",
    "se": "Sweden",
    "no": "Norway",
    "rs": "Serbia",
    "si": "Slovenia",
    "sk": "Slovakia",
    "za": "South Africa",
    "au": "Australia",
    "be": "Belgium",
    "pl": "Poland",
    "ch": "Switzerland",
    "es": "Spain",
    "fr": "France",
    "ru": "Russia",
    "ua": "Ukraine",
    "hu": "Hungary",
    "at": "Austria",
    "hr": "Croatia",
    "ba": "Bosnia",
    "dk": "Denmark",
    "fi": "Finland",
    "ie": "Ireland",
    "br": "Brazil",
    "mu": "Mauritius",
    "ca": "Canada",
    "nz": "New Zealand",
    "lt": "Lithuania",
    "lv": "Latvia",
    "ee": "Estonia",
    "bg": "Bulgaria",
    "ro": "Romania",
    "gr": "Greece",
    "pt": "Portugal",
    "sg": "Singapore",
    "my": "Malaysia",
    "id": "Indonesia",
    "th": "Thailand",
    "ph": "Philippines",
    "jp": "Japan",
    "kr": "South Korea",
    "cn": "China",
    "ke": "Kenya",
    "ug": "Uganda",
    "tz": "Tanzania",
    "mx": "Mexico",
    "ar": "Argentina",
    "cl": "Chile",
    "pe": "Peru",
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
    "bosnia and herzegovina": "bosnia",
    "suomi": "finland",
    "brasil": "brazil",
}

# Load assets/country_codes.json dynamically as fallback / expansion
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
    """Return standard English country name for a given 2-letter ISO code."""
    if not iso_code:
        return None
    code = iso_code.strip().lower()
    return ISO_TO_COUNTRY.get(code)


def normalize_country_name(name_or_iso: Optional[str]) -> Optional[str]:
    """Normalize country string (or ISO code) to canonical lowercase representation."""
    if not name_or_iso:
        return None
    raw = name_or_iso.strip().lower()
    if len(raw) == 2 and raw in ISO_TO_COUNTRY:
        raw = ISO_TO_COUNTRY[raw].lower()
    return COUNTRY_ALIASES.get(raw, raw)


def are_countries_equivalent(c1: Optional[str], c2: Optional[str]) -> bool:
    """Return True if c1 and c2 represent the same country semantically (e.g. 'se' vs 'Sweden')."""
    if not c1 or not c2:
        return False
    norm1 = normalize_country_name(c1)
    norm2 = normalize_country_name(c2)
    return norm1 == norm2
