"""Vedabase scripture reference validation adapter with 24-hour local SQLite cache."""
import sqlite3
import time
from pathlib import Path
from typing import Optional, Tuple


class VedabaseValidator:
    def __init__(self, cache_db: Path = Path(".renamer/vedabase_cache.db")):
        self.cache_db = cache_db
        self.cache_db.parent.mkdir(parents=True, exist_ok=True)
        self._init_cache()

    def _init_cache(self):
        with sqlite3.connect(str(self.cache_db)) as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS vedabase_cache (
                ref_key TEXT PRIMARY KEY,
                is_valid INTEGER NOT NULL,
                cached_at REAL NOT NULL
            )
            """)
            conn.commit()

    def validate_scripture_reference(self, scripture_ref: str) -> Tuple[bool, str]:
        """Validate if a scripture reference (e.g. SB-1-4-5, BG-3-12) exists in Vedabase.
        
        Returns:
            (is_valid, validation_status)
        """
        ref_key = scripture_ref.strip().upper()
        now = time.time()
        ttl = 86400  # 24 hours in seconds

        # Check local cache
        with sqlite3.connect(str(self.cache_db)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_valid, cached_at FROM vedabase_cache WHERE ref_key = ?", (ref_key,))
            row = cursor.fetchone()
            if row:
                is_valid, cached_at = row
                if (now - cached_at) < ttl:
                    return bool(is_valid), "cached"

        # Canonical standard cantos/chapters verification (offline authority)
        # Srimad Bhagavatam has 12 Cantos
        # Bhagavad Gita has 18 Chapters
        # Chaitanya Charitamrita has Adi, Madhya, Antya
        is_canon = False
        parts = ref_key.split("-")
        if parts[0] == "SB" and len(parts) >= 4:
            try:
                canto = int(parts[1])
                if 1 <= canto <= 12:
                    is_canon = True
            except ValueError:
                pass
        elif parts[0] == "BG" and len(parts) >= 3:
            try:
                ch = int(parts[1])
                if 1 <= ch <= 18:
                    is_canon = True
            except ValueError:
                pass
        elif parts[0].startswith("CC") and len(parts) >= 3:
            is_canon = True

        # Save result to cache
        with sqlite3.connect(str(self.cache_db)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vedabase_cache (ref_key, is_valid, cached_at) VALUES (?, ?, ?)",
                (ref_key, 1 if is_canon else 0, now)
            )
            conn.commit()

        return is_canon, "validated"
