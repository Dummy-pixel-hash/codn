"""Unit tests for the FontRegistry loader/resolver/prompt-builder/validator."""

import os

import pytest

from font_registry import FontRegistry, FONT_DIR

EXPECTED_CATEGORIES = ["serif", "sans-serif", "monospace", "handwritten", "display"]


@pytest.fixture(scope="module")
def registry():
    return FontRegistry()


def test_categories_returns_five(registry):
    assert registry.categories() == EXPECTED_CATEGORIES
    assert len(registry.categories()) == 5


def test_resolve_known_font(registry):
    path = registry.resolve("serif", "Cormorant Garamond", "regular")
    assert path is not None
    assert os.path.exists(path)


def test_resolve_unknown_name_falls_back(registry):
    path = registry.resolve("serif", "Totally Fake Font", "regular")
    assert path is not None
    assert os.path.exists(path)


def test_resolve_unknown_style_falls_back(registry):
    path = registry.resolve("bogus-style", "Anything", "regular")
    assert path is not None
    assert os.path.exists(path)


def test_resolve_bold_missing_falls_back(registry):
    # Cormorant Garamond has no bold_file -> regular file is returned.
    path = registry.resolve("serif", "Cormorant Garamond", "bold")
    assert path is not None
    assert os.path.exists(path)


def test_resolve_bold_with_bold_file(registry):
    # Crimson Text has a real bold file -> bold path returned.
    path = registry.resolve("serif", "Crimson Text", "bold")
    assert path is not None
    assert path.endswith("CrimsonText-Bold.ttf")


def test_resolve_missing_file_falls_back(registry, tmp_path, monkeypatch):
    # Point FONT_DIR at an empty dir so every manifest file is "missing":
    # resolve must not raise and must return None after exhausting fallbacks.
    monkeypatch.setattr("font_registry.FONT_DIR", str(tmp_path))
    registry._font_path = lambda name: os.path.join(str(tmp_path), name)
    path = registry.resolve("serif", "Cormorant Garamond", "regular")
    assert path is None


def test_build_prompt_text_length(registry):
    text = registry.build_prompt_text(max_chars=2000)
    assert len(text) <= 2000
    font_names = [f["name"].split()[0] for c in registry.categories() for f in registry.fonts_in(c)]
    present = sum(1 for name in font_names if name in text)
    assert present >= 3


def test_build_prompt_text_categories(registry):
    text = registry.build_prompt_text(max_chars=2000)
    for cat in registry.categories():
        assert f"[{cat.upper()}]" in text.upper()
