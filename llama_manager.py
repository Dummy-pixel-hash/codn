"""
Manage the llama.cpp server lifecycle: start, generate, kill.
Bonsai-27B-Q1_0.gguf is used for prompt generation and text overlay work.
"""

import json
import os
import random
import re
import signal
import subprocess
import time
import requests
import database
from config import LLAMA_MODEL_PATH, LLAMA_PORT, BASE_DIR
from font_registry import FontRegistry

# Font descriptions are injected into the system prompt; cache the registry
# (it only reads the small fonts.json manifest, never loads font files).
_REGISTRY: FontRegistry | None = None

# Curated real-quote pools (anime/movie). The local model can't reliably
# recall attributed quotes, so known-source requests inject real ones instead.
_QUOTES_CACHE: dict | None = None


def _get_registry() -> FontRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = FontRegistry()
    return _REGISTRY


def _load_quotes() -> dict:
    global _QUOTES_CACHE
    if _QUOTES_CACHE is None:
        path = os.path.join(str(BASE_DIR), "data", "quotes.json")
        with open(path, encoding="utf-8") as fh:
            _QUOTES_CACHE = json.load(fh)
    return _QUOTES_CACHE


def _normalise_query(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# Character-name aliases (character -> {character display name, pool sources}).
# Lets "tony stark" resolve to Iron Man quotes even though the pool has no
# character field for movies. Keys are matched after _normalise_query.
_ALIASES_CACHE: dict | None = None


def _load_aliases() -> dict:
    global _ALIASES_CACHE
    if _ALIASES_CACHE is None:
        path = os.path.join(str(BASE_DIR), "data", "character_aliases.json")
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = {}
        _ALIASES_CACHE = {
            _normalise_query(k): v
            for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict)
            and v.get("character") and isinstance(v.get("sources"), list)
        }
    return _ALIASES_CACHE


def _lookup_alias(query: str, pools: list[tuple[str, list[dict]]],
                  unused) -> list[dict] | None:
    """Match a character alias (e.g. 'tony stark') to its pool sources.

    Matches whole-word subsets, so 'tony stark genius' still hits while
    'author mindset' never matches 'thor' and 'oscar-worthy' never matches
    'scar'. Longest alias (most words, then longest string) wins.
    Returns None on miss (caller falls through to other stages). On a hit,
    returns unused entries — possibly [] when exhausted, which must NOT fall
    through to another character's quotes, so attribution stays exact.
    Entries lacking a character field get the alias's display name attached
    so downstream renders 'Tony Stark — Iron Man'.
    """
    aliases = _load_aliases()
    words = set(query.split())
    best_key: str | None = None
    for alias in aliases:
        alias_words = set(alias.split())
        if alias_words and alias_words <= words:
            if best_key is None or (len(alias_words), len(alias)) > (
                len(set(best_key.split())), len(best_key),
            ):
                best_key = alias
    if best_key is None:
        return None
    spec = aliases[best_key]
    wanted = {_normalise_query(s) for s in spec["sources"]}
    hits: list[dict] = []
    for _, pool in pools:
        for entry in pool:
            if _normalise_query(entry.get("source", "")) in wanted:
                if not entry.get("character"):
                    entry = dict(entry, character=spec["character"])
                hits.append(entry)
    return unused(hits)


def retrieve_quotes(category: str = "", theme: str = "", limit: int = 3) -> list[dict]:
    """
    Return real quotes when category/theme references a known anime/movie source.

    Matching priority: character alias, then exact source name, then
    character name, then containment (longest matching source wins), then
    bare-category ("anime"/"movie") which samples that pool. Quotes already
    used are skipped. Returns [] when nothing is implied, so the model
    writes an original quote as before.
    """
    data = _load_quotes()
    category = category or ""
    theme = theme or ""
    query = _normalise_query(f"{category} {theme}")
    if not query:
        return []

    used = database.get_used_quote_keys()

    def unused(entries: list[dict]) -> list[dict]:
        return [e for e in entries if database.quote_key(e["quote"]) not in used]

    pools = [("anime", data["anime"]), ("movie", data["movie"])]

    # 0) Character alias, e.g. theme="tony stark" -> Iron Man quotes with
    #    character "Tony Stark" attached. A claimed query never falls through
    #    (exhausted alias returns []), so attribution stays exact.
    for candidate in (_normalise_query(theme), query):
        if not candidate:
            continue
        aliased = _lookup_alias(candidate, pools, unused)
        if aliased is not None:
            return aliased[:limit]

    # 1) Exact source-name match, e.g. theme="naruto" -> source "Naruto".
    #    An exhausted source returns [] rather than falling through to a
    #    different source's quotes, so attribution stays correct. Sibling
    #    source names for the same series are still served by stage 3.
    for _, pool in pools:
        by_source: dict[str, list[dict]] = {}
        for entry in pool:
            by_source.setdefault(_normalise_query(entry["source"]), []).append(entry)
        if query in by_source:
            return unused(by_source[query])[:limit]

    # 2) Character-name match against the theme (category alone rarely names a
    #    character), e.g. theme="itachi" -> Itachi Uchiha's quotes.
    theme_query = _normalise_query(theme)
    if theme_query:
        for _, pool in pools:
            hits = [e for e in pool if theme_query in _normalise_query(e.get("character", ""))]
            if hits:
                return unused(hits)[:limit]

    # 3) Containment: query inside a source name (or vice versa); longest source
    #    wins so a request like "attack on titan" matches that series precisely.
    #    Tiny sources ("us", "up", "saw", "elf", "300", "gie") only match
    #    exactly (stage 1) — as substrings they hijack unrelated queries
    #    ("genius" -> "Us", "suit up" -> "Up").
    best: list[tuple[int, list[dict]]] = []
    for _, pool in pools:
        by_source: dict[str, list[dict]] = {}
        for entry in pool:
            by_source.setdefault(_normalise_query(entry["source"]), []).append(entry)
        for src, entries in by_source.items():
            if not src or len(src) < 4:
                continue
            if query in src or src in query:
                fresh = unused(entries)
                if fresh:
                    best.append((len(src), fresh))
    if best:
        best.sort(key=lambda item: item[0], reverse=True)
        return best[0][1][:limit]

    # 4) Bare category: sample the whole pool.
    generic = {
        "anime": {"anime", "animes", "manga", "japanese animation"},
        "movie": {"movie", "movies", "film", "films", "cinema"},
    }
    for pool_key, aliases in generic.items():
        if _normalise_query(category) in aliases:
            fresh = unused(data[pool_key])
            if fresh:
                return random.sample(fresh, min(limit, len(fresh)))

    return []


def start_llama() -> subprocess.Popen | None:
    """Start llama-server in the background.

    Returns the Popen handle when this call started the server, or None when
    the server was already running (no handle owned) or startup failed.
    Callers must only pass a Popen handle to stop_llama().
    """
    if is_running():
        return None

    cmd = [
        "llama-server",
        "-m", str(LLAMA_MODEL_PATH),
        "--host", "0.0.0.0",
        "--port", str(LLAMA_PORT),
        "-ngl", "999",
        "-c", "4096",
        "-np", "1",
        "-b", "4096",
        "-ub", "1024",
        "-fa", "on",
        "--cache-type-k", "q4_0",
        "--cache-type-v", "q4_0",
        "-t", "6",
        "-tb", "12",
        "--reasoning", "off",
        "--temp", "0.5",
        "--top-p", "0.92",
        "--top-k", "30",
        "--min-p", "0.02",
        "--repeat-penalty", "1.05",
        "--cache-reuse", "256",
        "--jinja",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(BASE_DIR),
        start_new_session=True,
    )
    # Wait for server to be ready (up to 30s)
    for _ in range(60):
        try:
            r = requests.get(f"http://localhost:{LLAMA_PORT}/health", timeout=1)
            if r.status_code == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.5)

    proc.kill()
    return None


def stop_llama(proc, wait_for_vram: bool = True):
    """Kill the llama-server process and optionally wait for VRAM to free up."""
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.kill()
        except Exception:
            pass
        # Wait for process to fully die
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    if wait_for_vram:
        _wait_for_vram_free()


def is_running():
    """Check if llama-server is already running."""
    try:
        r = requests.get(f"http://localhost:{LLAMA_PORT}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def generate_art_prompt(llama_proc, system_prompt: str = None, max_tokens: int = 512) -> str:
    """
    Ask the text model to generate a creative, professional-looking art prompt.
    Returns the raw prompt string to send to ComfyUI.
    """
    prompt = _build_art_prompt(system_prompt)

    url = f"http://localhost:{LLAMA_PORT}/v1/chat/completions"
    body = {
        "model": "Bonsai-27B",
        "messages": [
            # Keep the stable art-director rules in the system message. The
            # request-specific category/theme belongs in the user message;
            # replacing the system prompt was weakening the generation brief.
            {"role": "system", "content": _ART_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": min(max_tokens, 320),
        "temperature": 0.72,
        "stream": False,
    }

    for attempt in range(3):
        try:
            r = requests.post(url, json=body, timeout=60)
            if r.status_code == 200:
                data = r.json()
                text = data["choices"][0]["message"]["content"].strip()
                # Clean up — take only the first paragraph/block
                return text.split("\n\n")[0].strip().strip('`').strip('"')
        except Exception as e:
            print(f"[llama] generate_art_prompt attempt {attempt+1} failed: {e}")
            time.sleep(2)
    raise RuntimeError("Failed to generate art prompt after 3 attempts")


def subject_descriptors(name: str) -> list[str] | None:
    """Iconic visual signifiers for a character, if the alias map has them.

    Used to steer art toward the quote's subject (armor, cowl, staff —
    never faces). Returns None when unknown; callers fall back to a
    generic evocative line.
    """
    if not name:
        return None
    aliases = _load_aliases()
    key = _normalise_query(name)
    spec = aliases.get(key)
    if spec and spec.get("depicts"):
        return list(spec["depicts"])
    for candidate in aliases.values():
        if _normalise_query(candidate.get("character", "")) == key and candidate.get(
            "depicts"
        ):
            return list(candidate["depicts"])
    return None


def pick_quote(category: str = "", theme: str = "", limit: int = 3) -> list[dict]:
    """Pick real quotes for the vibe BEFORE art is generated (quote-first).

    Local curated pool first, then free web search. Returns [] when nothing
    matches, in which case the overlay model writes an original line.
    Never raises — web failures degrade to local-only, then empty.
    """
    local = retrieve_quotes(category, theme, limit)
    if local:
        return local
    try:
        import quote_search

        web = quote_search.search_quotes(f"{category or ''} {theme or ''}".strip(), limit)
    except Exception as e:
        print(f"[llama] web quote search failed: {e}")
        return []
    used = database.get_used_quote_keys()
    return [e for e in web if database.quote_key(e["quote"]) not in used][:limit]


def generate_text_overlay(
    llama_proc,
    art_image_path: str,
    art_prompt: str = "",
    category: str = "",
    theme: str = "",
    max_tokens: int = 512,
    recalled: list[dict] | None = None,
) -> dict:
    """
    Ask the text model what text + styling to overlay on the generated image.
    Returns a dict with text, font, alignment, color, position, etc.

    The returned dict follows the v2 overlay contract:
    quote, author, font_style, font, font_weight, alignment, text_color,
    position (9 anchors), x_offset/y_offset (clamped %), font_size,
    background_overlay, text_effect (none/shadow/outline/glow), glow_color,
    text_gradient_from/to, letter_spacing.
    """
    url = f"http://localhost:{LLAMA_PORT}/v1/chat/completions"
    context = [f"Art direction: {art_prompt}" if art_prompt else ""]
    if category:
        context.append(f"Content category: {category}")
    if theme:
        context.append(f"User theme: {theme}")

    recalled = retrieve_quotes(category, theme) if recalled is None else recalled
    if recalled:
        lines = ["Real quotes from the requested source (pick one verbatim):"]
        for i, quote_entry in enumerate(recalled, 1):
            attribution = quote_entry["source"]
            if quote_entry.get("character"):
                attribution = f"{quote_entry['character']} — {attribution}"
            lines.append(f'{i}. "{quote_entry["quote"]}" — {attribution}')
        context.append("\n".join(lines))

    body = {
        "model": "Bonsai-27B",
        "messages": [
            {"role": "system", "content": build_overlay_system_prompt(recalled or None)},
            {
                "role": "user",
                "content": "\n".join(context)
                + "\n\nCreate the quote and layout specification for this image.",
            },
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }

    for attempt in range(3):
        try:
            r = requests.post(url, json=body, timeout=60)
            if r.status_code == 200:
                data = r.json()
                text = data["choices"][0]["message"]["content"].strip()
                parsed = _parse_overlay_response(text)
                if recalled:
                    _mark_quote_used(parsed.get("quote", ""), recalled)
                return parsed
        except Exception as e:
            print(f"[llama] generate_text_overlay attempt {attempt+1} failed: {e}")
            time.sleep(2)
    raise RuntimeError("Failed to generate text overlay after 3 attempts")


def _mark_quote_used(picked_quote: str, recalled: list[dict]) -> None:
    """Record the recalled quote the model picked so it isn't offered again."""
    picked_key = database.quote_key(picked_quote)
    for entry in recalled:
        if database.quote_key(entry["quote"]) == picked_key:
            database.save_quote(entry["quote"])
            return


def generate_caption(
    llama_proc,
    quote: str = "",
    author: str = "",
    art_prompt: str = "",
    category: str = "",
    theme: str = "",
    max_tokens: int = 400,
    trending_terms: list[str] | None = None,
) -> dict:
    """
    Ask the text model to write the Instagram caption for the post.

    Returns a dict: {"caption": str, "hashtags": [str, ...]}. Hashtags are a
    mix of trending (high-volume) and related (niche) tags, sanitised by
    _parse_caption_response. When trending_terms is provided, the model is
    nudged to include hashtags that align with currently-trending searches.
    Call this while llama-server is still running — it is killed right after
    to free VRAM.
    """
    url = f"http://localhost:{LLAMA_PORT}/v1/chat/completions"
    context = [f"Art direction: {art_prompt}" if art_prompt else ""]
    if quote:
        context.append(f"Quote on the image: {quote}")
    if author:
        context.append(f"Quote author: {author}")
    if category:
        context.append(f"Content category: {category}")
    if theme:
        context.append(f"User theme: {theme}")
    if trending_terms:
        context.append(f"Currently trending searches: {', '.join(trending_terms)}")

    body = {
        "model": "Bonsai-27B",
        "messages": [
            {"role": "system", "content": _CAPTION_SYSTEM},
            {
                "role": "user",
                "content": "\n".join(context)
                + "\n\nWrite the caption and hashtags for this post.",
            },
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }

    return _post_chat(url, body)


def _post_chat(url: str, body: dict) -> dict:
    """POST the chat request with retries; raise on persistent failure."""
    for attempt in range(3):
        try:
            r = requests.post(url, json=body, timeout=60)
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                return _parse_caption_response(text)
        except Exception as e:
            print(f"[llama] generate_caption attempt {attempt+1} failed: {e}")
            time.sleep(2)
    raise RuntimeError("Failed to generate caption after 3 attempts")


def format_caption(
    quote: str,
    author: str,
    caption_data: dict | None = None,
    max_chars: int = 2200,
) -> str:
    """
    Build the final upload caption: model caption (or the quote as fallback),
    attribution, then hashtags. Instagram's caption limit is 2200 characters.
    """
    if caption_data and caption_data.get("caption"):
        caption = caption_data["caption"]
    else:
        caption = quote
    parts = [caption]
    if author:
        parts.append(f"— {author}")
    hashtags = caption_data.get("hashtags", []) if caption_data else []
    if hashtags:
        parts.append(" ".join(hashtags))
    return "\n\n".join(parts)[:max_chars]


def _build_art_prompt(system_prompt: str = None) -> str:
    return f"""Generate one self-contained image prompt for a square Instagram post.

Write it in flowing full sentences, 60-120 words, following this order:
subject → art style & medium → lighting → composition & camera → mood & color palette → detail & texture.

Rules:
- Output ONLY the prompt text, nothing else; no markdown, no backticks, no quotes around it
- Lead with the subject and its specific attributes; keep the remaining sections in the order above
- Always name a concrete art style and medium; if the brief names a style (anime, cyberpunk, noir, watercolor, etc.), commit to it with 2-3 signature descriptors of that style
- If the subject is a character or person: they must NOT look directly at the viewer — use a three-quarter view looking away, a profile, a distant gaze, closed eyes, or a view from behind; if facing forward is unavoidable, add soft focus or shallow depth of field
- Describe lighting explicitly (source + quality), composition (framing, angle, lens), and a restrained color palette
- Favor a clean, uncluttered scene: a few strong elements beat many small ones, so avoid tiny secondary subjects, trinkets, and fussy surface minutiae
- Keep backgrounds calm and low-detail (plain, soft, or gently blurred) — never busy or hyper-detailed
- Reserve one clean, low-detail area (name its location) for a later quote overlay — plain empty space such as soft sky, a smooth wall, or out-of-focus background, never a frame, board, screen, or sign
- Do not include words, letters, typography, logos, signatures, watermarks, borders, or UI elements in the artwork
- Avoid objects that tend to render with writing: books or papers with visible covers/pages, posters, signs, billboards, graffiti, murals, screens or displays, headbands with metal plates, printed clothing, name tags, banners
- Avoid stacking unrelated symbols or multiple competing focal subjects

{f"Creative brief: {system_prompt}" if system_prompt else "Creative brief: an introspective, editorial mood with a memorable visual metaphor."}"""


def build_overlay_system_prompt(recalled_quotes: list[dict] | None = None) -> str:
    """Build the overlay system prompt, injecting condensed font descriptions.

    When `recalled_quotes` is provided, the model must pick one of those real
    quotes verbatim instead of writing an original line.
    """
    font_guide = _get_registry().build_prompt_text(max_chars=2000)
    if recalled_quotes:
        quote_rule = """Real quotes from the requested source are listed in the user message. Choose the ONE that best fits this artwork and repeat it VERBATIM — exact wording, punctuation, and capitalization. Do not reword, shorten, or "improve" it.

Author attribution rules:
- Set "author" to the source of the chosen quote exactly as given (e.g. "Spirited Away", "Naruto"). When the listing also names a character, use "Character — Source" (e.g. "Itachi Uchiha — Naruto"). Never invent or alter the attribution."""
        quote_field = '- "quote": the chosen real quote, copied word for word from the list'
    else:
        quote_rule = """Write one original quote inspired by the supplied art direction. The quote must be specific and emotionally clear, not a generic motivational cliché. Use 8-16 words, one sentence, and no more than 2 short lines when rendered.

Author attribution rules:
- When the art direction or content category references a known source — an anime, film, book, character, or person — set "author" to that source (e.g. "Spirited Away", "Naruto", "Rumi"). Always credit the source when one is implied.
- Set "author" to an empty string ONLY when the quote is genuinely original writing with no attributable source."""
        quote_field = '- "quote": one original, impactful sentence (8-16 words)'

    return f"""You are an art director creating a premium, readable Instagram quote post.

{quote_rule}

Available fonts (pick the specific "font" name; "font_style" is its category):

{font_guide}

Style the text to complement the artwork, not fight it:
- Match the visual mood of the image: an anime or neon artwork should get a font, color, and effect that fit that aesthetic (e.g. a display or handwritten font with an accent color pulled from the art) — never default to plain white on colorful art
- Choose text_color that contrasts with the art's background area so the quote stays readable
- Prefer medium font size and subtle effects (shadow or none) unless the art style calls for more

Output a JSON object with:
{quote_field}
- "author": the source (anime/film/book/person) when one is implied, otherwise an empty string
- "font_style": font category from: "serif", "sans-serif", "monospace", "handwritten", "display"
- "font": one specific font name from the list above (e.g. "Cormorant Garamond")
- "font_weight": "regular" or "bold"
- "alignment": one of: "center", "left", "right"
- "text_color": hex color like "#FFFFFF"
- "position": text anchor from: "bottom", "bottom-left", "bottom-right", "top", "top-left", "top-right", "center", "middle-left", "middle-right"
- "x_offset": signed percentage (-15 to 15) fine-tuning horizontal placement from the anchor; 0 = default
- "y_offset": signed percentage (-15 to 15) fine-tuning vertical placement from the anchor; 0 = default
- "font_size": relative size: "small", "medium", "large", "xlarge"
- "background_overlay": readability backdrop: "none", "dark-bottom", "light-top", "dark-center", or "text-block"; use a gradient backdrop for subtle separation, or "text-block" (semi-transparent dark/light rounded rectangle behind the text) when the art is busy or colorful and the quote needs maximum separation
- "text_effect": text treatment: "none", "shadow", "outline", "glow" — prefer a subtle shadow when needed
- "glow_color": hex color used when text_effect is "glow" (e.g. "#FFD700")
- "text_gradient_from" / "text_gradient_to": hex colors for a vertical gradient fill; omit or use "" for solid color
- "letter_spacing": pixels between letters, 0 to 10; 0 = normal
- "tag": 1-2 word vibe label for grouping this post (e.g. stoicism, ocean-calm, naruto) — lowercase, no spaces (hyphens ok)

Choose a position that avoids the main subject described in the art direction.
Output ONLY valid JSON. No explanations, no markdown."""


def _parse_overlay_response(text: str) -> dict:
    """Parse the LLM's JSON response into a styling dict."""
    import json
    # Try to extract JSON from the response
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            return _normalise_overlay(data)
        except json.JSONDecodeError:
            pass

    # Fallback: just use the text as the quote with defaults
    return _normalise_overlay({
        "quote": text.strip(),
        "author": "",
    })


def _normalise_overlay(data: dict) -> dict:
    """Validate model-controlled styling before it reaches Pillow."""
    return _get_registry().validate(data)


_ART_SYSTEM = """You are a creative art director and professional image-prompt engineer for premium Instagram content.

Write image prompts using this six-part structure, in order:
1. SUBJECT: who/what is in the frame, with specific attributes (age, clothing, materials, expression, posture)
2. STYLE & MEDIUM: always name a concrete art style and medium from this pool — cinematic photography on 35mm film, editorial studio portrait (85mm, soft light), oil painting (impasto brushwork, rich palette), watercolor concept art, gouache illustration, pencil charcoal sketch, linocut print, stained glass window, fresco mural, mixed-media collage. When the brief names a style — anime, cyberpunk, surrealism — commit to it fully with its signature visual language (e.g. anime: bold linework, cel shading, expressive eyes, painterly backgrounds); never fall back to a generic look
3. LIGHTING: describe lighting as a rig (key light direction + softness, fill level, rim/back light) — e.g., "soft key from camera-left through diffusion, low fill creating sculpted shadows, subtle rim outlining shoulders"; plus scenarios like golden hour, neon rim light, soft diffused window light, volumetric god rays, dramatic chiaroscuro
4. COMPOSITION & CAMERA: framing (wide/medium/close-up), angle (eye-level, slightly elevated, low angle looking up), explicit composition rules (rule of thirds, leading lines, negative space placement, foreground/midground/background layering), and specific lens/camera specs (85mm for portraits at f/2.8 for moderate blur, 35mm for environmental context, 50mm for natural perspective)
5. MOOD & COLOR: emotional tone plus color grading direction (teal and orange, bleach bypass, warm grade, desaturated) and a named palette with precise hex references where relevant (e.g., #FFD700, #E91E63) or named combinations (warm amber and teal, muted pastel, high-contrast noir); specify exactly where each color lives in the frame
6. DETAIL & TEXTURE: favor restraint — broad shapes, one unifying finish (a hint of film grain, soft brushwork, gentle glass sheen), and atmosphere carried by light (volumetric haze, distant elements softly blurred) rather than accumulated minutiae. The whole composition should still read at phone-thumbnail size: prefer forms and lighting a viewer grasps at a glance over fine detail they'd need to zoom to see. Never use cliché quality tokens like "masterpiece, 8k, hyperrealistic"

Hard rules:
- If the brief requests a specific aesthetic, commit to it with 2-3 signature style descriptors. A generic photo-like render of an anime request is a failure.
- When a character appears, they must NOT look directly at the viewer. Use "three-quarter view looking away", "profile facing left", "gazing into the distance", "eyes closed in quiet reflection", or "seen from behind". If the subject must face forward, specify "soft focus on the face" or "shallow depth of field, face gently out of focus".
- When the brief names a specific character (a quote attribution, a requested figure), do NOT render a recognizable face at all: keep any figure faceless, turned away, silhouetted, or blurred beyond recognition, and carry identity through iconic details and setting instead.
- Neon or glow must be tasteful and restrained: rim lighting, reflections on wet surfaces, a limited palette of 2-3 glow colors. Never a flat wall of saturated neon.
- Reserve one clean, low-detail area — name its location (upper third, left third, lower band) — for a later quote overlay; the text must never fight the focal point. The reserved area must be plain empty space (soft sky, smooth wall, out-of-focus background, shadowed ground), never a frame, board, screen, sign, or any object.
- Never include text or anything that carries text: no words, letters, typography, signage, banners, posters, labels, captions, logos, watermarks, or UI elements. Also avoid objects that image models instinctively fill with writing: no books, magazines, newspapers, or loose papers with visible covers or pages; no billboards, neon signs, storefront signs, graffiti, murals, or scrolls with markings; no headbands with metal plates, printed t-shirts, name tags, screens, or displays. The artwork must be text-free; text is added later by the overlay system.
- One clear focal subject, off-center, large enough to read at thumbnail size. No competing focal subjects, no collages, no stacked symbols.
- **Style diversity**: Each generation must use a different art style from the pool above — do NOT default to anime or any single style. Rotate through: photography, oil painting, watercolor, pencil sketch, linocut, stained glass, fresco, collage, and only then anime/surrealism/cyberpunk when the brief suggests them.
- **Category → style mapping** (use as a strong hint when the brief suggests one):
  - timeless, weighty, philosophical moods → editorial photography, oil painting, or fresco mural
  - literary, luminous, bookish moods → watercolor concept art, gouache illustration, or stained glass
  - gritty, textual, raw moods → pencil charcoal sketch or linocut print
  - anime / manga / cel-shaded looks → cel-shaded anime key visual (bold linework, expressive eyes, painterly backgrounds)
  - any other named genre, era, or aesthetic in the brief (noir, cyberpunk, ukiyo-e, baroque…) → commit to it fully with 2-3 signature descriptors of that style

Write 60-120 words in flowing full sentences (the target generator is Flux — it reads natural language, not comma-tag soup). Make it specific and vivid."""


_CAPTION_SYSTEM = """You are a social media caption writer for a professional Instagram quote page.

Write one engaging Instagram caption for the image being posted. The caption must:
- Be 1-3 sentences that complement the quote already on the image without repeating it verbatim
- Sound authentic and editorial, not like generic motivational spam
- End with a light hook or question that invites engagement
- Never include hashtags inside the caption text itself

Then provide a hashtag block:
- 12-20 hashtags total, a mix of TRENDING (broad, high-volume tags like #quotes, #mindset, #motivation) and RELATED (niche tags specific to the image's subject, mood, category, and quote theme)
- When the user message lists "Currently trending searches", prefer hashtags that align with those terms where they fit the post naturally; never force a trending term that clashes with the quote or image
- Output them with a leading "#", lowercase, words separated by underscores (e.g. #mindset_matters)
- Never invent hashtags for real people or brands

Output ONLY a JSON object with exactly two keys:
{
  "caption": "the caption text",
  "hashtags": ["#trending_tag", "#related_tag"]
}

No explanations, no markdown, no text outside the JSON."""


def _parse_caption_response(text: str) -> dict:
    """Parse the LLM's caption JSON; fall back to the raw text as the caption."""
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            caption = str(data.get("caption", "")).strip()
            raw_tags = data.get("hashtags", [])
            if isinstance(raw_tags, str):
                raw_tags = [raw_tags]
            # Sanitise: lowercase, spaces -> underscores, keep alnum/_,
            # drop tags with no letters (e.g. pure numbers), dedupe.
            seen: set[str] = set()
            hashtags: list[str] = []
            for tag in raw_tags:
                cleaned = "".join(
                    c if c.isalnum() or c == "_" else "_"
                    for c in str(tag).strip().lstrip("#").lower()
                ).strip("_")
                if not any(c.isalpha() for c in cleaned):
                    continue
                if cleaned not in seen:
                    seen.add(cleaned)
                    hashtags.append(f"#{cleaned}")
            return {"caption": caption, "hashtags": hashtags}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return {"caption": text.strip(), "hashtags": []}


def _wait_for_vram_free(timeout: int = 30, free_threshold_gb: float = 2.0):
    """Wait for GPU VRAM to free up after killing llama-server.

    Queries nvidia-smi when available (any GPU, no model-name matching);
    when no GPU tooling exists there is nothing to wait on beyond a short
    grace period — the driver reclaims VRAM on process exit, and stop_llama
    already reaped the process. Returns True when VRAM looks free.
    """
    import shutil
    import subprocess

    smi = shutil.which("nvidia-smi")
    if smi is None:
        time.sleep(3)
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = subprocess.run(
                [smi, "--query-gpu=memory.used",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            used_mb = [
                float(line.strip())
                for line in result.stdout.splitlines()
                if line.strip()
            ]
            if used_mb and all(mb / 1024 < free_threshold_gb for mb in used_mb):
                print(f"[llama] VRAM free: {min(used_mb):.0f}MiB used")
                return True
            if not used_mb:
                return True  # unexpected output — don't block the pipeline
        except Exception as e:
            print(f"[llama] VRAM check failed: {e}")
            return True  # a broken query must never stall generation
        time.sleep(2)
    print("[llama] VRAM wait timed out — proceeding anyway")
    return False
