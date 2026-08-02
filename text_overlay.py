"""
Add styled text overlays to generated art images using Pillow.
Supports various fonts, alignments, colors, positions, and background overlays.
"""

from PIL import Image, ImageDraw, ImageFont
import os


# Available system fonts (each style maps to visually distinct families)
_USER_FONTS = os.path.expanduser("~/.local/share/fonts")
_SYSTEM_FONTS = {
    "serif": [
        "/usr/share/fonts/google-noto/NotoSerif-Regular.ttf",
        f"{_USER_FONTS}/PlayfairDisplay-Variable.ttf",
        "/usr/share/fonts/liberation-serif/LiberationSerif-Regular.ttf",
    ],
    "sans-serif": [
        "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/open-sans/OpenSans-Regular.ttf",
        "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    ],
    "monospace": [
        "/usr/share/fonts/google-noto/NotoSansMono-Regular.ttf",
        "/usr/share/fonts/google-noto/NotoMono-Regular.ttf",
    ],
    "handwritten": [
        f"{_USER_FONTS}/Caveat-Variable.ttf",
        f"{_USER_FONTS}/DancingScript-Variable.ttf",
        "/usr/share/fonts/aajohan-comfortaa-fonts/Comfortaa-Regular.otf",
        "/usr/share/fonts/abattis-cantarell-fonts/Cantarell-Regular.otf",
    ],
    "display": [
        f"{_USER_FONTS}/Oswald-Variable.ttf",
        f"{_USER_FONTS}/Pacifico-Regular.ttf",
    ],
}


def _find_font(style: str) -> str | None:
    """Find a system font matching the requested style."""
    fonts = _SYSTEM_FONTS.get(style, [])
    for f in fonts:
        if os.path.exists(f):
            return f
    # Fallback to any available font
    fallbacks = [
        "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/open-sans/OpenSans-Regular.ttf",
    ]
    for f in fallbacks:
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


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width."""
    lines = []
    current = ""
    for word in text.split():
        test_line = f"{current} {word}".strip() if current else word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
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
        style: Dict with keys: quote, author, font_style, alignment,
               text_color, position, font_size, background_overlay

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
    alignment = style.get("alignment", "center")
    text_color = style.get("text_color", "#FFFFFF")
    position = style.get("position", "bottom")
    font_size_rel = style.get("font_size", "medium")
    overlay = style.get("background_overlay", "none")
    text_effect = style.get("text_effect", "none")

    if overlay != "none":
        img = _draw_bg_overlay(img, overlay)

    draw = ImageDraw.Draw(img)
    font_path = _find_font(font_style)
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
    quote_lines = _wrap_text(draw, quote, font, max_width)
    while font_size > 24 and (
        len(quote_lines) > 3 or len(quote_lines) * line_height > int(h * 0.26)
    ):
        font_size -= 2
        font = ImageFont.truetype(font_path, font_size)
        line_height = font_size + int(font_size * 0.22)
        quote_lines = _wrap_text(draw, quote, font, max_width)

    author_font = None
    author_line = ""
    author_height = 0
    if author:
        author_font = ImageFont.truetype(font_path, max(int(font_size * 0.7), 12))
        author_line = f"— {author}"
        bbox = draw.textbbox((0, 0), author_line, font=author_font)
        author_height = bbox[3] - bbox[1]

    block_height = len(quote_lines) * line_height + (author_height + 10 if author_line else 0)

    if position == "top":
        y = margin_y
    elif position == "center":
        y = max(margin_y, (h - block_height) // 2)
    elif position == "middle-left" or position == "middle-right":
        y = max(margin_y, (h - block_height) // 2)
    else:
        y = max(margin_y, h - margin_y - block_height)

    for line in quote_lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        if position == "middle-left":
            x = margin_x
        elif position == "middle-right":
            x = w - margin_x - text_w
        elif alignment == "right":
            x = w - margin_x - text_w
        elif alignment == "left":
            x = margin_x
        else:
            x = (w - text_w) // 2
        _draw_text_with_effect(draw, (x, y), line, font, color_rgba, text_effect)
        y += line_height

    if author_line:
        bbox = draw.textbbox((0, 0), author_line, font=author_font)
        text_w = bbox[2] - bbox[0]
        if position == "middle-left":
            ax = margin_x
        elif position == "middle-right":
            ax = w - margin_x - text_w
        elif alignment == "right":
            ax = w - margin_x - text_w
        elif alignment == "left":
            ax = margin_x
        else:
            ax = (w - text_w) // 2
        _draw_text_with_effect(draw, (ax, y + 10), author_line, author_font, color_rgba, text_effect)

    img.convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path


def _draw_text_with_effect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font,
    fill: tuple[int, int, int, int],
    effect: str,
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
    else:
        draw.text(xy, text, font=font, fill=fill)


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
