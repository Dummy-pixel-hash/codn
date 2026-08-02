"""
Manage the llama.cpp server lifecycle: start, generate, kill.
Bonsai-27B-Q1_0.gguf is used for prompt generation and text overlay work.
"""

import json
import os
import signal
import subprocess
import time
import requests
from config import LLAMA_MODEL_PATH, LLAMA_PORT, BASE_DIR


def start_llama():
    """Start llama-server in the background."""
    if is_running():
        return True

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


def generate_text_overlay(
    llama_proc,
    art_image_path: str,
    art_prompt: str = "",
    category: str = "",
    theme: str = "",
    max_tokens: int = 512,
) -> dict:
    """
    Ask the text model what text + styling to overlay on the generated image.
    Returns a dict with text, font, alignment, color, position, etc.
    """
    url = f"http://localhost:{LLAMA_PORT}/v1/chat/completions"
    context = [f"Art direction: {art_prompt}" if art_prompt else ""]
    if category:
        context.append(f"Content category: {category}")
    if theme:
        context.append(f"User theme: {theme}")

    body = {
        "model": "Bonsai-27B",
        "messages": [
            {"role": "system", "content": _OVERLAY_SYSTEM},
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
                return _parse_overlay_response(text)
        except Exception as e:
            print(f"[llama] generate_text_overlay attempt {attempt+1} failed: {e}")
            time.sleep(2)
    raise RuntimeError("Failed to generate text overlay after 3 attempts")


def _build_art_prompt(system_prompt: str = None) -> str:
    return f"""Generate one self-contained prompt for a square Instagram image.

Rules:
- Output ONLY the prompt text, nothing else
- No markdown, no backticks, no quotes around it
- Describe one clear focal subject, one supporting environment, a restrained color palette, lighting, camera or art style, and composition
- Make the subject large enough to read at thumbnail size and place it off-center
- Reserve one clean, low-detail area for a later quote overlay; explicitly say where that area is
- Do not include words, letters, typography, logos, signatures, watermarks, borders, or UI elements in the artwork
- Avoid stacking unrelated symbols or multiple competing focal subjects

{f"Creative brief: {system_prompt}" if system_prompt else "Creative brief: an introspective, editorial mood with a memorable visual metaphor."}"""


_OVERLAY_SYSTEM = """You are an art director creating a premium, readable Instagram quote post.

Write one original quote inspired by the supplied art direction. The quote must be
specific and emotionally clear, not a generic motivational cliché. Use 8-16 words,
one sentence, and no more than 2 short lines when rendered. Do not invent a real
person's words: set author to an empty string for an original quote.

Output a JSON object with:
- "quote": one original, impactful sentence (8-16 words)
- "author": an empty string for original writing; only use a source when explicitly provided
- "font_style": font choice from: "serif", "sans-serif", "monospace", "handwritten", "display"
- "alignment": one of: "center", "left", "right"
- "text_color": hex color like "#FFFFFF"
- "position": where the text goes: "bottom", "top", "center", "middle-left", "middle-right"
- "font_size": relative size: "small", "medium", "large", "xlarge"
- "background_overlay": readability backdrop: "none", "dark-bottom", "light-top", "dark-center" — use a subtle backdrop when needed
- "text_effect": text treatment: "none", "shadow", "outline" — prefer a subtle shadow when needed

Choose a position that avoids the main subject described in the art direction.
Prefer medium font size. Output ONLY valid JSON. No explanations, no markdown."""


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
    allowed = {
        "font_style": {"serif", "sans-serif", "monospace", "handwritten", "display"},
        "alignment": {"center", "left", "right"},
        "position": {"top", "bottom", "center", "middle-left", "middle-right"},
        "font_size": {"small", "medium", "large", "xlarge"},
        "background_overlay": {"none", "dark-bottom", "light-top", "dark-center"},
        "text_effect": {"none", "shadow", "outline"},
    }
    result = {
        "quote": " ".join(str(data.get("quote", "")).split()).strip('"“”'),
        "author": " ".join(str(data.get("author", "")).split()).strip('"“”'),
        "font_style": data.get("font_style", "serif"),
        "alignment": data.get("alignment", "center"),
        "text_color": data.get("text_color", "#FFFFFF"),
        "position": data.get("position", "bottom"),
        "font_size": data.get("font_size", "medium"),
        "background_overlay": data.get("background_overlay", "none"),
        "text_effect": data.get("text_effect", "shadow"),
    }
    if not result["quote"]:
        result["quote"] = "Let the quiet reveal what noise conceals."
    if result["author"].lower() in {"unknown", "n/a", "none", "original"}:
        result["author"] = ""
    for key, values in allowed.items():
        if result[key] not in values:
            result[key] = "medium" if key == "font_size" else next(iter(values))
    if not isinstance(result["text_color"], str) or not _is_hex_color(result["text_color"]):
        result["text_color"] = "#FFFFFF"
    return result


def _is_hex_color(value: str) -> bool:
    return len(value) in {4, 7} and value.startswith("#") and all(
        char in "0123456789abcdefABCDEF" for char in value[1:]
    )


_ART_SYSTEM = """You are a creative art prompt generator for professional Instagram content.

Generate prompts that would produce stunning, professional-looking images. Think about:
- Art styles (digital painting, oil painting, watercolor, photography, surrealism, minimalism, etc.)
- Color palettes and moods
- Lighting and atmosphere
- Composition and visual impact
- Subjects and themes
- A quote caption will be overlaid on the image later, so keep some part of the composition open and uncluttered — balanced negative space anywhere in the frame (not necessarily at the bottom) — so the text can sit anywhere without covering the focal point

The prompt should be detailed enough to guide an AI image generator toward a professional result. Make it specific and vivid."""


def _wait_for_vram_free(timeout: int = 60):
    """Wait for GPU VRAM to free up after killing llama-server using nvtop."""
    import subprocess
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["nvtop", "-s"],
                capture_output=True, text=True, timeout=5
            )
            data = json.loads(result.stdout)
            for gpu in data:
                if "4060" in gpu.get("device_name", ""):
                    used_bytes = int(gpu.get("mem_used", 0))
                    total_bytes = int(gpu.get("mem_total", 1))
                    used_gb = used_bytes / (1024 ** 3)
                    total_gb = total_bytes / (1024 ** 3)
                    # If VRAM is below ~2GB, we're good to start ComfyUI
                    if used_gb < 2.0:
                        print(f"[llama] VRAM free: {used_gb:.1f}GB / {total_gb:.1f}GB")
                        return True
            # If no 4060 found, assume VRAM is clear
            if not data:
                return True
        except Exception as e:
            print(f"[llama] VRAM check failed: {e}")
            break
        time.sleep(2)
    print("[llama] VRAM wait timed out — proceeding anyway")
    return False
