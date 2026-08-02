"""Unit tests for the trending-keywords fetcher (no network)."""

import pytest

import trending


@pytest.fixture(autouse=True)
def reset_cache():
    trending._CACHE["terms"] = []
    trending._CACHE["fetched_at"] = 0.0
    yield
    trending._CACHE["terms"] = []
    trending._CACHE["fetched_at"] = 0.0


FEED_XML = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Daily Search Trends</title>
    <item><title>freddy peralta</title></item>
    <item><title>fever</title></item>
    <item><title>robert &amp; stock</title></item>
    <item><title>  </title></item>
  </channel>
</rss>"""


def _fake_get_ok(*args, **kwargs):
    class Resp:
        def raise_for_status(self):
            pass

        @property
        def text(self):
            return FEED_XML
    return Resp()


def test_fetch_success(monkeypatch):
    monkeypatch.setattr("trending.time.time", lambda: 1000.0)
    monkeypatch.setattr("trending.requests.get", _fake_get_ok)
    terms = trending.get_trending_terms(max_terms=15)
    assert terms == ["freddy peralta", "fever", "robert & stock"]
    assert trending._CACHE["terms"] == terms


def test_fetch_failure_returns_empty(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")
    monkeypatch.setattr("trending.time.time", lambda: 1000.0)
    monkeypatch.setattr("trending.requests.get", boom)
    assert trending.get_trending_terms() == []


def test_cache_served_within_ttl(monkeypatch):
    trending._CACHE["terms"] = ["Cached"]
    trending._CACHE["fetched_at"] = 500.0
    monkeypatch.setattr("trending.time.time", lambda: 501.0)
    assert trending.get_trending_terms() == ["Cached"]


def test_cache_expired_stale_fallback(monkeypatch):
    trending._CACHE["terms"] = ["Stale"]
    trending._CACHE["fetched_at"] = 0.0

    def boom(*args, **kwargs):
        raise RuntimeError("network down")
    monkeypatch.setattr("trending.time.time", lambda: 1000.0)
    monkeypatch.setattr("trending.requests.get", boom)
    assert trending.get_trending_terms() == ["Stale"]


def test_max_terms_limits_results(monkeypatch):
    monkeypatch.setattr("trending.time.time", lambda: 2000.0)
    monkeypatch.setattr("trending.requests.get", _fake_get_ok)
    assert len(trending.get_trending_terms(max_terms=2)) == 2
