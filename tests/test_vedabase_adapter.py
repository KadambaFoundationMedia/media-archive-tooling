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

    # 1. First validation should query network
    is_valid, status = validator.validate_scripture_reference("SB-1-4-5")
    assert is_valid is True
    assert status == "validated"
    assert len(called) == 1

    # 2. Second validation within 24h should hit cache without network query
    is_valid_cached, status_cached = validator.validate_scripture_reference("SB-1-4-5")
    assert is_valid_cached is True
    assert status_cached in ("validated", "cached")
    assert len(called) == 1  # No additional network call!


def test_vedabase_validation_not_found(vedabase_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = VedabaseValidator(cache_db=vedabase_env, client=client)

    is_valid, status = validator.validate_scripture_reference("SB-99-99-99")
    assert is_valid is False
    assert status == "not_found"


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

    # Artificially age the cached entry past 24 hours
    import sqlite3
    with sqlite3.connect(str(vedabase_env)) as conn:
        conn.execute("UPDATE vedabase_cache SET cached_at = ? WHERE ref_key = ?", (time.time() - 90000, "SB-1-1-1"))
        conn.commit()

    # Should re-query network
    validator.validate_scripture_reference("SB-1-1-1")
    assert called == 2

