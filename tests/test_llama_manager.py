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
    assert "render text" in sys_msg


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
