"""Unit tests for the text_overlay renderer (no network)."""

import pytest
from PIL import Image

import text_overlay


def _luminance(path: str) -> list[list[int]]:
    """Return per-pixel luminance grid of the saved JPEG."""
    img = Image.open(path).convert("L")
    return [[img.getpixel((x, y)) for x in range(img.width)] for y in range(img.height)]


def _text_bbox(path: str) -> tuple[int, int, int, int]:
    """Bounding box of bright (>150 luminance) pixels; text is white on dark art."""
    grid = _luminance(path)
    xs = [x for y, row in enumerate(grid) for x, v in enumerate(row) if v > 150]
    ys = [y for y, row in enumerate(grid) for x, v in enumerate(row) if v > 150]
    assert xs and ys, "no bright text pixels found"
    return min(xs), min(ys), max(xs), max(ys)


def test_add_text_overlay_output_exists(base_image, tmp_path):
    out = str(tmp_path / "out.jpg")
    result = text_overlay.add_text_overlay(
        base_image, out,
        {"quote": "Hello world.", "font_size": "large", "text_effect": "none"},
    )
    assert result == out
    img = Image.open(out)
    assert img.format == "JPEG"
    assert img.size == (400, 400)


def test_dark_bottom_overlay_changes_pixels(base_image, tmp_path):
    plain = str(tmp_path / "plain.jpg")
    over = str(tmp_path / "over.jpg")
    style = {"quote": "Overlay test.", "font_size": "large", "text_effect": "none"}
    text_overlay.add_text_overlay(base_image, plain, dict(style, background_overlay="none"))
    text_overlay.add_text_overlay(base_image, over, dict(style, background_overlay="dark-bottom"))
    assert _luminance(plain) != _luminance(over)


def test_positions_move_text_vertically(base_image, tmp_path):
    top = str(tmp_path / "top.jpg")
    bottom = str(tmp_path / "bottom.jpg")
    style = {"quote": "Position test.", "font_size": "large", "text_effect": "none"}
    text_overlay.add_text_overlay(base_image, top, dict(style, position="top-left"))
    text_overlay.add_text_overlay(base_image, bottom, dict(style, position="bottom-left"))
    top_y = _text_bbox(top)[1]
    bottom_y = _text_bbox(bottom)[1]
    assert bottom_y > top_y + 50


def test_gradient_differs_from_solid(base_image, tmp_path):
    solid = str(tmp_path / "solid.jpg")
    grad = str(tmp_path / "grad.jpg")
    style = {"quote": "Gradient test.", "font_size": "large", "text_effect": "none"}
    text_overlay.add_text_overlay(base_image, solid, dict(style, text_color="#FFFFFF"))
    text_overlay.add_text_overlay(
        base_image, grad,
        dict(style, text_gradient_from="#FFD700", text_gradient_to="#FF4500"),
    )
    assert _luminance(solid) != _luminance(grad)


def test_glow_differs_from_plain(base_image, tmp_path):
    plain = str(tmp_path / "plain.jpg")
    glow = str(tmp_path / "glow.jpg")
    style = {"quote": "Glow test.", "font_size": "large"}
    text_overlay.add_text_overlay(base_image, plain, dict(style, text_effect="none"))
    text_overlay.add_text_overlay(base_image, glow, dict(style, text_effect="glow", glow_color="#FF00FF"))
    assert _luminance(plain) != _luminance(glow)


def test_letter_spacing_widens_text(base_image, tmp_path):
    tight = str(tmp_path / "tight.jpg")
    spaced = str(tmp_path / "spaced.jpg")
    style = {"quote": "Spacing test.", "font_size": "large", "text_effect": "none"}
    text_overlay.add_text_overlay(base_image, tight, dict(style, letter_spacing=0))
    text_overlay.add_text_overlay(base_image, spaced, dict(style, letter_spacing=6))
    tight_w = _text_bbox(tight)[2] - _text_bbox(tight)[0]
    spaced_w = _text_bbox(spaced)[2] - _text_bbox(spaced)[0]
    assert spaced_w > tight_w


def test_bold_selects_bold_file():
    # Crimson Text ships a real bold file; bold weight must resolve to it.
    path = text_overlay._find_font("serif", "Crimson Text", "bold")
    assert path is not None
    assert path.endswith("CrimsonText-Bold.ttf")


def test_bold_render_differs_from_regular(base_image, tmp_path):
    reg = str(tmp_path / "reg.jpg")
    bold = str(tmp_path / "bold.jpg")
    style = {"quote": "Bold test.", "font_style": "serif", "font": "Crimson Text", "font_size": "large", "text_effect": "none"}
    text_overlay.add_text_overlay(base_image, reg, dict(style, font_weight="regular"))
    text_overlay.add_text_overlay(base_image, bold, dict(style, font_weight="bold"))
    assert _luminance(reg) != _luminance(bold)


def test_font_fallback_no_crash(base_image, tmp_path, monkeypatch):
    # If no font can be resolved at all, the renderer must save a plain copy
    # instead of raising.
    monkeypatch.setattr(text_overlay, "_find_font", lambda *a, **k: None)
    out = str(tmp_path / "fallback.jpg")
    result = text_overlay.add_text_overlay(
        base_image, out,
        {"quote": "Fallback.", "font_style": "serif", "font": "Missing", "text_effect": "none"},
    )
    assert result == out
    assert Image.open(out).size == (400, 400)


def test_offsets_move_text(base_image, tmp_path):
    base = str(tmp_path / "off0.jpg")
    shifted = str(tmp_path / "off1.jpg")
    style = {"quote": "Offset test.", "position": "bottom-left", "font_size": "large", "text_effect": "none"}
    text_overlay.add_text_overlay(base_image, base, dict(style, x_offset=0, y_offset=0))
    text_overlay.add_text_overlay(base_image, shifted, dict(style, x_offset=10, y_offset=-10))
    base_x, base_y = _text_bbox(base)[0], _text_bbox(base)[1]
    shift_x, shift_y = _text_bbox(shifted)[0], _text_bbox(shifted)[1]
    assert (shift_x, shift_y) != (base_x, base_y)
