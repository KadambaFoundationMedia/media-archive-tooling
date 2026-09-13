"""Vedabase scripture reference validation adapter with 24-hour local SQLite cache."""
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Tuple
import httpx


def build_vedabase_url(ref_key: str) -> Optional[str]:
    """Convert a canonical scripture reference key into a Vedabase library URL.

    For range keys this returns the conventional combined range URL. Runtime
    validation does not rely on that URL existing; it validates the start and end
    verse pages individually via ``_build_validation_urls``.
    """
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
    if book == "BG" and len(parts) >= 3:
        chapter = parts[1]
        verse = "-".join(parts[2:])
        return f"https://vedabase.io/en/library/bg/{chapter}/{verse}/"
    if book == "CC" and len(parts) >= 3:
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


def _build_validation_urls(ref_key: str) -> Optional[List[str]]:
    """Return concrete Vedabase verse pages that prove a reference exists.

    Archive range syntax represents a span, not a separate scripture identifier:
    ``BG-1-1-3`` means BG 1.1 through 1.3, while ``SB-1-1-2-4`` means SB
    1.1.2 through 1.1.4. Vedabase may not expose a dedicated page for the
    combined range, so a range is validated by checking both endpoints.
    """
    norm = ref_key.strip().upper()
    parts = norm.split("-")
    if not parts:
        return None

    book = parts[0]
    urls: List[str] = []

    if book == "BG":
        if len(parts) == 3:
            chapter, verse = parts[1], parts[2]
            urls = [f"https://vedabase.io/en/library/bg/{chapter}/{verse}/"]
        elif len(parts) == 4:
            chapter, start, end = parts[1], parts[2], parts[3]
            urls = [
                f"https://vedabase.io/en/library/bg/{chapter}/{start}/",
                f"https://vedabase.io/en/library/bg/{chapter}/{end}/",
            ]
        else:
            return None

    elif book == "SB":
        if len(parts) == 4:
            canto, chapter, verse = parts[1], parts[2], parts[3]
            urls = [f"https://vedabase.io/en/library/sb/{canto}/{chapter}/{verse}/"]
        elif len(parts) == 5:
            canto, chapter, start, end = parts[1], parts[2], parts[3], parts[4]
            urls = [
                f"https://vedabase.io/en/library/sb/{canto}/{chapter}/{start}/",
                f"https://vedabase.io/en/library/sb/{canto}/{chapter}/{end}/",
            ]
        else:
            return None

    elif book == "CC":
        if len(parts) < 3:
            return None
        if parts[1].lower() in ("adi", "madhya", "antya"):
            lila = parts[1].lower()
            if len(parts) == 4:
                chapter, verse = parts[2], parts[3]
                urls = [f"https://vedabase.io/en/library/cc/{lila}/{chapter}/{verse}/"]
            elif len(parts) == 5:
                chapter, start, end = parts[2], parts[3], parts[4]
                urls = [
                    f"https://vedabase.io/en/library/cc/{lila}/{chapter}/{start}/",
                    f"https://vedabase.io/en/library/cc/{lila}/{chapter}/{end}/",
                ]
            else:
                return None
        else:
            # Preserve the existing archive fallback where an omitted lila means Adi.
            lila = "adi"
            if len(parts) == 3:
                chapter, verse = parts[1], parts[2]
                urls = [f"https://vedabase.io/en/library/cc/{lila}/{chapter}/{verse}/"]
            elif len(parts) == 4:
                chapter, start, end = parts[1], parts[2], parts[3]
                urls = [
                    f"https://vedabase.io/en/library/cc/{lila}/{chapter}/{start}/",
                    f"https://vedabase.io/en/library/cc/{lila}/{chapter}/{end}/",
                ]
            else:
                return None
    else:
        return None

    # A one-verse range such as 1-1 does not need a duplicate request.
    return list(dict.fromkeys(urls))


class VedabaseValidator:
    """Validates scripture references against Vedabase with local caching.

    Positive validations are cached for 24 hours. Negative 404 results are cached
    only briefly because CDN/anti-bot/transient routing behavior can occasionally
    surface a false 404 for a real Vedabase page. A transient negative must not
    keep a valid scripture reference in the human-review queue for a full day.
    """

    POSITIVE_TTL_SECS = 86400
    NEGATIVE_TTL_SECS = 30

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
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(vedabase_cache)")
            columns = [col[1] for col in cursor.fetchall()]
            if "status" not in columns:
                cursor.execute("ALTER TABLE vedabase_cache ADD COLUMN status TEXT DEFAULT 'validated'")
            conn.commit()

    def validate_scripture_reference(self, scripture_ref: str) -> Tuple[bool, str]:
        """Validate whether a canonical scripture reference exists in Vedabase.

        Returns:
            (is_valid, validation_status)
            where validation_status can be 'validated', 'cached', 'not_found',
            'invalid_format', or 'validation_pending_stale'.
        """
        ref_key = scripture_ref.strip().upper()
        now = time.time()

        existing_row = None
        with sqlite3.connect(str(self.cache_db)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_valid, status, cached_at FROM vedabase_cache WHERE ref_key = ?", (ref_key,))
            existing_row = cursor.fetchone()
            if existing_row:
                is_valid, status, cached_at = existing_row
                ttl = self.POSITIVE_TTL_SECS if bool(is_valid) else self.NEGATIVE_TTL_SECS
                if (now - cached_at) < ttl:
                    return bool(is_valid), status or "cached"

        urls = _build_validation_urls(ref_key)
        if not urls:
            return False, "invalid_format"

        try:
            headers = {"User-Agent": "MediaArchiveTooling/1.0 (archive research)"}

            if self._custom_client:
                responses = [self._custom_client.get(url, headers=headers) for url in urls]
            else:
                with httpx.Client(transport=self._transport, timeout=10.0, follow_redirects=True) as client:
                    responses = [client.get(url, headers=headers) for url in urls]

            if any(resp.status_code == 404 for resp in responses):
                is_valid = False
                status = "not_found"
            elif all(resp.status_code == 200 for resp in responses):
                is_valid = True
                status = "validated"
            else:
                if existing_row:
                    return bool(existing_row[0]), "validation_pending_stale"
                return False, "validation_pending_stale"

            with sqlite3.connect(str(self.cache_db)) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO vedabase_cache (ref_key, is_valid, status, cached_at) VALUES (?, ?, ?, ?)",
                    (ref_key, 1 if is_valid else 0, status, now)
                )
                conn.commit()

            return is_valid, status

        except Exception:
            if existing_row:
                return bool(existing_row[0]), "validation_pending_stale"
            return False, "validation_pending_stale"
