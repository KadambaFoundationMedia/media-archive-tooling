import sqlite3
import time
import tempfile
import shutil
from pathlib import Path
import httpx
import pytest

from media_archive_tooling.adapters.vedabase import VedabaseValidator, build_vedabase_url


@pytest.fixture
def vedabase_env():
    temp_dir = Path(tempfile.mkdtemp())
    cache_db = temp_dir / "vedabase_test.db"
    yield cache_db
    shutil.rmtree(temp_dir)


def test_build_vedabase_url():
    assert build_vedabase_url("SB-1-4-5") == "https://vedabase.io/en/library/sb/1/4/5/"
    assert build_vedabase_url("BG-3-12") == "https://vedabase.io/en/library/bg/3/12/"
    assert build_vedabase_url("BG-13-8-12") == "https://vedabase.io/en/library/bg/13/8-12/"
    assert build_vedabase_url("CC-Adi-1-1") == "https://vedabase.io/en/library/cc/adi/1/1/"
    assert build_vedabase_url("INVALID") is None


def test_vedabase_validation_success_and_caching(vedabase_env):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        if "sb/1/4/5" in str(request.url):
            return httpx.Response(200, text="<html>Verse text</html>")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    is_valid, status = validator.validate_scripture_reference("SB-1-4-5")
    assert is_valid is True
    assert status == "validated"
    assert len(called) == 1

    is_valid_cached, status_cached = validator.validate_scripture_reference("SB-1-4-5")
    assert is_valid_cached is True
    assert status_cached in ("validated", "cached")
    assert len(called) == 1


def test_bg_grouped_range_uses_canonical_range_page(vedabase_env):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        if str(request.url) == "https://vedabase.io/en/library/bg/13/8-12/":
            return httpx.Response(200, text="Bg. 13.8-12")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    assert validator.validate_scripture_reference("BG-13-8-12") == (True, "validated")
    assert called == ["https://vedabase.io/en/library/bg/13/8-12/"]


def test_range_falls_back_to_chapter_coverage_when_exact_range_page_is_absent(vedabase_env):
    called = []
    chapter_html = """
    <a href="/en/library/bg/13/6-7/">6-7</a>
    <a href="/en/library/bg/13/8-12/">8-12</a>
    <a href="/en/library/bg/13/13/">13</a>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        if str(request.url) == "https://vedabase.io/en/library/bg/13/8-10/":
            return httpx.Response(404, text="Not Found")
        if str(request.url) == "https://vedabase.io/en/library/bg/13/":
            return httpx.Response(200, text=chapter_html)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    assert validator.validate_scripture_reference("BG-13-8-10") == (True, "validated")
    assert called == [
        "https://vedabase.io/en/library/bg/13/8-10/",
        "https://vedabase.io/en/library/bg/13/",
    ]


def test_single_verse_inside_grouped_vedabase_page_is_valid(vedabase_env):
    chapter_html = '<a href="/en/library/bg/13/8-12/">8-12</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://vedabase.io/en/library/bg/13/8/":
            return httpx.Response(404, text="Not Found")
        if str(request.url) == "https://vedabase.io/en/library/bg/13/":
            return httpx.Response(200, text=chapter_html)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    assert validator.validate_scripture_reference("BG-13-8") == (True, "validated")


def test_sb_and_cc_exact_ranges_use_canonical_range_pages(vedabase_env):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, text="Range page")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    assert validator.validate_scripture_reference("SB-1-1-2-4") == (True, "validated")
    assert validator.validate_scripture_reference("CC-ADI-9-48-50") == (True, "validated")
    assert called == [
        "https://vedabase.io/en/library/sb/1/1/2-4/",
        "https://vedabase.io/en/library/cc/adi/9/48-50/",
    ]


def test_vedabase_validation_not_found(vedabase_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    is_valid, status = validator.validate_scripture_reference("SB-99-99-99")
    assert is_valid is False
    assert status == "not_found"


def test_range_not_found_when_chapter_coverage_has_a_gap(vedabase_env):
    chapter_html = """
    <a href="/en/library/bg/13/8-9/">8-9</a>
    <a href="/en/library/bg/13/11-12/">11-12</a>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://vedabase.io/en/library/bg/13/8-12/":
            return httpx.Response(404, text="Not Found")
        if str(request.url) == "https://vedabase.io/en/library/bg/13/":
            return httpx.Response(200, text=chapter_html)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    assert validator.validate_scripture_reference("BG-13-8-12") == (False, "not_found")


def test_descending_range_is_invalid_format(vedabase_env):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, text="Verse text")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    assert validator.validate_scripture_reference("BG-13-12-8") == (False, "invalid_format")
    assert called == []


def test_legacy_false_negative_cache_does_not_poison_corrected_validator(vedabase_env):
    validator = VedabaseValidator(cache_db=vedabase_env)
    with sqlite3.connect(str(vedabase_env)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO vedabase_cache (ref_key, is_valid, status, cached_at) VALUES (?, ?, ?, ?)",
            ("BG-13-8-12", 0, "not_found", time.time()),
        )
        conn.commit()

    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, text="Bg. 13.8-12")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    corrected = VedabaseValidator(cache_db=vedabase_env, client=client)

    assert corrected.validate_scripture_reference("BG-13-8-12") == (True, "validated")
    assert called == ["https://vedabase.io/en/library/bg/13/8-12/"]


def test_vedabase_negative_cache_expires_quickly_and_recovers(vedabase_env):
    called = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        if called <= 2:
            return httpx.Response(404, text="Not Found")
        return httpx.Response(200, text="Bg. 13.8-12")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    assert validator.validate_scripture_reference("BG-13-8-12") == (False, "not_found")
    assert called == 2

    with sqlite3.connect(str(vedabase_env)) as conn:
        conn.execute(
            "UPDATE vedabase_cache SET cached_at = ? WHERE ref_key = ?",
            (
                time.time() - VedabaseValidator.NEGATIVE_TTL_SECS - 1,
                "v2:BG-13-8-12",
            ),
        )
        conn.commit()

    assert validator.validate_scripture_reference("BG-13-8-12") == (True, "validated")
    assert called == 3


def test_vedabase_network_failure_returns_pending_stale(vedabase_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    is_valid, status = validator.validate_scripture_reference("BG-2-13")
    assert is_valid is False
    assert status == "validation_pending_stale"


def test_vedabase_cache_refresh_after_ttl(vedabase_env):
    called = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(200, text="OK")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    validator.validate_scripture_reference("SB-1-1-1")
    assert called == 1

    with sqlite3.connect(str(vedabase_env)) as conn:
        conn.execute(
            "UPDATE vedabase_cache SET cached_at = ? WHERE ref_key = ?",
            (time.time() - 90000, "v2:SB-1-1-1"),
        )
        conn.commit()

    validator.validate_scripture_reference("SB-1-1-1")
    assert called == 2
