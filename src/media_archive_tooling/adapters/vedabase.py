"""Vedabase scripture reference validation adapter with 24-hour local SQLite cache."""
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional, Set, Tuple
from urllib.parse import urlparse

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


def _inclusive_verses(start: str, end: Optional[str] = None) -> Optional[Tuple[int, ...]]:
    if not start.isdigit() or (end is not None and not end.isdigit()):
        return None
    start_num = int(start)
    end_num = int(end) if end is not None else start_num
    if start_num < 1 or end_num < start_num:
        return None
    return tuple(range(start_num, end_num + 1))


def _reference_validation_plan(
    ref_key: str,
) -> Optional[Tuple[str, str, str, Tuple[int, ...]]]:
    """Return direct URL, chapter index URL/path prefix, and represented verses.

    Vedabase sometimes publishes several consecutive verses on one canonical page
    (for example BG 13.8-12). A reference therefore cannot be validated by assuming
    that every represented verse has its own standalone URL.
    """
    norm = ref_key.strip().upper()
    parts = norm.split("-")
    direct_url = build_vedabase_url(norm)
    if not direct_url:
        return None

    book = parts[0]
    chapter_path: Optional[str] = None
    verses: Optional[Tuple[int, ...]] = None

    if book == "BG":
        if len(parts) == 3 and parts[1].isdigit():
            verses = _inclusive_verses(parts[2])
            chapter_path = f"/en/library/bg/{int(parts[1])}/"
        elif len(parts) == 4 and parts[1].isdigit():
            verses = _inclusive_verses(parts[2], parts[3])
            chapter_path = f"/en/library/bg/{int(parts[1])}/"

    elif book == "SB":
        if len(parts) == 4 and parts[1].isdigit() and parts[2].isdigit():
            verses = _inclusive_verses(parts[3])
            chapter_path = f"/en/library/sb/{int(parts[1])}/{int(parts[2])}/"
        elif len(parts) == 5 and parts[1].isdigit() and parts[2].isdigit():
            verses = _inclusive_verses(parts[3], parts[4])
            chapter_path = f"/en/library/sb/{int(parts[1])}/{int(parts[2])}/"

    elif book == "CC":
        if len(parts) < 3:
            return None
        if parts[1].lower() in ("adi", "madhya", "antya"):
            lila = parts[1].lower()
            if len(parts) == 4 and parts[2].isdigit():
                verses = _inclusive_verses(parts[3])
                chapter_path = f"/en/library/cc/{lila}/{int(parts[2])}/"
            elif len(parts) == 5 and parts[2].isdigit():
                verses = _inclusive_verses(parts[3], parts[4])
                chapter_path = f"/en/library/cc/{lila}/{int(parts[2])}/"
        else:
            # Preserve the archive fallback where an omitted lila means Adi.
            lila = "adi"
            if len(parts) == 3 and parts[1].isdigit():
                verses = _inclusive_verses(parts[2])
                chapter_path = f"/en/library/cc/{lila}/{int(parts[1])}/"
            elif len(parts) == 4 and parts[1].isdigit():
                verses = _inclusive_verses(parts[2], parts[3])
                chapter_path = f"/en/library/cc/{lila}/{int(parts[1])}/"

    if not chapter_path or not verses:
        return None

    return direct_url, f"https://vedabase.io{chapter_path}", chapter_path, verses


def _extract_chapter_verse_coverage(html: str, chapter_path: str) -> Set[int]:
    """Extract verse numbers covered by canonical links on a Vedabase chapter page."""
    coverage: Set[int] = set()
    hrefs = re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE)

    for href in hrefs:
        parsed = urlparse(href)
        path = parsed.path
        if not path.startswith("/") and not parsed.scheme:
            path = chapter_path + path
        if not path.startswith(chapter_path):
            continue

        remainder = path[len(chapter_path):].strip("/")
        if not remainder or "/" in remainder:
            continue

        match = re.fullmatch(r"(\d+)(?:-(\d+))?", remainder)
        if not match:
            continue

        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if start < 1 or end < start:
            continue
        coverage.update(range(start, end + 1))

    return coverage


class VedabaseValidator:
    """Validates scripture references against Vedabase with local caching.

    Positive validations are cached for 24 hours. Negative results are cached only
    briefly because CDN/anti-bot/transient routing behavior can occasionally
    surface a false 404 for a real Vedabase page.
    """

    POSITIVE_TTL_SECS = 86400
    NEGATIVE_TTL_SECS = 30
    CACHE_KEY_VERSION = 2

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

    def _cache_key(self, ref_key: str) -> str:
        # Namespace cache entries by validation algorithm so an old false negative
        # cannot survive a validator correction and immediately re-poison a rerun.
        return f"v{self.CACHE_KEY_VERSION}:{ref_key}"

    def validate_scripture_reference(self, scripture_ref: str) -> Tuple[bool, str]:
        """Validate whether a canonical scripture reference exists in Vedabase.

        For ranges, the exact canonical range page is authoritative when it exists.
        If that exact URL is absent, the chapter index is used to confirm that every
        represented verse is covered by Vedabase's single-verse or grouped-verse
        canonical pages.
        """
        ref_key = scripture_ref.strip().upper()
        cache_key = self._cache_key(ref_key)
        now = time.time()

        existing_row = None
        with sqlite3.connect(str(self.cache_db)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT is_valid, status, cached_at FROM vedabase_cache WHERE ref_key = ?",
                (cache_key,),
            )
            existing_row = cursor.fetchone()
            if existing_row:
                is_valid, status, cached_at = existing_row
                ttl = self.POSITIVE_TTL_SECS if bool(is_valid) else self.NEGATIVE_TTL_SECS
                if (now - cached_at) < ttl:
                    return bool(is_valid), status or "cached"

        plan = _reference_validation_plan(ref_key)
        if not plan:
            return False, "invalid_format"
        direct_url, chapter_url, chapter_path, represented_verses = plan

        try:
            headers = {"User-Agent": "MediaArchiveTooling/1.0 (archive research)"}

            def get(url: str) -> httpx.Response:
                if self._custom_client:
                    return self._custom_client.get(url, headers=headers)
                with httpx.Client(
                    transport=self._transport,
                    timeout=10.0,
                    follow_redirects=True,
                ) as client:
                    return client.get(url, headers=headers)

            direct_response = get(direct_url)
            if direct_response.status_code == 200:
                is_valid = True
                status = "validated"
            elif direct_response.status_code != 404:
                if existing_row:
                    return bool(existing_row[0]), "validation_pending_stale"
                return False, "validation_pending_stale"
            else:
                chapter_response = get(chapter_url)
                if chapter_response.status_code == 200:
                    coverage = _extract_chapter_verse_coverage(
                        chapter_response.text,
                        chapter_path,
                    )
                    is_valid = all(verse in coverage for verse in represented_verses)
                    status = "validated" if is_valid else "not_found"
                elif chapter_response.status_code == 404:
                    is_valid = False
                    status = "not_found"
                else:
                    if existing_row:
                        return bool(existing_row[0]), "validation_pending_stale"
                    return False, "validation_pending_stale"

            with sqlite3.connect(str(self.cache_db)) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO vedabase_cache (ref_key, is_valid, status, cached_at) VALUES (?, ?, ?, ?)",
                    (cache_key, 1 if is_valid else 0, status, now),
                )
                conn.commit()

            return is_valid, status

        except Exception:
            if existing_row:
                return bool(existing_row[0]), "validation_pending_stale"
            return False, "validation_pending_stale"
