"""WHERE entity resolution: canonical places, countries, and ISO alpha-2 mapping."""
import json
import re
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from rapidfuzz import fuzz

from ..models import WhereResult, ResolutionState, Evidence
from ...common.ascii_latin import to_ascii_latin, sanitize_filename_token

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "assets"
LOCATIONS_PATH = ASSETS_DIR / "default_locations.json"
COUNTRIES_PATH = ASSETS_DIR / "country_codes.json"


class WhereResolver:
    def __init__(
        self,
        locations_data: Optional[List[Dict[str, Any]]] = None,
        countries_data: Optional[Dict[str, str]] = None,
        location_lookup_provider: Optional[Any] = None,
        fuzzy_threshold: float = 85.0,
        near_tie_margin: float = 5.0,
    ):
        self.locations: List[Dict[str, Any]] = locations_data or self._load_locations()
        self.countries: Dict[str, str] = countries_data or self._load_countries()
        self.location_lookup_provider = location_lookup_provider
        self.fuzzy_threshold = fuzzy_threshold
        self.near_tie_margin = near_tie_margin
        self._alias_list = []
        for loc in self.locations:
            all_aliases = [loc["canonical_place"]] + loc.get("aliases", [])
            for a in all_aliases:
                self._alias_list.append((a, loc))
        self._alias_list.sort(key=lambda x: len(x[0]), reverse=True)

    def _load_locations(self) -> List[Dict[str, Any]]:
        if LOCATIONS_PATH.exists():
            with open(LOCATIONS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _load_countries(self) -> Dict[str, str]:
        if COUNTRIES_PATH.exists():
            with open(COUNTRIES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def get_country_iso2(self, country_name: str) -> Optional[str]:
        if not country_name:
            return None
        norm = country_name.lower().strip()
        if norm in self.countries:
            return self.countries[norm]
        if len(norm) == 2 and norm in self.countries.values():
            return norm
        return None

    def resolve(
        self,
        filename_text: str,
        parent_folder: str = "",
        ancestor_folders: Optional[List[str]] = None
    ) -> Tuple[WhereResult, str]:
        """Resolve WHERE entity from filename and folder context."""
        ancestors = ancestor_folders or []
        combined_context = " ".join([filename_text, parent_folder] + ancestors)

        matched_loc = None
        matched_raw = None
        matched_in_filename = False

        # 1. Exact alias match in filename (longest alias first)
        for alias, loc in self._alias_list:
            pattern = rf"(?:^|[\s_.-]){re.escape(alias)}(?=[_.\s-]|$)"
            m = re.search(pattern, filename_text, re.IGNORECASE)
            if m:
                matched_loc = loc
                matched_raw = m.group(0).strip(" _.-")
                matched_in_filename = True
                span = m.span()
                filename_text = filename_text[:span[0]] + " " + filename_text[span[1]:]
                break

        # If not in filename, check folder context
        if not matched_loc:
            for alias, loc in self._alias_list:
                pattern = rf"(?:^|[\s_.-]){re.escape(alias)}(?=[_.\s-]|$)"
                m = re.search(pattern, combined_context, re.IGNORECASE)
                if m:
                    matched_loc = loc
                    matched_raw = m.group(0).strip(" _.-")
                    matched_in_filename = False
                    break

        if matched_loc:
            canonical_place = matched_loc["canonical_place"]
            country_name = matched_loc.get("country")
            iso2 = matched_loc.get("country_iso2") or self.get_country_iso2(country_name or "")
            state = ResolutionState.EXACT if matched_in_filename else ResolutionState.STRONG
            res = WhereResult(
                place_location=canonical_place,
                country=country_name,
                country_iso2=iso2.lower() if iso2 else None,
                state=state,
                evidence=[Evidence(
                    source="filename_exact" if matched_in_filename else "folder_context",
                    raw_value=matched_raw or canonical_place,
                    details=f"resolved to '{canonical_place}-{iso2}'"
                )]
            )
            return res, filename_text.strip()

        # 2. Check for known country names in filename if no place found (e.g. "Sweden", "Serbia")
        for country_name, iso2 in sorted(self.countries.items(), key=lambda x: len(x[0]), reverse=True):
            if len(country_name) < 4:
                continue
            pattern = rf"(?:^|[\s_.-]){re.escape(country_name)}(?=[_.\s-]|$)"
            m = re.search(pattern, filename_text, re.IGNORECASE)
            if m:
                canon_place = country_name.title()
                raw_token = m.group(0).strip(" _.-")
                span = m.span()
                filename_text = filename_text[:span[0]] + " " + filename_text[span[1]:]
                return WhereResult(
                    place_location=canon_place,
                    country=canon_place,
                    country_iso2=iso2.lower(),
                    state=ResolutionState.STRONG,
                    evidence=[Evidence(source="filename_country_match", raw_value=raw_token, details=f"matched country '{canon_place}-{iso2}'")]
                ), filename_text.strip()

        # 3. Bounded Fuzzy Matching across known places
        tokens = [t.strip() for t in re.split(r"[\s_\-.]+", filename_text) if len(t.strip()) >= 4]
        fuzzy_matches = []

        for token in tokens:
            token_clean = to_ascii_latin(token).lower()
            for loc in self.locations:
                for alias in [loc["canonical_place"]] + loc.get("aliases", []):
                    alias_clean = to_ascii_latin(alias).lower()
                    ratio = fuzz.ratio(token_clean, alias_clean)
                    if ratio >= self.fuzzy_threshold:
                        fuzzy_matches.append((ratio, loc, token))

        fuzzy_matches.sort(key=lambda x: x[0], reverse=True)
        if fuzzy_matches:
            best_ratio, best_loc, best_token = fuzzy_matches[0]
            # Check for near-tie with a DIFFERENT canonical place
            near_tie = False
            second_match = None
            for r, l, t in fuzzy_matches[1:]:
                if l["canonical_place"] != best_loc["canonical_place"]:
                    if (best_ratio - r) < self.near_tie_margin:
                        near_tie = True
                        second_match = (r, l, t)
                    break

            canonical_place = best_loc["canonical_place"]
            country_name = best_loc.get("country")
            iso2 = best_loc.get("country_iso2") or self.get_country_iso2(country_name or "")
            pattern = rf"(?:^|[\s_.-]){re.escape(best_token)}(?=[_.\s-]|$)"
            m = re.search(pattern, filename_text)
            if m:
                span = m.span()
                filename_text = filename_text[:span[0]] + " " + filename_text[span[1]:]

            if near_tie and second_match:
                sec_ratio, sec_loc, sec_tok = second_match
                sec_iso = sec_loc.get("country_iso2") or self.get_country_iso2(sec_loc.get("country") or "")
                res = WhereResult(
                    place_location=canonical_place,
                    country=country_name,
                    country_iso2=iso2.lower() if iso2 else None,
                    state=ResolutionState.AMBIGUOUS,
                    alternatives=[f"{sec_loc['canonical_place']}-{sec_iso or ''}".strip("-")],
                    evidence=[Evidence(
                        source="bounded_fuzzy_near_tie",
                        raw_value=best_token,
                        details=f"near-tie ({best_ratio:.1f} vs {sec_ratio:.1f}) between '{canonical_place}' and '{sec_loc['canonical_place']}'"
                    )]
                )
                return res, filename_text.strip()
            else:
                res = WhereResult(
                    place_location=canonical_place,
                    country=country_name,
                    country_iso2=iso2.lower() if iso2 else None,
                    state=ResolutionState.PROVISIONAL,
                    evidence=[Evidence(
                        source="bounded_fuzzy",
                        raw_value=best_token,
                        details=f"fuzzy matched '{canonical_place}' with score {best_ratio:.1f}"
                    )]
                )
                return res, filename_text.strip()

        # 4. Online location lookup fallback
        NON_LOCATION_WORDS = {
            "kks", "sb", "bg", "cc", "part", "track", "audio", "video", "clean", "livestream",
            "duben", "rijen", "recording", "class", "lecture", "program", "festival", "seminar",
            "morning", "evening", "reading", "bhajan", "kirtan", "sunday", "feast", "camp",
            "summer", "winter", "disc", "final", "copy", "recovered", "damaged", "corrupted",
            "with", "from", "that", "this", "edit", "edited", "remastered", "lekce", "prednaska"
        }
        if self.location_lookup_provider:
            queries_run = 0
            for token in tokens:
                if queries_run >= 2:
                    break
                token_clean = to_ascii_latin(token).strip(" _.-")
                if (
                    len(token_clean) >= 4
                    and not token_clean.isdigit()
                    and token_clean.lower() not in NON_LOCATION_WORDS
                    and not re.match(r"^(?:ID-[0-9a-fA-F]{8}|[A-Z]\d{3,4}[A-Z]?)$", token_clean)
                ):
                    queries_run += 1
                    loc_data = self.location_lookup_provider.lookup(token_clean)
                    if loc_data and loc_data.get("canonical_place"):
                        canon_place = loc_data["canonical_place"]
                        c_name = loc_data.get("country")
                        iso2 = loc_data.get("country_iso2")
                        pattern = rf"(?:^|[\s_.-]){re.escape(token)}(?=[_.\s-]|$)"
                        m = re.search(pattern, filename_text)
                        if m:
                            span = m.span()
                            filename_text = filename_text[:span[0]] + " " + filename_text[span[1]:]
                        res = WhereResult(
                            place_location=canon_place,
                            country=c_name,
                            country_iso2=iso2.lower() if iso2 else None,
                            state=ResolutionState.PROVISIONAL,
                            evidence=[Evidence(
                                source="online_location_lookup",
                                raw_value=token,
                                details=f"geocoded to '{canon_place}-{iso2}'"
                            )]
                        )
                        return res, filename_text.strip()

        return WhereResult(
            place_location=None,
            country=None,
            country_iso2=None,
            state=ResolutionState.UNRESOLVED,
            evidence=[]
        ), filename_text.strip()
