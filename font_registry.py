"""
FontRegistry — single source of truth for the curated font library.

Loads `fonts.json`, resolves font file paths with a graceful fallback chain,
builds condensed font descriptions for the LLM system prompt, and validates
overlay style dicts before they reach the renderer.

Standalone module: must not import llama_manager or text_overlay (avoids
circular imports). Resolution never raises on missing fonts — it always
falls back.
"""

import json
import os

from config import BASE_DIR

# Where the downloaded OFL font files live.
FONT_DIR = os.path.expanduser("~/.local/share/fonts")

# Sensible fallback quote/color defaults shared with the validator.
DEFAULT_QUOTE = "Let the quiet reveal what noise conceals."
DEFAULT_TEXT_EFFECT = "shadow"
DEFAULT_GLOW_COLOR = "#FFD700"

_ALLOWED = {
    "alignment": {"center", "left", "right"},
    "position": {
        "top", "top-left", "top-right", "bottom", "bottom-left", "bottom-right",
        "center", "middle-left", "middle-right",
    },
    "font_size": {"small", "medium", "large", "xlarge"},
    "background_overlay": {"none", "dark-bottom", "light-top", "dark-center", "text-block", "text-block-dark", "text-block-light"},
    "text_effect": {"none", "shadow", "outline", "glow"},
    "font_weight": {"regular", "bold"},
}

_FIELD_DEFAULTS = {
    "alignment": "center",
    "position": "bottom",
    "font_size": "medium",
    "background_overlay": "none",
    "text_effect": DEFAULT_TEXT_EFFECT,
    "font_weight": "regular",
}


def _is_hex_color(value: str) -> bool:
    return isinstance(value, str) and len(value) in {4, 7} and value.startswith("#") and all(
        char in "0123456789abcdefABCDEF" for char in value[1:]
    )


def _clamp_float(value, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _clamp_int(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


class FontRegistry:
    """Loads the font manifest and exposes lookup/fallback/prompt/validation."""

    def __init__(self, manifest_path: str = "fonts.json"):
        if not os.path.isabs(manifest_path):
            manifest_path = os.path.join(str(BASE_DIR), manifest_path)
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(
                f"Font manifest not found at {manifest_path}. Expected fonts.json in the project root."
            )
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Font manifest {manifest_path} is not valid JSON: {exc}") from exc

        self._data = data
        self._order = [cat for cat in data if isinstance(data[cat], dict) and "fonts" in data[cat]]
        self._by_name: dict[str, tuple[str, dict]] = {}
        for cat in self._order:
            fonts = data[cat]["fonts"]
            if not isinstance(fonts, list) or not fonts:
                raise ValueError(f"Font category '{cat}' must contain a non-empty fonts list")
            for font in fonts:
                if not isinstance(font, dict) or not font.get("name") or not font.get("file"):
                    raise ValueError(f"Font entry in '{cat}' must have 'name' and 'file'")
                self._by_name[font["name"]] = (cat, font)

    def categories(self) -> list[str]:
        """Return the category names in manifest order."""
        return list(self._order)

    def fonts_in(self, category: str) -> list[dict]:
        """Return the font dicts for a category (empty list if unknown)."""
        cat = self._data.get(category)
        return list(cat["fonts"]) if isinstance(cat, dict) else []

    def _font_path(self, file_name: str) -> str:
        return os.path.join(FONT_DIR, file_name)

    def resolve(self, style: str, font_name: str, weight: str = "regular") -> str | None:
        """
        Resolve a font name to an absolute file path.

        Fallback chain (never raises):
          unknown style       -> serif
          unknown name        -> first font in the style
          name in wrong style -> first font in the requested style
          bold missing file   -> regular file
          regular missing     -> walk the style list for the first existing file
          nothing exists      -> None
        """
        if style not in self._order:
            if style != "serif":
                print(f"[font_registry] unknown style '{style}', falling back to 'serif'")
            style = "serif"

        font = self._lookup(style, font_name)
        if font is None:
            font = self._data[style]["fonts"][0]
            print(f"[font_registry] unknown font '{font_name}' for '{style}', using '{font['name']}'")

        if weight == "bold" and font.get("bold_file"):
            bold_path = self._font_path(font["bold_file"])
            if os.path.exists(bold_path):
                return bold_path
            print(f"[font_registry] bold file missing for '{font['name']}', using regular")

        regular_path = self._font_path(font.get("file", ""))
        if regular_path and os.path.exists(regular_path):
            return regular_path

        # The manifest references a missing file — walk the style for the first file that exists.
        for candidate in self._data[style]["fonts"]:
            path = self._font_path(candidate.get("file", ""))
            if path and os.path.exists(path):
                print(
                    f"[font_registry] '{font.get('name')}' file missing; "
                    f"falling back to '{candidate['name']}'"
                )
                return path

        print(f"[font_registry] no existing font file for style '{style}'")
        return None

    def _lookup(self, style: str, font_name: str) -> dict | None:
        """Return the font dict if `font_name` exists in `style`, else None."""
        if not font_name:
            return None
        entry = self._by_name.get(font_name)
        if entry is not None and entry[0] == style:
            return entry[1]
        return None

    def first_font_name(self, style: str) -> str:
        """Canonical name of the first font in a style (falls back to serif)."""
        if style not in self._order:
            style = "serif"
        return self._data[style]["fonts"][0]["name"]

    def build_prompt_text(self, max_chars: int = 2000) -> str:
        """
        Condensed font guide for the LLM system prompt.

        Always includes every category header (so the model knows the full
        palette) and as many per-font lines as fit within `max_chars`.
        """
        lines: list[str] = []
        used = 0
        short_headers = [f"[{cat.upper()}]" for cat in self._order]
        for idx, cat in enumerate(self._order):
            cat_data = self._data[cat]
            # Reserve space for the remaining short category headers so every
            # category name survives truncation.
            reserve = sum(len(h) + 1 for h in short_headers[idx:])
            header = f"[{cat.upper()}] {cat_data['description']}"
            if used + len(header) + reserve > max_chars:
                # Budget tight — keep the category name visible without its
                # description rather than dropping the category entirely.
                if used + len(short_headers[idx]) + reserve <= max_chars:
                    lines.append(short_headers[idx])
                    used += len(short_headers[idx]) + 1
                continue
            lines.append(header)
            used += len(header) + 1
            for font in cat_data["fonts"]:
                line = f"- {font['name']}: {font['description']} Use: {font['use_cases']}"
                if used + len(line) + 1 + reserve > max_chars:
                    break
                lines.append(line)
                used += len(line) + 1
        return "\n".join(lines)

    def validate(self, style_dict: dict) -> dict:
        """
        Return a corrected overlay dict for rendering.

        Fills v2 defaults, clamps numeric fields, validates hex colors, and
        corrects the font name to a real font in the requested category.
        """
        d = dict(style_dict or {})

        raw_quote = d.get("quote")
        quote = " ".join(str(raw_quote).split()).strip('"“”') if raw_quote is not None else ""
        if not quote:
            quote = DEFAULT_QUOTE
        author = " ".join(str(d.get("author", "")).split()).strip('"“”')
        if author.lower() in {"unknown", "n/a", "none", "original"}:
            author = ""

        font_style = d.get("font_style", "serif")
        if font_style not in self._order:
            font_style = "serif"

        font = d.get("font", "")
        if not isinstance(font, str) or self._lookup(font_style, font) is None:
            if font:
                print(f"[font_registry] unknown font '{font}' for '{font_style}', using '{self.first_font_name(font_style)}'")
            font = self.first_font_name(font_style)

        font_weight = d.get("font_weight", "regular")
        if font_weight not in _ALLOWED["font_weight"]:
            font_weight = "regular"

        text_effect = d.get("text_effect", DEFAULT_TEXT_EFFECT)
        if text_effect not in _ALLOWED["text_effect"]:
            text_effect = DEFAULT_TEXT_EFFECT

        text_color = d.get("text_color", "#FFFFFF")
        if not _is_hex_color(text_color):
            text_color = "#FFFFFF"

        glow_color = d.get("glow_color", DEFAULT_GLOW_COLOR)
        if not _is_hex_color(glow_color):
            glow_color = DEFAULT_GLOW_COLOR

        gradient_from = d.get("text_gradient_from", "")
        gradient_to = d.get("text_gradient_to", "")
        if not _is_hex_color(gradient_from):
            gradient_from = ""
        if not _is_hex_color(gradient_to):
            gradient_to = ""

        result = {
            "quote": quote,
            "author": author,
            "font_style": font_style,
            "font": font,
            "font_weight": font_weight,
            "alignment": d.get("alignment", "center"),
            "text_color": text_color,
            "position": d.get("position", "bottom"),
            "x_offset": _clamp_float(d.get("x_offset", 0.0), -100.0, 100.0, 0.0),
            "y_offset": _clamp_float(d.get("y_offset", 0.0), -100.0, 100.0, 0.0),
            "font_size": d.get("font_size", "medium"),
            "background_overlay": d.get("background_overlay", "none"),
            "text_effect": text_effect,
            "glow_color": glow_color,
            "text_gradient_from": gradient_from,
            "text_gradient_to": gradient_to,
            "letter_spacing": _clamp_int(d.get("letter_spacing", 0), 0, 10, 0),
        }
        for key, values in _ALLOWED.items():
            if result[key] not in values:
                result[key] = _FIELD_DEFAULTS[key]
        return result
