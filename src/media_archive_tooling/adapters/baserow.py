"""Baserow reference provider adapter with offline caching and seed data fallback."""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
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
    ):
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.media_table_id = media_table_id

        self._categories: List[Dict[str, Any]] = []
        self._locations: List[Dict[str, Any]] = []
        self._countries: Dict[str, str] = {}
        self._loaded = False

    def load_all_references(self):
        """Batch-load all reference tables into in-memory normalized indexes."""
        if self._loaded:
            return

        # 1. Load seed / fallback files first
        self._load_seed_assets()

        # 2. If API credentials provided, attempt to fetch from Baserow API
        if self.api_token and self.media_table_id:
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
        url = f"{self.api_url}/api/database/rows/table/{self.media_table_id}/?size=200"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                # Process distinct place_location and Country values from Baserow rows
                rows = data.get("results", [])
                for row in rows:
                    place = row.get("place_location")
                    country = row.get("Country")
                    if place:
                        # Check if place already known
                        existing = self.find_place(place)
                        if not existing:
                            iso2 = self.find_country(country) if country else None
                            self._locations.append({
                                "canonical_place": place,
                                "country": country,
                                "country_iso2": iso2,
                                "aliases": [place.lower()]
                            })

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
        """Add a new place to reference data if it is clearly not a duplicate."""
        self.load_all_references()
        existing = self.find_place(place_name)
        if existing:
            return False, "Already exists as known place"

        iso2 = self.find_country(country_name) if country_name else None
        new_loc = {
            "canonical_place": place_name,
            "country": country_name,
            "country_iso2": iso2,
            "aliases": [place_name.lower()]
        }
        self._locations.append(new_loc)
        return True, None
