"""Unit tests for llama_manager overlay parsing/normalisation (no network)."""

import llama_manager


def test_normalise_empty_dict_defaults():
    d = llama_manager._normalise_overlay({})
    assert d["font_style"] == "serif"
    assert d["font"]  # real font name
    assert d["font_weight"] == "regular"
    assert d["position"] == "bottom"
    assert d["x_offset"] == 0.0
    assert d["y_offset"] == 0.0
    assert d["letter_spacing"] == 0
    assert d["text_effect"] == "shadow"
    assert d["glow_color"] == "#FFD700"
    assert d["text_gradient_from"] == ""
    assert d["text_gradient_to"] == ""


def test_normalise_keeps_valid_v2():
    v2 = {
        "quote": "Test.",
        "font_style": "serif",
        "font": "Cormorant Garamond",
        "font_weight": "bold",
        "position": "bottom-left",
        "x_offset": 5,
        "y_offset": -3,
        "letter_spacing": 6,
        "text_effect": "glow",
        "glow_color": "#FF00FF",
        "text_gradient_from": "#FFD700",
        "text_gradient_to": "#FF4500",
    }
    d = llama_manager._normalise_overlay(v2)
    assert d["font"] == "Cormorant Garamond"
    assert d["font_weight"] == "bold"
    assert d["position"] == "bottom-left"
    assert d["x_offset"] == 5.0
    assert d["y_offset"] == -3.0
    assert d["letter_spacing"] == 6
    assert d["text_effect"] == "glow"
    assert d["glow_color"] == "#FF00FF"
    assert d["text_gradient_from"] == "#FFD700"
    assert d["text_gradient_to"] == "#FF4500"


def test_normalise_clamps_offsets():
    d = llama_manager._normalise_overlay({"x_offset": 500, "y_offset": -500, "letter_spacing": 99})
    assert d["x_offset"] == 100.0
    assert d["y_offset"] == -100.0
    assert d["letter_spacing"] == 10


def test_normalise_invalid_effect():
    d = llama_manager._normalise_overlay({"text_effect": "laser"})
    assert d["text_effect"] == "shadow"


def test_normalise_invalid_position():
    d = llama_manager._normalise_overlay({"position": "somewhere-weird"})
    assert d["position"] == "bottom"


def test_normalise_unknown_font():
    d = llama_manager._normalise_overlay({"font": "Bogus Font", "font_style": "serif"})
    assert d["font"]  # corrected to a real font
    assert d["font"] != "Bogus Font"


def test_normalise_hex_color_validation():
    d = llama_manager._normalise_overlay({
        "text_color": "notacolor",
        "glow_color": "zzz",
        "text_gradient_from": "#12345",
        "text_gradient_to": "red",
    })
    assert d["text_color"] == "#FFFFFF"
    assert d["glow_color"] == "#FFD700"
    assert d["text_gradient_from"] == ""
    assert d["text_gradient_to"] == ""


def test_prompt_contains_new_fields():
    p = llama_manager.build_overlay_system_prompt()
    for field in ("font_weight", "glow_color", "x_offset", "letter_spacing", "text_gradient_from"):
        assert field in p


def test_parse_overlay_response_valid_json():
    raw = 'Some prefix {"quote": "A fine quote.", "font": "Inter", "font_style": "sans-serif", "text_effect": "outline"} suffix'
    d = llama_manager._parse_overlay_response(raw)
    assert d["quote"] == "A fine quote."
    assert d["font"] == "Inter"
    assert d["text_effect"] == "outline"


def test_art_system_forbids_text_in_artwork():
    """The art-prompt system message must never ask the image model to draw text."""
    sys_msg = llama_manager._ART_SYSTEM
    for forbidden in ("letters", "typography", "banners", "watermarks"):
        assert forbidden in sys_msg
    assert "text-free" in sys_msg


def test_art_system_forbids_direct_gaze():
    sys_msg = llama_manager._ART_SYSTEM
    assert "NOT look directly at the viewer" in sys_msg
    assert "three-quarter view" in sys_msg
    assert "seen from behind" in sys_msg


def test_art_system_requires_style_commitment():
    sys_msg = llama_manager._ART_SYSTEM
    assert "commit to it fully" in sys_msg
    assert "signature visual language" in sys_msg
    assert "anime" in sys_msg


def test_art_system_tasteful_neon():
    sys_msg = llama_manager._ART_SYSTEM
    assert "tasteful and restrained" in sys_msg
    assert "Never a flat wall of saturated neon" in sys_msg


def test_build_art_prompt_six_part_structure():
    p = llama_manager._build_art_prompt("anime quote art")
    for part in ("subject → art style & medium", "composition & camera", "mood & color palette"):
        assert part in p
    assert "must NOT look directly at the viewer" in p
    assert "commit to it with 2-3 signature descriptors" in p


def test_overlay_prompt_credits_source():
    p = llama_manager.build_overlay_system_prompt()
    assert "set \"author\" to that source" in p
    assert "Spirited Away" in p
    assert "empty string ONLY when the quote is genuinely original" in p


def test_overlay_prompt_style_matches_art():
    p = llama_manager.build_overlay_system_prompt()
    assert "Match the visual mood of the image" in p
    assert "never default to plain white on colorful art" in p


def test_parse_caption_response_valid():
    raw = 'Here you go {"caption": "A thoughtful caption.", "hashtags": ["#quotes", "#Mindset_Matters", "quotes"]}'
    d = llama_manager._parse_caption_response(raw)
    assert d["caption"] == "A thoughtful caption."
    assert d["hashtags"] == ["#quotes", "#mindset_matters"]


def test_parse_caption_response_sanitises():
    raw = '{"caption": "Cap.", "hashtags": ["#Bad Tag!", "#ok_2", "#ok_2", 42]}'
    d = llama_manager._parse_caption_response(raw)
    assert d["hashtags"] == ["#bad_tag", "#ok_2"]


def test_parse_caption_response_fallback():
    d = llama_manager._parse_caption_response("just some plain text")
    assert d["caption"] == "just some plain text"
    assert d["hashtags"] == []


def test_caption_system_demands_trending_and_related():
    sys_msg = llama_manager._CAPTION_SYSTEM
    assert "TRENDING" in sys_msg
    assert "RELATED" in sys_msg
    assert "hashtags" in sys_msg


def test_format_caption_with_data():
    out = llama_manager.format_caption(
        "Quote.", "Author",
        {"caption": "Model caption.", "hashtags": ["#a", "#b"]},
    )
    assert out == "Model caption.\n\n— Author\n\n#a #b"


def test_format_caption_fallback_to_quote():
    out = llama_manager.format_caption("Quote.", "Author", None)
    assert out == "Quote.\n\n— Author"


def test_format_caption_no_author():
    out = llama_manager.format_caption("Quote.", "", {"caption": "Cap.", "hashtags": ["#a"]})
    assert out == "Cap.\n\n#a"


def test_generate_caption_includes_trending_terms(monkeypatch):
    """Trending terms must appear in the user message sent to the model."""
    captured = {}

    def fake_post(url, body):
        captured["body"] = body
        return {"caption": "Nice.", "hashtags": ["#a"]}

    monkeypatch.setattr(llama_manager, "_post_chat", fake_post)
    llama_manager.generate_caption(
        None,
        quote="Q.",
        author="A.",
        category="philosophy",
        trending_terms=["self improvement", "stoicism"],
    )
    user_msg = captured["body"]["messages"][1]["content"]
    assert "Currently trending searches: self improvement, stoicism" in user_msg


def test_generate_caption_no_trending_no_line(monkeypatch):
    captured = {}

    def fake_post(url, body):
        captured["body"] = body
        return {"caption": "Nice.", "hashtags": []}

    monkeypatch.setattr(llama_manager, "_post_chat", fake_post)
    llama_manager.generate_caption(None, quote="Q.", category="philosophy")
    user_msg = captured["body"]["messages"][1]["content"]
    assert "trending" not in user_msg


def test_caption_system_mentions_trending_usage():
    assert "Currently trending searches" in llama_manager._CAPTION_SYSTEM


def test_retrieve_quotes_exact_source_match():
    quotes = llama_manager.retrieve_quotes(category="anime", theme="naruto")
    assert 1 <= len(quotes) <= 3
    assert all(q["source"] == "Naruto" for q in quotes)


def test_retrieve_quotes_character_match():
    quotes = llama_manager.retrieve_quotes(category="anime", theme="itachi")
    assert quotes
    assert all("Itachi" in q.get("character", "") for q in quotes)


def test_retrieve_quotes_movie_match():
    quotes = llama_manager.retrieve_quotes(category="movie", theme="the godfather")
    assert 1 <= len(quotes) <= 3
    assert all("Godfather" in q["source"] for q in quotes)


def test_retrieve_quotes_containment_match():
    quotes = llama_manager.retrieve_quotes(category="anime", theme="attack on titan")
    assert quotes
    assert all("Attack on Titan" in q["source"] for q in quotes)


def test_retrieve_quotes_no_match_returns_empty():
    quotes = llama_manager.retrieve_quotes(category="philosophy", theme="stoicism")
    assert quotes == []


def test_retrieve_quotes_bare_category_returns_pool():
    quotes = llama_manager.retrieve_quotes(category="anime", theme="")
    assert 1 <= len(quotes) <= 3
    assert all(q.get("source") for q in quotes)


def test_retrieve_quotes_none_values_safe():
    quotes = llama_manager.retrieve_quotes(category=None, theme=None)
    assert quotes == []


def test_retrieve_quotes_skips_used(monkeypatch):
    naruto = llama_manager.retrieve_quotes(category="anime", theme="naruto")
    assert naruto
    used_key = llama_manager.database.quote_key(naruto[0]["quote"])
    monkeypatch.setattr(
        llama_manager.database, "get_used_quote_keys", lambda *a, **k: {used_key}
    )
    quotes = llama_manager.retrieve_quotes(category="anime", theme="naruto")
    assert quotes
    assert all(llama_manager.database.quote_key(q["quote"]) != used_key for q in quotes)


def test_retrieve_quotes_exhausted_source_returns_empty(monkeypatch):
    naruto = llama_manager.retrieve_quotes(category="anime", theme="naruto")
    assert naruto
    used_keys = {llama_manager.database.quote_key(q["quote"]) for q in naruto}
    monkeypatch.setattr(
        llama_manager.database, "get_used_quote_keys", lambda *a, **k: used_keys
    )
    quotes = llama_manager.retrieve_quotes(category="anime", theme="naruto")
    assert quotes == []


def test_mark_quote_used_records_picked(monkeypatch):
    saved = []
    monkeypatch.setattr(llama_manager.database, "save_quote", lambda q: saved.append(q))
    recalled = llama_manager.retrieve_quotes(category="anime", theme="naruto")
    llama_manager._mark_quote_used(recalled[0]["quote"], recalled)
    assert saved == [recalled[0]["quote"]]


def test_mark_quote_used_ignores_unrelated(monkeypatch):
    saved = []
    monkeypatch.setattr(llama_manager.database, "save_quote", lambda q: saved.append(q))
    recalled = llama_manager.retrieve_quotes(category="anime", theme="naruto")
    llama_manager._mark_quote_used("A totally original line.", recalled)
    assert saved == []


def test_overlay_prompt_verbatim_when_quotes_supplied():
    prompt = llama_manager.build_overlay_system_prompt(
        [{"quote": "Test.", "source": "Naruto", "character": "Itachi Uchiha"}]
    )
    assert "VERBATIM" in prompt
    assert "Real quotes from the requested source" in prompt
    assert "Write one original quote" not in prompt


def test_overlay_prompt_default_still_original():
    prompt = llama_manager.build_overlay_system_prompt()
    assert "Write one original quote" in prompt


class _FakeOverlayResponse:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": '{"quote": "Q.", "author": "Naruto"}'}}]}


def test_generate_text_overlay_includes_real_quotes(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return _FakeOverlayResponse()

    monkeypatch.setattr(llama_manager.requests, "post", fake_post)
    llama_manager.generate_text_overlay(
        None, "/tmp/img.png", art_prompt="", category="anime", theme="naruto"
    )
    user_msg = captured["body"]["messages"][1]["content"]
    system_msg = captured["body"]["messages"][0]["content"]
    assert "Real quotes from the requested source" in user_msg
    assert "— Naruto" in user_msg
    assert "VERBATIM" in system_msg


def test_generate_text_overlay_no_quotes_for_unknown(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return _FakeOverlayResponse()

    monkeypatch.setattr(llama_manager.requests, "post", fake_post)
    llama_manager.generate_text_overlay(
        None, "/tmp/img.png", art_prompt="", category="philosophy", theme="stoicism"
    )
    user_msg = captured["body"]["messages"][1]["content"]
    assert "Real quotes from the requested source" not in user_msg
