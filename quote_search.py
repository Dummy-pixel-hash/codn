"""
Free web quote search: find real, attributed quotes matching the vibe.

Providers (priority order, first non-empty wins):
  1. Wikiquote — true topic search: API title search, then page wikitext
     parsing for quote bullets. No key, huge corpus.
  2. DummyJSON — one bulk fetch (TTL-cached), ranked client-side by vibe
     word overlap. No key.
  3. ZenQuotes — random pull, kept only on keyword overlap. No key.

Results are normalised to {"quote", "author", "source"}. Like trending.py:
TTL-cached, short timeouts, never raises — any failure returns [] (or the
stale cache) so generation always degrades gracefully to local/model quotes.
"""

import re
import time

import requests

_TTL_SECONDS = 6 * 3600  # 6h — quote corpora barely change; keep load low
_CACHE: dict = {"fetched_at": 0.0, "key": "", "results": []}

_WIKI_API = "https://en.wikiquote.org/w/api.php"
_DUMMY_URL = "https://dummyjson.com/quotes?limit=1000"
_ZEN_URL = "https://zenquotes.io/api/random"
# Wikimedia blocks default python-requests UA (403); identify politely.
_WIKI_HEADERS = {"User-Agent": "codn-quote-search/1.0 (personal Instagram generator)"}

_STOPWORDS = frozenset(
    "a an the and or of to in on for with about quotes quote say says "
    "like just really very motivation motivational life live".split()
)

_TAG_RE = re.compile(r"<[^>]+>")
_WIKILINK_RE = re.compile(r"\[\[([^|\]]*\|)?([^\]]+)\]\]")
_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")


def _vibe_words(vibe: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", vibe.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _clean_wikitext_line(line: str) -> str:
    """Strip Wikiquote bullet markup down to plain quote text."""
    text = line.lstrip("*#:; ").strip()
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = _TEMPLATE_RE.sub("", text)
    text = _WIKILINK_RE.sub(r"\2", text)
    text = _TAG_RE.sub("", text)
    text = text.replace("'''", "").replace("''", "")  # bold/italic markup
    text = text.strip(" '\"“”")
    return " ".join(text.split())


def _wikiquote_search(topic: str, limit: int) -> list[dict]:
    r = requests.get(
        _WIKI_API,
        params={"action": "query", "list": "search", "srsearch": topic,
                "srlimit": 5, "format": "json"},
        headers=_WIKI_HEADERS,
        timeout=8,
    )
    r.raise_for_status()
    titles = [
        hit["title"]
        for hit in r.json().get("query", {}).get("search", [])
        if hit.get("title") and ":" not in hit["title"]  # skip Category:/Help:
    ][:2]
    out: list[dict] = []
    for title in titles:
        try:
            pr = requests.get(
                _WIKI_API,
                params={"action": "parse", "page": title, "prop": "wikitext",
                        "format": "json"},
                headers=_WIKI_HEADERS,
                timeout=8,
            )
            pr.raise_for_status()
            wikitext = pr.json().get("parse", {}).get("wikitext", {}).get("*", "")
        except (requests.RequestException, ValueError, KeyError):
            continue
        for line in wikitext.splitlines():
            s = line.strip()
            if not s.startswith("*") or s.startswith("**"):
                continue
            quote = _clean_wikitext_line(s)
            if 20 <= len(quote) <= 300 and quote not in {q["quote"] for q in out}:
                out.append({"quote": quote, "author": title, "source": "Wikiquote"})
                if len(out) >= limit:
                    return out
    return out


def _dummyjson_search(vibe_words: set[str], limit: int) -> list[dict]:
    r = requests.get(_DUMMY_URL, timeout=8)
    r.raise_for_status()
    quotes = r.json().get("quotes", [])

    def score(q: str) -> int:
        return len(vibe_words & set(re.findall(r"[a-z0-9]+", q.lower())))

    ranked = sorted(
        (q for q in quotes if isinstance(q, dict) and q.get("quote") and q.get("author")),
        key=lambda q: score(q.get("quote", "")),
        reverse=True,
    )
    out = [
        {"quote": q["quote"].strip(), "author": q["author"].strip(), "source": "DummyJSON"}
        for q in ranked[:limit]
        if score(q.get("quote", "")) > 0 or not vibe_words
    ]
    return out


def _zenquotes_fallback(vibe_words: set[str], limit: int) -> list[dict]:
    out: list[dict] = []
    for _ in range(3):
        try:
            r = requests.get(_ZEN_URL, timeout=8)
            r.raise_for_status()
            items = r.json()
        except (requests.RequestException, ValueError):
            break
        for item in items if isinstance(items, list) else []:
            q, a = (item.get("q") or "").strip(), (item.get("a") or "").strip()
            if not q or not a or a.lower() == "unknown":
                continue
            if vibe_words and not (
                vibe_words & set(re.findall(r"[a-z0-9]+", q.lower()))
            ):
                continue
            if q not in {x["quote"] for x in out}:
                out.append({"quote": q, "author": a, "source": "ZenQuotes"})
                if len(out) >= limit:
                    return out
    return out


def search_quotes(vibe: str, limit: int = 3,
                  ttl_seconds: int = _TTL_SECONDS) -> list[dict]:
    """Return web quotes matching the vibe (cached, never raises)."""
    vibe = (vibe or "").strip()
    if not vibe:
        return []
    now = time.time()
    key = vibe.lower()
    if _CACHE["results"] and _CACHE["key"] == key and (now - _CACHE["fetched_at"]) < ttl_seconds:
        return _CACHE["results"]
    try:
        results = _wikiquote_search(vibe, limit)
        if not results:
            results = _dummyjson_search(_vibe_words(vibe), limit)
        if not results:
            results = _zenquotes_fallback(_vibe_words(vibe), limit)
        results = results[:limit]
        _CACHE.update(key=key, results=results, fetched_at=now)
        return results
    except Exception as e:
        print(f"[quote_search] failed, using stale cache or empty: {e}")
        return _CACHE["results"] if _CACHE["key"] == key else []
