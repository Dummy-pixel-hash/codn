"""
Add styled text overlays to generated art images using Pillow.
Supports a curated font library (via FontRegistry), 9 anchor positions with
fine-grained percentage offsets, and effects: none, shadow, outline, glow,
gradient fill, bold weight, and letter-spacing.
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

from font_registry import FontRegistry

_REGISTRY: FontRegistry | None = None


def _get_registry() -> FontRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = FontRegistry()
    return _REGISTRY


# Last-resort fallbacks if no manifest font file exists on disk.
_SYSTEM_FALLBACKS = [
    "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/open-sans/OpenSans-Regular.ttf",
]


def _find_font(style: str, font_name: str = "", weight: str = "regular") -> str | None:
    """Resolve a font path via FontRegistry, falling back to any system font."""
    path = _get_registry().resolve(style, font_name, weight)
    if path:
        return path
    for f in _SYSTEM_FALLBACKS:
        if os.path.exists(f):
            return f
    return None


def _size_to_pixels(size_rel: str, img_height: int) -> int:
    """Convert relative font size to pixel value."""
    sizes = {
        # Keep the quote subordinate to the artwork. The old values made a
        # normal 15-word quote fill most of a square image.
        "small": int(img_height * 0.032),
        "medium": int(img_height * 0.043),
        "large": int(img_height * 0.055),
        "xlarge": int(img_height * 0.065),
    }
    return sizes.get(size_rel, sizes["medium"])


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
    letter_spacing: int = 0,
) -> list[str]:
    """Word-wrap text to fit within max_width, accounting for letter-spacing."""
    lines = []
    current = ""
    for word in text.split():
        test_line = f"{current} {word}".strip() if current else word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        width = (bbox[2] - bbox[0]) + max(0, len(test_line) - 1) * letter_spacing
        if width <= max_width:
            current = test_line
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def add_text_overlay(image_path: str, output_path: str, style: dict) -> str:
    """
    Add styled text overlay to an image.

    Args:
        image_path: Path to the source art image
        output_path: Where to save the result
        style: Dict with keys:
            quote, author, font_style, font, font_weight, alignment,
            text_color, position (9 anchors: top/bottom/center plus
            -left/-right variants and middle-left/middle-right),
            x_offset, y_offset (signed % of image dims, clamped),
            font_size, background_overlay, text_effect (none/shadow/
            outline/glow), glow_color, text_gradient_from/to,
            letter_spacing (px, 0-10)

    Returns:
        Path to the output image
    """
    img = Image.open(image_path).convert("RGBA")
    w, h = img.size

    quote = " ".join(str(style.get("quote", "")).split()).strip('"“”')
    if not quote:
        quote = "Let the quiet reveal what noise conceals."
    author = " ".join(str(style.get("author", "")).split()).strip('"“”')
    if author.lower() in {"unknown", "n/a", "none", "original"}:
        author = ""
    font_style = style.get("font_style", "sans-serif")
    font_name = style.get("font", "")
    font_weight = style.get("font_weight", "regular")
    alignment = style.get("alignment", "center")
    text_color = style.get("text_color", "#FFFFFF")
    position = style.get("position", "bottom")
    x_offset = style.get("x_offset", 0.0)
    y_offset = style.get("y_offset", 0.0)
    font_size_rel = style.get("font_size", "medium")
    overlay = style.get("background_overlay", "none")
    text_effect = style.get("text_effect", "none")
    glow_color = style.get("glow_color", "#FFD700")
    gradient_from = style.get("text_gradient_from", "")
    gradient_to = style.get("text_gradient_to", "")
    letter_spacing = style.get("letter_spacing", 0)

    if overlay != "none":
        img = _draw_bg_overlay(img, overlay)

    draw = ImageDraw.Draw(img)
    font_path = _find_font(font_style, font_name, font_weight)
    if not font_path:
        img.convert("RGB").save(output_path, "JPEG", quality=95)
        return output_path

    font_size = _size_to_pixels(font_size_rel, h)
    font = ImageFont.truetype(font_path, font_size)
    margin_x = int(w * 0.11)
    margin_y = int(h * 0.09)
    max_width = int(w * 0.78)
    line_height = font_size + int(font_size * 0.22)
    color_rgba = _hex_to_rgba(text_color)

    # Fit both width and height. This protects the composition when the model
    # ignores the requested word count or returns a long fallback response.
    quote_lines = _wrap_text(draw, quote, font, max_width, letter_spacing)
    while font_size > 24 and (
        len(quote_lines) > 3 or len(quote_lines) * line_height > int(h * 0.26)
    ):
        font_size -= 2
        font = ImageFont.truetype(font_path, font_size)
        line_height = font_size + int(font_size * 0.22)
        quote_lines = _wrap_text(draw, quote, font, max_width, letter_spacing)

    author_font = None
    author_line = ""
    author_height = 0
    if author:
        author_font = ImageFont.truetype(font_path, max(int(font_size * 0.7), 12))
        author_line = f"— {author}"
        bbox = draw.textbbox((0, 0), author_line, font=author_font)
        author_height = bbox[3] - bbox[1]

    block_height = len(quote_lines) * line_height + (author_height + 10 if author_line else 0)

    top_anchors = {"top", "top-left", "top-right"}
    bottom_anchors = {"bottom", "bottom-left", "bottom-right"}
    left_anchors = {"middle-left", "top-left", "bottom-left"}
    right_anchors = {"middle-right", "top-right", "bottom-right"}

    if position in top_anchors:
        y = margin_y
    elif position in bottom_anchors:
        y = max(margin_y, h - margin_y - block_height)
    else:
        y = max(margin_y, (h - block_height) // 2)
    try:
        y += int(float(y_offset) / 100.0 * h)
    except (TypeError, ValueError):
        pass
    y = max(margin_y, min(y, max(margin_y, h - margin_y - block_height)))

    for line in quote_lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        if position in left_anchors:
            x = margin_x
        elif position in right_anchors:
            x = w - margin_x - text_w
        elif alignment == "right":
            x = w - margin_x - text_w
        elif alignment == "left":
            x = margin_x
        else:
            x = (w - text_w) // 2
        try:
            x += int(float(x_offset) / 100.0 * w)
        except (TypeError, ValueError):
            pass
        x = max(margin_x, min(x, max(margin_x, w - margin_x - text_w)))
        _draw_text(
            draw, img, (x, y), line, font, color_rgba, text_effect,
            glow_color, gradient_from, gradient_to, letter_spacing,
        )
        y += line_height

    if author_line:
        bbox = draw.textbbox((0, 0), author_line, font=author_font)
        text_w = bbox[2] - bbox[0]
        if position in left_anchors:
            ax = margin_x
        elif position in right_anchors:
            ax = w - margin_x - text_w
        elif alignment == "right":
            ax = w - margin_x - text_w
        elif alignment == "left":
            ax = margin_x
        else:
            ax = (w - text_w) // 2
        try:
            ax += int(float(x_offset) / 100.0 * w)
        except (TypeError, ValueError):
            pass
        ax = max(margin_x, min(ax, max(margin_x, w - margin_x - text_w)))
        _draw_text(
            draw, img, (ax, y + 10), author_line, author_font, color_rgba,
            text_effect, glow_color, gradient_from, gradient_to, 0,
        )

    img.convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path


def _draw_text(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    xy: tuple[int, int],
    text: str,
    font,
    fill: tuple[int, int, int, int],
    effect: str,
    glow_color: str,
    gradient_from: str,
    gradient_to: str,
    letter_spacing: int = 0,
) -> None:
    """Dispatch text rendering: spaced, gradient, or plain effect."""
    if letter_spacing > 0 and len(text) > 1:
        x, y = xy
        for char in text:
            char_w = draw.textlength(char, font=font)
            _draw_text(
                draw, img, (x, y), char, font, fill, effect,
                glow_color, gradient_from, gradient_to, 0,
            )
            x += int(char_w + letter_spacing)
    elif gradient_from and gradient_to:
        _draw_gradient_text(draw, img, xy, text, font, gradient_from, gradient_to, effect)
    else:
        _draw_text_with_effect(draw, img, xy, text, font, fill, effect, glow_color)


def _draw_text_with_effect(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    xy: tuple[int, int],
    text: str,
    font,
    fill: tuple[int, int, int, int],
    effect: str,
    glow_color: str = "#FFD700",
) -> None:
    if effect == "shadow":
        draw.text((xy[0] + 3, xy[1] + 3), text, font=font, fill=(0, 0, 0, 180))
        draw.text(xy, text, font=font, fill=fill)
    elif effect == "outline":
        draw.text(
            xy,
            text,
            font=font,
            fill=fill,
            stroke_width=max(2, font.size // 14),
            stroke_fill=(0, 0, 0, 255),
        )
    elif effect == "glow":
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_rgba = _hex_to_rgba(glow_color)
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2), (0, -3), (-3, 0), (3, 0), (0, 3)):
            glow_draw.text((xy[0] + dx, xy[1] + dy), text, font=font, fill=glow_rgba)
        glow = glow.filter(ImageFilter.GaussianBlur(3))
        img.alpha_composite(glow)
        draw.text(xy, text, font=font, fill=fill)
    else:
        draw.text(xy, text, font=font, fill=fill)


def _draw_gradient_text(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    xy: tuple[int, int],
    text: str,
    font,
    from_hex: str,
    to_hex: str,
    effect: str,
) -> None:
    """Render text filled with a vertical gradient via an alpha mask."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    if tw <= 0 or th <= 0:
        return

    mask = Image.new("L", (tw, th), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.text((-bbox[0], -bbox[1]), text, font=font, fill=255)

    top = _hex_to_rgba(from_hex)
    bottom = _hex_to_rgba(to_hex)
    grad = Image.new("RGBA", (tw, th))
    grad_draw = ImageDraw.Draw(grad)
    for y in range(th):
        t = y / max(1, th - 1)
        grad_draw.line(
            [(0, y), (tw - 1, y)],
            fill=(
                int(top[0] + (bottom[0] - top[0]) * t),
                int(top[1] + (bottom[1] - top[1]) * t),
                int(top[2] + (bottom[2] - top[2]) * t),
                255,
            ),
        )
    grad.putalpha(mask)

    if effect == "shadow":
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.text((xy[0] + 3, xy[1] + 3), text, font=font, fill=(0, 0, 0, 180))
        img.alpha_composite(shadow)
    elif effect == "outline":
        outline = Image.new("RGBA", img.size, (0, 0, 0, 0))
        outline_draw = ImageDraw.Draw(outline)
        outline_draw.text(
            xy,
            text,
            font=font,
            fill=(0, 0, 0, 255),
            stroke_width=max(2, font.size // 14),
            stroke_fill=(0, 0, 0, 255),
        )
        img.alpha_composite(outline)

    img.alpha_composite(grad, (xy[0] + bbox[0], xy[1] + bbox[1]))


def _draw_bg_overlay(img: Image.Image, style: str) -> Image.Image:
    """Return the image with a gradient overlay composited behind text."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_grad = ImageDraw.Draw(grad)

    if "dark-bottom" in style:
        for i in range(h):
            # A light scrim preserves the artwork instead of turning half of
            # the image into a flat black panel.
            start = int(h * 0.45)
            alpha = int(105 * max(0, (i - start) / (h - start))) if i >= start else 0
            draw_grad.line([(0, i), (w - 1, i)], fill=(0, 0, 0, alpha))
    elif "light-top" in style:
        for i in range(h):
            alpha = int(80 * max(0, (h * 0.55 - i) / (h * 0.55)))
            draw_grad.line([(0, i), (w - 1, i)], fill=(255, 255, 255, alpha))
    elif "dark-center" in style:
        center_y = h // 2
        for i in range(h):
            dist = abs(i - center_y) / (h // 2)
            draw_grad.line([(0, i), (w - 1, i)], fill=(0, 0, 0, int(90 * dist)))

    return Image.alpha_composite(img, grad)


def _hex_to_rgba(hex_color: str) -> tuple[int, int, int, int]:
    """Convert hex color to RGBA tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(char * 2 for char in hex_color)
    if len(hex_color) != 6 or any(char not in "0123456789abcdefABCDEF" for char in hex_color):
        hex_color = "FFFFFF"
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b, 255)
