"""Online location lookup provider adapter with local caching."""
import sqlite3
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any


class LocationLookupProvider:
    def __init__(self, cache_db: Path = Path(".renamer/location_cache.db")):
        self.cache_db = cache_db
        self.cache_db.parent.mkdir(parents=True, exist_ok=True)
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

    def lookup(self, query: str) -> Optional[Dict[str, str]]:
        """Look up place and country from online provider or local cache."""
        q_norm = query.strip().lower()
        now = time.time()
        ttl = 30 * 86400  # 30 days cache for geocoded locations

        with sqlite3.connect(str(self.cache_db)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT canonical_place, country, country_iso2, cached_at FROM location_lookup_cache WHERE query = ?", (q_norm,))
            row = cursor.fetchone()
            if row:
                place, country, iso2, cached_at = row
                if (now - cached_at) < ttl and place:
                    return {"canonical_place": place, "country": country, "country_iso2": iso2}

        return None
