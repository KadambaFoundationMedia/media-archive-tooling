"""Baserow reference provider adapter with offline caching and seed data fallback."""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import httpx

from ..common.ascii_latin import to_ascii_latin

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "assets"
DEFAULT_CATEGORIES_PATH = ASSETS_DIR / "default_categories.json"
DEFAULT_LOCATIONS_PATH = ASSETS_DIR / "default_locations.json"
COUNTRIES_PATH = ASSETS_DIR / "country_codes.json"


class BaserowReferenceProvider:
    """Provides reference metadata (categories, places, countries) from Baserow or local cache."""

    def __init__(
        self,
        api_url: str = "https://api.baserow.io",
        api_token: Optional[str] = None,
        media_table_id: Optional[str] = None,
        category_table_id: Optional[str] = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.media_table_id = media_table_id
        self.category_table_id = category_table_id

        self._categories: List[Dict[str, Any]] = []
        self._locations: List[Dict[str, Any]] = []
        self._countries: Dict[str, str] = {}
        self._loaded = False

    def load_all_references(self):
        """Batch-load all reference tables into in-memory normalized indexes."""
        if self._loaded:
            return

        # 1. Load seed / fallback files first as baseline snapshot
        self._load_seed_assets()

        # 2. If API credentials provided, fetch live data from Baserow API
        if self.api_token:
            try:
                self._fetch_from_baserow_api()
            except Exception as e:
                logger.warning(f"Baserow API fetch failed (using local fallback): {e}")

        self._loaded = True

    def _load_seed_assets(self):
        if DEFAULT_CATEGORIES_PATH.exists():
            with open(DEFAULT_CATEGORIES_PATH, "r", encoding="utf-8") as f:
                self._categories = json.load(f)

        if DEFAULT_LOCATIONS_PATH.exists():
            with open(DEFAULT_LOCATIONS_PATH, "r", encoding="utf-8") as f:
                self._locations = json.load(f)

        if COUNTRIES_PATH.exists():
            with open(COUNTRIES_PATH, "r", encoding="utf-8") as f:
                self._countries = json.load(f)

    def _fetch_from_baserow_api(self):
        headers = {"Authorization": f"Token {self.api_token}"}
        with httpx.Client(timeout=15.0) as client:
            # 1. Fetch category_title reference rows with pagination
            if self.category_table_id:
                next_url: Optional[str] = f"{self.api_url}/api/database/rows/table/{self.category_table_id}/?user_field_names=true&size=200"
                fetched_categories = []
                while next_url:
                    resp = client.get(next_url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        rows = data.get("results", [])
                        for row in rows:
                            cat_name = row.get("category")
                            terms_raw = row.get("title_matching_terms") or ""
                            if isinstance(terms_raw, str):
                                terms = [t.strip() for t in terms_raw.split(",") if t.strip()]
                            elif isinstance(terms_raw, list):
                                terms = [str(t).strip() for t in terms_raw if str(t).strip()]
                            else:
                                terms = []
                            if cat_name:
                                fetched_categories.append({
                                    "category": cat_name,
                                    "title_matching_terms": ", ".join(terms),
                                    "folder_path": row.get("folder_path") or cat_name,
                                    "color": row.get("color") or "#7f8c8d"
                                })
                        next_url = data.get("next")
                    else:
                        logger.warning(f"Failed to fetch category_title: {resp.status_code} {resp.text}")
                        break
                if fetched_categories:
                    self._categories = fetched_categories

            # 2. Fetch Media table place/country rows with pagination
            if self.media_table_id:
                next_url = f"{self.api_url}/api/database/rows/table/{self.media_table_id}/?user_field_names=true&size=200"
                while next_url:
                    resp = client.get(next_url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        rows = data.get("results", [])
                        for row in rows:
                            place = row.get("place_location")
                            country = row.get("Country")
                            if place:
                                existing = self.find_place(place)
                                if not existing:
                                    iso2 = self.find_country(country) if country else None
                                    self._locations.append({
                                        "canonical_place": place,
                                        "country": country,
                                        "country_iso2": iso2,
                                        "aliases": [place.lower()]
                                    })
                        next_url = data.get("next")
                    else:
                        logger.warning(f"Failed to fetch media table: {resp.status_code} {resp.text}")
                        break

    def get_category_titles(self) -> List[Dict[str, Any]]:
        self.load_all_references()
        return self._categories

    def get_known_locations(self) -> List[Dict[str, Any]]:
        self.load_all_references()
        return self._locations

    def get_country_values(self) -> Dict[str, str]:
        self.load_all_references()
        return self._countries

    def find_place(self, place_name: str) -> Optional[Dict[str, Any]]:
        self.load_all_references()
        norm = to_ascii_latin(place_name).lower().strip()
        for loc in self._locations:
            if to_ascii_latin(loc["canonical_place"]).lower() == norm:
                return loc
            for alias in loc.get("aliases", []):
                if to_ascii_latin(alias).lower() == norm:
                    return loc
        return None

    def find_country(self, country_name: str) -> Optional[str]:
        self.load_all_references()
        if not country_name:
            return None
        norm = country_name.lower().strip()
        if norm in self._countries:
            return self._countries[norm]
        if len(norm) == 2 and norm in self._countries.values():
            return norm
        return None

    def create_missing_reference_value(
        self,
        place_name: str,
        country_name: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """Add a new place to reference data if clearly not a duplicate and write prerequisites are met."""
        self.load_all_references()
        existing = self.find_place(place_name)
        if existing:
            return False, "Already exists as known place"

        if not self.api_token or not self.media_table_id:
            return False, "Baserow write prerequisites unavailable (missing token or media table id)"

        # Perform guarded write to Baserow
        headers = {
            "Authorization": f"Token {self.api_token}",
            "Content-Type": "application/json"
        }
        url = f"{self.api_url}/api/database/rows/table/{self.media_table_id}/?user_field_names=true"
        payload: Dict[str, Any] = {"place_location": place_name}
        if country_name:
            payload["Country"] = country_name

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    iso2 = self.find_country(country_name) if country_name else None
                    new_loc = {
                        "canonical_place": place_name,
                        "country": country_name,
                        "country_iso2": iso2,
                        "aliases": [place_name.lower()]
                    }
                    self._locations.append(new_loc)
                    return True, None
                else:
                    return False, f"Baserow write failed: {resp.status_code} {resp.text}"
        except Exception as e:
            return False, f"Baserow write request error: {e}"
