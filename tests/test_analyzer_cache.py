"""Tests for the external-lookup cache markers in laptop_analyzer_v3."""
import datetime

import laptop_analyzer_v3 as analyzer
from app_config import EXTERNAL_MISS_TTL_DAYS


def test_positive_entry_is_always_fresh():
    assert analyzer._is_fresh_cache_entry({"current_usd": 700}) is True


def test_legacy_entry_without_marker_stays_valid():
    """Caches written before negative caching existed must keep working."""
    assert analyzer._is_fresh_cache_entry({"score": 82, "url": "u"}) is True


def test_empty_entry_is_not_fresh():
    assert analyzer._is_fresh_cache_entry({}) is False
    assert analyzer._is_fresh_cache_entry(None) is False


def test_recent_miss_suppresses_relookup():
    entry = analyzer._miss_entry()
    assert analyzer._is_miss(entry) is True
    assert analyzer._is_fresh_cache_entry(entry) is True


def test_expired_miss_allows_relookup():
    stale = datetime.date.today() - datetime.timedelta(days=EXTERNAL_MISS_TTL_DAYS + 1)
    entry = {analyzer._MISS_MARKER: stale.isoformat()}
    assert analyzer._is_fresh_cache_entry(entry) is False


def test_corrupt_miss_marker_allows_relookup():
    assert analyzer._is_fresh_cache_entry({analyzer._MISS_MARKER: "not-a-date"}) is False
