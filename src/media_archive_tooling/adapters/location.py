"""Online location lookup provider adapter with local caching."""
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any
import httpx


class LocationLookupProvider:
    """Online location lookup provider with local SQLite 30-day caching."""

    def __init__(
        self,
        cache_db: Path = Path(".renamer/location_cache.db"),
        client: Optional[httpx.Client] = None,
        transport: Optional[httpx.BaseTransport] = None,
        provider_url: str = "https://nominatim.openstreetmap.org/search",
    ):
        self.cache_db = cache_db
        self.cache_db.parent.mkdir(parents=True, exist_ok=True)
        self.provider_url = provider_url
        self._custom_client = client
        self._transport = transport
        self._last_request_time: float = 0.0
        self.min_request_interval: float = 1.0
        self._init_cache()

    def _init_cache(self):
        with sqlite3.connect(str(self.cache_db)) as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS location_lookup_cache (
                query TEXT PRIMARY KEY,
                canonical_place TEXT,
                country TEXT,
                country_iso2 TEXT,
                cached_at REAL NOT NULL
            )
            """)
            conn.commit()

    def _save_to_cache(self, query: str, place: str, country: str, iso2: str, cached_at: float):
        with sqlite3.connect(str(self.cache_db)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO location_lookup_cache (query, canonical_place, country, country_iso2, cached_at) VALUES (?, ?, ?, ?, ?)",
                (query, place, country, iso2, cached_at)
            )
            conn.commit()

    def lookup(self, query: str) -> Optional[Dict[str, str]]:
        """Look up place and country from local cache or online provider."""
        q_norm = query.strip().lower()
        if not q_norm or len(q_norm) < 3:
            return None

        now = time.time()
        ttl = 30 * 86400  # 30 days cache for geocoded locations

        # 1. Check local cache
        with sqlite3.connect(str(self.cache_db)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT canonical_place, country, country_iso2, cached_at FROM location_lookup_cache WHERE query = ?", (q_norm,))
            row = cursor.fetchone()
            if row:
                place, country, iso2, cached_at = row
                if (now - cached_at) < ttl:
                    if place:
                        return {"canonical_place": place, "country": country, "country_iso2": iso2}
                    else:
                        # Cached negative result
                        return None

        # 2. Rate limit online provider
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self._last_request_time = time.time()

        params = {
            "q": query.strip(),
            "format": "json",
            "addressdetails": "1",
            "limit": "1"
        }
        url = f"{self.provider_url}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": "MediaArchiveTooling/1.0 (archive-research-renamer)"}

        try:
            if self._custom_client:
                resp = self._custom_client.get(url, headers=headers)
            else:
                with httpx.Client(transport=self._transport, timeout=10.0) as client:
                    resp = client.get(url, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list) and len(data) > 0:
                    item = data[0]
                    address = item.get("address", {})
                    place = (
                        address.get("city")
                        or address.get("town")
                        or address.get("municipality")
                        or address.get("village")
                        or item.get("name")
                    )
                    country = address.get("country")
                    iso2 = (address.get("country_code") or "").lower()
                    if place and iso2:
                        res = {"canonical_place": place, "country": country or place, "country_iso2": iso2}
                        self._save_to_cache(q_norm, place, country or place, iso2, now)
                        return res

                # Cache negative lookup so we do not repeatedly pound provider
                self._save_to_cache(q_norm, "", "", "", now)
                return None
            else:
                return None
        except Exception:
            return None
