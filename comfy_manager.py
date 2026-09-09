"""
Manage ComfyUI lifecycle: start, generate art, kill.
Uses the Flux-2 Klein workflow for professional art generation.
"""

import os
import signal
import subprocess
import time
import json
import random
import requests
from pathlib import Path
from config import COMFYUI_URL, COMFYUI_WORKFLOW_PATH, OUTPUT_DIR


def start_comfyui() -> subprocess.Popen | None:
    """Start ComfyUI in the background.

    Returns the Popen handle when this call started the server, or None when
    already running (no handle owned) or startup failed.
    """
    if is_running():
        return None

    cmd = [
        "/mnt/data/Models/venv/bin/python", "main.py",
        "--listen", "0.0.0.0",
        "--port", "8189",
        "--output-directory", str(OUTPUT_DIR),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd="/mnt/data/Models/ComfyUI",
        env={**os.environ, "HF_HOME": "/mnt/data/Models/cache/huggingface"},
        start_new_session=True,
    )

    # Wait for ComfyUI to be ready (up to 60s)
    for _ in range(120):
        try:
            r = requests.get(f"{COMFYUI_URL}/system_stats", timeout=2)
            if r.status_code == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.5)

    proc.kill()
    return None


def stop_comfyui(proc):
    """Kill the ComfyUI process."""
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.kill()
        except Exception:
            pass


def is_running():
    """Check if ComfyUI is already running."""
    try:
        r = requests.get(f"{COMFYUI_URL}/system_stats", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def check_comfyui_nodes():
    """Check which custom nodes are installed in ComfyUI."""
    try:
        r = requests.get(f"{COMFYUI_URL}/object_info", timeout=5)
        if r.status_code == 200:
            info = r.json()
            node_types = list(info.keys())
            # Check for required nodes
            required = ["Flux2Scheduler", "EmptyFlux2LatentImage", "ConditioningZeroOut"]
            missing = [n for n in required if n not in node_types]
            print(f"[comfy] Installed {len(node_types)} node types")
            print(f"[comfy] Required nodes: {required}")
            print(f"[comfy] Missing: {missing if missing else 'None'}")
            return info
    except Exception as e:
        print(f"[comfy] Node check failed: {e}")
    return None


def generate_art(prompt: str, output_dir: Path = OUTPUT_DIR, should_stop=None) -> str | None:
    """
    Send a prompt to ComfyUI and wait for the generated image.
    Returns the path to the output image, or None on failure/cancel.

    should_stop: optional zero-arg callable polled while waiting; when it
    returns True the wait aborts early (cooperative job cancel).
    """
    if not is_running():
        print("[comfy] ComfyUI not running, starting...")
        start_comfyui()

    # Load workflow
    workflow = _load_workflow()

    # Inject prompt into PrimitiveStringMultiline (node "6")
    workflow["6"]["inputs"]["value"] = prompt

    # The quote is rendered by Pillow later. Suppressing generated typography
    # avoids malformed letters, logos, and watermarks competing with it. The
    # object list targets the carriers image models fill with gibberish text
    # (book covers, signs, headband plates) even when no text is requested.
    if "5" in workflow:
        workflow["5"]["inputs"]["text"] = (
            "text, letters, words, typography, logo, watermark, signature, "
            "caption, border, frame, UI, book cover, book pages, poster, sign, "
            "billboard, graffiti, screen, display, headband plate, kanji, "
            "gibberish writing, nonsense symbols, duplicate subject, collage"
        )

    # Randomize seed for variety
    workflow["8"]["inputs"]["noise_seed"] = random.randint(0, 2**31 - 1)

    # Queue the prompt
    url = f"{COMFYUI_URL}/prompt"
    try:
        resp = requests.post(url, json={"prompt": workflow}, timeout=10)
    except Exception as e:
        print(f"[comfy] Failed to connect to ComfyUI: {e}")
        return None
    if resp.status_code != 200:
        print(f"[comfy] Failed to queue prompt: {resp.status_code} {resp.text[:500]}")
        return None

    result_data = resp.json()
    prompt_id = result_data.get("prompt_id")
    if not prompt_id:
        print(f"[comfy] Queue failed response: {result_data}")
        return None
    print(f"[comfy] Queued prompt: {prompt_id}")

    # Wait for completion (poll history)
    max_wait = 300  # 5 minutes max
    elapsed = 0
    while elapsed < max_wait:
        if should_stop is not None and should_stop():
            print("[comfy] Generation cancelled")
            return None
        try:
            hist_resp = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=5)
            if hist_resp.status_code == 200 and hist_resp.json():
                # Check for completed outputs
                outputs = _extract_outputs(hist_resp.json())
                if outputs:
                    return outputs[0]
        except Exception:
            pass
        time.sleep(2)
        elapsed += 2

    print("[comfy] Generation timed out")
    return None


def _load_workflow() -> dict:
    """Load the workflow JSON and return a mutable copy."""
    if COMFYUI_WORKFLOW_PATH.exists():
        with open(COMFYUI_WORKFLOW_PATH) as f:
            return json.load(f)
    raise FileNotFoundError(f"Workflow not found: {COMFYUI_WORKFLOW_PATH}")


def _extract_outputs(history_data: dict) -> list[str]:
    """Extract output image paths from ComfyUI history."""
    outputs = []

    # History is keyed by prompt_id (UUID string), take the last entry
    items = list(history_data.items())
    if not items:
        return outputs

    latest_output = items[-1][1].get("outputs", {})

    for node_id, node_outputs in latest_output.items():
        # ComfyUI 0.28: node_outputs is a dict like {"images": [...]}
        # older versions: a list of output dicts
        if isinstance(node_outputs, dict):
            node_outputs = [node_outputs]
        for output in node_outputs:
            # Walk through nested outputs looking for images
            _find_images(output, outputs)

    # History only stores filenames relative to the output dir; resolve full paths
    return [
        str(OUTPUT_DIR / f) if not os.path.isabs(f) else f
        for f in outputs
    ]


def _find_images(obj, results: list[str]):
    """Recursively find image file paths in ComfyUI output."""
    if isinstance(obj, str):
        if any(ext in obj.lower() for ext in (".png", ".jpg", ".jpeg", ".webp")):
            results.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            _find_images(item, results)
    elif isinstance(obj, dict):
        for val in obj.values():
            _find_images(val, results)
