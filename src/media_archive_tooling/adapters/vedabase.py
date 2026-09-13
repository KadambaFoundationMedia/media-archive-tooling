"""Vedabase scripture reference validation adapter with 24-hour local SQLite cache."""
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Tuple
import httpx


def build_vedabase_url(ref_key: str) -> Optional[str]:
    """Convert a canonical scripture reference key into a Vedabase library URL.

    For range keys this returns the conventional combined range URL. Runtime
    validation does not rely on that URL existing; it validates every verse in
    the inclusive range via ``_build_validation_urls``.
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


def _inclusive_range_urls(prefix: str, start: str, end: str) -> Optional[List[str]]:
    """Build one Vedabase URL per verse in an inclusive same-chapter range."""
    if not start.isdigit() or not end.isdigit():
        return None
    start_num = int(start)
    end_num = int(end)
    if start_num < 1 or end_num < start_num:
        return None
    return [f"{prefix}/{verse}/" for verse in range(start_num, end_num + 1)]


def _build_validation_urls(ref_key: str) -> Optional[List[str]]:
    """Return concrete Vedabase verse pages that prove a reference exists.

    Archive range syntax represents an inclusive span, not only two endpoints:
    ``BG-13-8-12`` means BG 13.8, 13.9, 13.10, 13.11, and 13.12, while
    ``SB-1-1-2-4`` means SB 1.1.2, 1.1.3, and 1.1.4. Validation therefore
    checks every verse page in the represented range.
    """
    norm = ref_key.strip().upper()
    parts = norm.split("-")
    if not parts:
        return None

    book = parts[0]
    urls: Optional[List[str]] = None

    if book == "BG":
        if len(parts) == 3:
            chapter, verse = parts[1], parts[2]
            if not chapter.isdigit() or not verse.isdigit():
                return None
            urls = [f"https://vedabase.io/en/library/bg/{int(chapter)}/{int(verse)}/"]
        elif len(parts) == 4:
            chapter, start, end = parts[1], parts[2], parts[3]
            if not chapter.isdigit():
                return None
            urls = _inclusive_range_urls(
                f"https://vedabase.io/en/library/bg/{int(chapter)}", start, end
            )
        else:
            return None

    elif book == "SB":
        if len(parts) == 4:
            canto, chapter, verse = parts[1], parts[2], parts[3]
            if not canto.isdigit() or not chapter.isdigit() or not verse.isdigit():
                return None
            urls = [
                f"https://vedabase.io/en/library/sb/{int(canto)}/{int(chapter)}/{int(verse)}/"
            ]
        elif len(parts) == 5:
            canto, chapter, start, end = parts[1], parts[2], parts[3], parts[4]
            if not canto.isdigit() or not chapter.isdigit():
                return None
            urls = _inclusive_range_urls(
                f"https://vedabase.io/en/library/sb/{int(canto)}/{int(chapter)}",
                start,
                end,
            )
        else:
            return None

    elif book == "CC":
        if len(parts) < 3:
            return None
        if parts[1].lower() in ("adi", "madhya", "antya"):
            lila = parts[1].lower()
            if len(parts) == 4:
                chapter, verse = parts[2], parts[3]
                if not chapter.isdigit() or not verse.isdigit():
                    return None
                urls = [
                    f"https://vedabase.io/en/library/cc/{lila}/{int(chapter)}/{int(verse)}/"
                ]
            elif len(parts) == 5:
                chapter, start, end = parts[2], parts[3], parts[4]
                if not chapter.isdigit():
                    return None
                urls = _inclusive_range_urls(
                    f"https://vedabase.io/en/library/cc/{lila}/{int(chapter)}",
                    start,
                    end,
                )
            else:
                return None
        else:
            # Preserve the existing archive fallback where an omitted lila means Adi.
            lila = "adi"
            if len(parts) == 3:
                chapter, verse = parts[1], parts[2]
                if not chapter.isdigit() or not verse.isdigit():
                    return None
                urls = [
                    f"https://vedabase.io/en/library/cc/{lila}/{int(chapter)}/{int(verse)}/"
                ]
            elif len(parts) == 4:
                chapter, start, end = parts[1], parts[2], parts[3]
                if not chapter.isdigit():
                    return None
                urls = _inclusive_range_urls(
                    f"https://vedabase.io/en/library/cc/{lila}/{int(chapter)}",
                    start,
                    end,
                )
            else:
                return None
    else:
        return None

    return urls


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
