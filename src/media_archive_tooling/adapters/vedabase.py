"""Vedabase scripture reference validation adapter with 24-hour local SQLite cache."""
import sqlite3
import time
import re
from pathlib import Path
from typing import Optional, Tuple
import httpx


def build_vedabase_url(ref_key: str) -> Optional[str]:
    """Convert a canonical scripture reference key into a Vedabase library URL."""
    norm = ref_key.strip().upper()
    parts = norm.split("-")
    if not parts:
        return None

    book = parts[0]
    if book == "SB" and len(parts) >= 4:
        canto = parts[1]
        chapter = parts[2]
        verse = "-".join(parts[3:])
        return f"https://vedabase.io/en/library/sb/{canto}/{chapter}/{verse}/"
    elif book == "BG" and len(parts) >= 3:
        chapter = parts[1]
        verse = "-".join(parts[2:])
        return f"https://vedabase.io/en/library/bg/{chapter}/{verse}/"
    elif book == "CC" and len(parts) >= 3:
        if parts[1].lower() in ("adi", "madhya", "antya"):
            lila = parts[1].lower()
            chapter = parts[2]
            verse = "-".join(parts[3:]) if len(parts) > 3 else "1"
        else:
            lila = "adi"
            chapter = parts[1]
            verse = "-".join(parts[2:])
        return f"https://vedabase.io/en/library/cc/{lila}/{chapter}/{verse}/"
    return None


class VedabaseValidator:
    """Validates scripture references against Vedabase with 24-hour local caching."""

    def __init__(
        self,
        cache_db: Path = Path(".renamer/vedabase_cache.db"),
        client: Optional[httpx.Client] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.cache_db = cache_db
        self.cache_db.parent.mkdir(parents=True, exist_ok=True)
        self._custom_client = client
        self._transport = transport
        self._init_cache()

    def _init_cache(self):
        with sqlite3.connect(str(self.cache_db)) as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS vedabase_cache (
                ref_key TEXT PRIMARY KEY,
                is_valid INTEGER NOT NULL,
                status TEXT NOT NULL,
                cached_at REAL NOT NULL
            )
            """)
            # Ensure status column exists if migrated from old schema
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(vedabase_cache)")
            columns = [col[1] for col in cursor.fetchall()]
            if "status" not in columns:
                cursor.execute("ALTER TABLE vedabase_cache ADD COLUMN status TEXT DEFAULT 'validated'")
            conn.commit()

    def validate_scripture_reference(self, scripture_ref: str) -> Tuple[bool, str]:
        """Validate if a scripture reference exists in Vedabase.
        
        Returns:
            (is_valid, validation_status)
            where validation_status can be 'validated', 'cached', 'not_found', or 'validation_pending_stale'.
        """
        ref_key = scripture_ref.strip().upper()
        now = time.time()
        ttl = 86400  # 24 hours in seconds

        # Check local cache
        existing_row = None
        with sqlite3.connect(str(self.cache_db)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_valid, status, cached_at FROM vedabase_cache WHERE ref_key = ?", (ref_key,))
            existing_row = cursor.fetchone()
            if existing_row:
                is_valid, status, cached_at = existing_row
                if (now - cached_at) < ttl:
                    return bool(is_valid), status or "cached"

        url = build_vedabase_url(ref_key)
        if not url:
            return False, "invalid_format"

        # Query Vedabase API / web endpoint
        try:
            headers = {"User-Agent": "MediaArchiveTooling/1.0 (archive research)"}
            if self._custom_client:
                resp = self._custom_client.get(url, headers=headers)
            else:
                with httpx.Client(transport=self._transport, timeout=10.0, follow_redirects=True) as client:
                    resp = client.get(url, headers=headers)

            if resp.status_code == 200:
                is_valid = True
                status = "validated"
            elif resp.status_code == 404:
                is_valid = False
                status = "not_found"
            else:
                # Network or remote server error (e.g. 500, 502, 503)
                if existing_row:
                    return bool(existing_row[0]), "validation_pending_stale"
                return False, "validation_pending_stale"

            # Save valid/not_found result to cache
            with sqlite3.connect(str(self.cache_db)) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO vedabase_cache (ref_key, is_valid, status, cached_at) VALUES (?, ?, ?, ?)",
                    (ref_key, 1 if is_valid else 0, status, now)
                )
                conn.commit()

            return is_valid, status

        except Exception:
            # Network failure / timeout
            if existing_row:
                return bool(existing_row[0]), "validation_pending_stale"
            return False, "validation_pending_stale"

