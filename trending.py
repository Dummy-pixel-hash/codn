"""
Fetch real-time trending search terms from Google's official trending RSS
feed (https://trends.google.com/trending/rss) to seed the caption generator
with currently-trending hashtag ideas.

The feed is a plain, key-less GET; results are cached with a TTL because
trends refresh slowly and to avoid hammering Google. Any failure returns the
stale cache or an empty list — never an exception — so caption generation
always degrades gracefully.
"""

import html
import re
import time

import requests

_CACHE: dict = {"fetched_at": 0.0, "terms": []}
_TTL_SECONDS = 6 * 3600  # 6h — trends don't change hourly; keep load low
_FEED_URL = "https://trends.google.com/trending/rss?geo=US"
_ITEM_TITLE_RE = re.compile(r"<item>.*?<title>(.*?)</title>", re.S)


def get_trending_terms(max_terms: int = 15, ttl_seconds: int = _TTL_SECONDS) -> list[str]:
    """Return cached or freshly-fetched Google Trends trending searches."""
    now = time.time()
    if _CACHE["terms"] and (now - _CACHE["fetched_at"]) < ttl_seconds:
        return _CACHE["terms"]

    try:
        r = requests.get(_FEED_URL, timeout=15)
        r.raise_for_status()
        raw_titles = _ITEM_TITLE_RE.findall(r.text)
        terms = [html.unescape(t.strip()) for t in raw_titles if t.strip()]
        _CACHE["terms"] = terms[:max_terms]
        _CACHE["fetched_at"] = now
        return _CACHE["terms"]
    except Exception as e:
        print(f"[trending] fetch failed, using stale cache or empty: {e}")
        return _CACHE["terms"]
