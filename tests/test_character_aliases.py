"""Unit tests for character-alias quote lookup (no network)."""

import json
import re

import llama_manager


def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _pool_sources():
    data = llama_manager._load_quotes()
    return {_norm(e["source"]) for e in data["anime"]} | {
        _norm(e["source"]) for e in data["movie"]
    }


def test_every_alias_resolves():
    """No dead aliases: each must match >=1 pool entry (map-rot guard)."""
    aliases = llama_manager._load_aliases()
    assert len(aliases) >= 50
    sources = _pool_sources()
    dead = [
        alias
        for alias, spec in aliases.items()
        if not any(_norm(s) in sources for s in spec["sources"])
    ]
    assert dead == []


def test_tony_stark_resolves_iron_man_family():
    quotes = llama_manager.retrieve_quotes(category="", theme="tony stark")
    assert 1 <= len(quotes) <= 3
    assert all(
        "iron man" in _norm(q["source"]) or "avengers" in _norm(q["source"])
        for q in quotes
    )
    assert all(q.get("character") == "Tony Stark" for q in quotes)


def test_bruce_wayne_resolves_batman_family():
    quotes = llama_manager.retrieve_quotes(category="", theme="Bruce Wayne!")
    assert quotes
    assert all("batman" in _norm(q["source"]) for q in quotes)
    assert all(q.get("character") == "Bruce Wayne" for q in quotes)


def test_alias_attribution_format():
    quotes = llama_manager.retrieve_quotes(category="", theme="tyler durden")
    assert quotes
    assert all(q["source"] == "Fight Club" for q in quotes)
    assert all(q.get("character") == "Tyler Durden" for q in quotes)


def test_unknown_character_falls_through():
    # Not an alias and not a source: existing stages decide (may be []).
    quotes = llama_manager.retrieve_quotes(category="", theme="itachi")
    assert quotes  # pre-existing character-name stage still works
    assert all("Itachi" in q.get("character", "") for q in quotes)


def test_exhausted_alias_returns_empty_no_leak(monkeypatch):
    tony = llama_manager.retrieve_quotes(category="", theme="tony stark")
    assert tony
    # Mark the WHOLE alias family used: must return [], not other sources.
    data = llama_manager._load_quotes()
    pool = data["anime"] + data["movie"]
    wanted = {
        _norm(s)
        for s in ["Iron Man", "The Avengers", "Avengers - Age of Ultron",
                  "Avengers : Infinity War", "Avengers: Endgame",
                  "Avengers: Infinity War", "Marvel's The Avengers"]
    }
    family = [e for e in pool if _norm(e.get("source", "")) in wanted]
    used_keys = {llama_manager.database.quote_key(e["quote"]) for e in family}
    monkeypatch.setattr(
        llama_manager.database, "get_used_quote_keys", lambda *a, **k: used_keys
    )
    assert llama_manager.retrieve_quotes(category="", theme="tony stark") == []


def test_alias_in_category_position():
    quotes = llama_manager.retrieve_quotes(category="gandalf", theme="")
    assert quotes
    assert all("lord of the rings" in _norm(q["source"]) for q in quotes)

def test_overlay_user_message_uses_alias_character(monkeypatch):
    """End-to-end shape: recalled alias quotes reach the model prompt."""
    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"quote": "Q.", "author": "Joker — Joker"}'}}]}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return FakeResp()

    monkeypatch.setattr(llama_manager.requests, "post", fake_post)
    llama_manager.generate_text_overlay(None, "/tmp/img.png", category="", theme="joker")
    user_msg = captured["body"]["messages"][1]["content"]
    assert "Joker" in user_msg


def test_alias_matches_inside_longer_vibe():
    """Regression: 'tony stark genius' must hit Tony Stark, not [] (which
    made the model invent attributions like 'Us')."""
    quotes = llama_manager.retrieve_quotes(category="", theme="tony stark genius")
    assert quotes
    assert all(q.get("character") == "Tony Stark" for q in quotes)
    quotes = llama_manager.retrieve_quotes(
        category="", theme="stoic tony stark monday motivation"
    )
    assert quotes
    assert all(q.get("character") == "Tony Stark" for q in quotes)


def test_tiny_sources_cannot_hijack_queries():
    """Regression: 'genius' must not match source 'Us'; 'suit up' must not
    match 'Up'. Tiny sources (<4 chars) are exact-match only."""
    assert llama_manager.retrieve_quotes(category="", theme="genius mindset") == []
    assert llama_manager.retrieve_quotes(category="", theme="suit up monday") == []


def test_exact_tiny_source_still_works():
    quotes = llama_manager.retrieve_quotes(category="", theme="us")
    assert quotes
    assert all(q["source"] == "Us" for q in quotes)


def test_alias_word_boundaries_hold():
    """'author mindset' must not match 'thor'; 'oscar-worthy' must not match
    'scar' — but 'oscar worthy batman' must still reach Batman."""
    assert llama_manager.retrieve_quotes(category="", theme="author mindset") == []
    quotes = llama_manager.retrieve_quotes(category="", theme="oscar worthy batman")
    assert quotes
    assert all("batman" in _norm(q["source"]) for q in quotes)


def test_subject_descriptors_known_character():
    descs = llama_manager.subject_descriptors("Tony Stark")
    assert descs
    assert any("arc-reactor" in d for d in descs)
    assert all("face" not in d.lower() and "portrait" not in d.lower() for d in descs)


def test_subject_descriptors_unknown_returns_none():
    assert llama_manager.subject_descriptors("Nobody McNobody") is None
    assert llama_manager.subject_descriptors("") is None


def test_depicts_map_stays_signifier_only():
    """Guard: depicts must never sneak likeness language in."""
    aliases = llama_manager._load_aliases()
    with_depicts = {k: v for k, v in aliases.items() if v.get("depicts")}
    assert len(with_depicts) >= 20
    banned = ("face", "portrait", "photorealistic", "likeness", "selfie")
    for alias, spec in with_depicts.items():
        assert isinstance(spec["depicts"], list) and 1 <= len(spec["depicts"]) <= 5, alias
        for item in spec["depicts"]:
            assert isinstance(item, str) and item.strip(), alias
            assert not any(b in item.lower() for b in banned), (alias, item)
