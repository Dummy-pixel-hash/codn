"""Unit tests for free web quote search (no network — requests mocked)."""

import quote_search


def _reset():
    quote_search._CACHE.update(key="", results=[], fetched_at=0.0)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


WIKI_SEARCH = {"query": {"search": [{"title": "Marcus Aurelius"}, {"title": "Help:Contents"}]}}
WIKI_PARSE = {
    "parse": {
        "wikitext": {
            "*": (
                "==Quotes==\n"
                "* \"The impediment to action advances action.\" <ref>x</ref>\n"
                "* Short\n"
                "* \"If it is not right do not do it; if it is not true do not say it.\" — ''Meditations''\n"
                "** sub-bullet context, skipped\n"
                "* [[Stoicism|Related philosophy]] link line that is long enough here\n"
            )
        }
    }
}


def test_wikiquote_parses_bullets(monkeypatch):
    _reset()

    def fake_get(url, params=None, timeout=None, **kwargs):
        if params and params.get("list") == "search":
            return _Resp(WIKI_SEARCH)
        return _Resp(WIKI_PARSE)

    monkeypatch.setattr(quote_search.requests, "get", fake_get)
    out = quote_search.search_quotes("stoicism", limit=5, ttl_seconds=0)
    assert out
    assert all({"quote", "author", "source"} <= set(o) for o in out)
    assert out[0]["author"] == "Marcus Aurelius"
    assert out[0]["source"] == "Wikiquote"
    assert "impediment to action" in out[0]["quote"]
    assert "<ref>" not in out[0]["quote"]
    # Skips sub-bullets and too-short lines.
    assert all(len(o["quote"]) >= 20 for o in out)


def test_falls_back_to_dummyjson(monkeypatch):
    _reset()
    calls = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        calls.append(url)
        if "wikiquote" in url:
            return _Resp({"query": {"search": []}})
        return _Resp({"quotes": [
            {"quote": "Ocean calm quote here.", "author": "Anon"},
            {"quote": "Unrelated xyz.", "author": "Nobody"},
        ]})

    monkeypatch.setattr(quote_search.requests, "get", fake_get)
    out = quote_search.search_quotes("ocean calm", limit=3, ttl_seconds=0)
    assert out and out[0]["quote"] == "Ocean calm quote here."
    assert out[0]["source"] == "DummyJSON"


def test_failure_returns_empty_and_never_raises(monkeypatch):
    _reset()

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(quote_search.requests, "get", boom)
    assert quote_search.search_quotes("anything", ttl_seconds=0) == []


def test_empty_vibe_short_circuits(monkeypatch):
    _reset()
    called = []
    monkeypatch.setattr(
        quote_search.requests, "get", lambda *a, **k: called.append(1)
    )
    assert quote_search.search_quotes("   ") == []
    assert called == []


def test_cache_served(monkeypatch):
    _reset()
    quote_search._CACHE.update(key="stoicism", results=[{"quote": "C."}], fetched_at=10**12)
    monkeypatch.setattr(
        quote_search.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch"))
    )
    assert quote_search.search_quotes("stoicism") == [{"quote": "C."}]
