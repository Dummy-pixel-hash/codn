"""
Configuration — reads from .env with sensible defaults.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return default
    try:
        return int(str(v).strip())
    except ValueError:
        print(f"[config] Invalid int for {name}={v!r}, using default {default}")
        return default


def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return default
    try:
        return float(str(v).strip())
    except ValueError:
        print(f"[config] Invalid float for {name}={v!r}, using default {default}")
        return default


# Paths
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = Path(env("OUTPUT_DIR", str(BASE_DIR / "output")))
QUOTES_DB = Path(env("QUOTES_DB", str(BASE_DIR / "quotes.db")))

# Instagram
INSTAGRAM_ACCESS_TOKEN = env("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_BUSINESS_ID = env("INSTAGRAM_BUSINESS_ID")
INSTAGRAM_ACCOUNT_ID = env("INSTAGRAM_ACCOUNT_ID")
INSTAGRAM_GRAPH_URL = env("INSTAGRAM_GRAPH_URL", "https://graph.instagram.com")
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL")
CLOUDFLARED_LOG = Path(env("CLOUDFLARED_LOG", str(BASE_DIR / "cloudflared.log")))

# ngrok
NGROK_AUTH_TOKEN = env("NGROK_AUTH_TOKEN")
# Reserved static domain (free plan: <name>.ngrok-free.app). Gives Meta a
# stable, trusted HTTPS host so its crawler can fetch generated media.
NGROK_STATIC_DOMAIN = env("NGROK_STATIC_DOMAIN")

# Text model (llama.cpp)
LLAMA_MODEL_PATH = Path(env("LLAMA_MODEL_PATH", str(BASE_DIR / "models" / "Bonsai-27B-Q1_0.gguf")))
LLAMA_PORT = env_int("LLAMA_PORT", 8001)

# ComfyUI
COMFYUI_URL = env("COMFYUI_URL", "http://localhost:8189")
COMFYUI_WORKFLOW_PATH = Path(env("COMFYUI_WORKFLOW_PATH", str(BASE_DIR / "workflows" / "art_generation.json")))

# App
APP_HOST = env("APP_HOST", "0.0.0.0")
APP_PORT = env_int("APP_PORT", 4000)

# Auth — bearer token required for all mutating endpoints and for
# config/history/quotes reads. Fail-closed: when empty, guarded endpoints
# return 503 until the operator sets a token. Generate one with:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
API_TOKEN = env("API_TOKEN")
