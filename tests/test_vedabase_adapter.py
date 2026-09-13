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


def test_bg_range_validation_checks_start_and_end_verses(vedabase_env):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, text="Verse text")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    is_valid, status = validator.validate_scripture_reference("BG-13-8-12")

    assert is_valid is True
    assert status == "validated"
    assert called == [
        "https://vedabase.io/en/library/bg/13/8/",
        "https://vedabase.io/en/library/bg/13/12/",
    ]


def test_sb_and_cc_ranges_validate_endpoints(vedabase_env):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, text="Verse text")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    assert validator.validate_scripture_reference("SB-1-1-2-4") == (True, "validated")
    assert validator.validate_scripture_reference("CC-ADI-9-48-50") == (True, "validated")

    assert called == [
        "https://vedabase.io/en/library/sb/1/1/2/",
        "https://vedabase.io/en/library/sb/1/1/4/",
        "https://vedabase.io/en/library/cc/adi/9/48/",
        "https://vedabase.io/en/library/cc/adi/9/50/",
    ]


def test_vedabase_validation_not_found(vedabase_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    is_valid, status = validator.validate_scripture_reference("SB-99-99-99")
    assert is_valid is False
    assert status == "not_found"


def test_vedabase_range_not_found_when_either_endpoint_is_missing(vedabase_env):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/13/8/" in str(request.url):
            return httpx.Response(200, text="Verse 8")
        return httpx.Response(404, text="Not Found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    is_valid, status = validator.validate_scripture_reference("BG-13-8-99")
    assert is_valid is False
    assert status == "not_found"


def test_vedabase_negative_cache_expires_quickly_and_recovers(vedabase_env):
    """A transient false 404 must not poison a real scripture reference for 24 hours."""
    called = 0
    first_attempt = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called, first_attempt
        called += 1
        # First attempt: start endpoint appears missing. Second attempt: both endpoints work.
        if first_attempt:
            first_attempt = False
            return httpx.Response(404, text="Not Found")
        return httpx.Response(200, text="BG 13.8-12")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    first_valid, first_status = validator.validate_scripture_reference("BG-13-8-12")
    assert first_valid is False
    assert first_status == "not_found"
    assert called == 2  # Range validation checks both endpoints in one pass.

    # Age only the negative entry past the short negative TTL, but nowhere near 24 hours.
    with sqlite3.connect(str(vedabase_env)) as conn:
        conn.execute(
            "UPDATE vedabase_cache SET cached_at = ? WHERE ref_key = ?",
            (time.time() - VedabaseValidator.NEGATIVE_TTL_SECS - 1, "BG-13-8-12"),
        )
        conn.commit()

    recovered_valid, recovered_status = validator.validate_scripture_reference("BG-13-8-12")
    assert recovered_valid is True
    assert recovered_status == "validated"
    assert called == 4


def test_vedabase_network_failure_returns_pending_stale(vedabase_env):
    """On network failure, retain candidate with pending/stale validation without inventing validity."""
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
        conn.execute("UPDATE vedabase_cache SET cached_at = ? WHERE ref_key = ?", (time.time() - 90000, "SB-1-1-1"))
        conn.commit()

    validator.validate_scripture_reference("SB-1-1-1")
    assert called == 2
