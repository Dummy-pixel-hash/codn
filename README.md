# codn — Instagram Content Creator Machine

AI-powered Instagram content generator: generates art backgrounds via ComfyUI (Flux-2 Klein), adds styled text overlays via a local LLM, deduplicates quotes, and uploads to Instagram via the Graph API.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Text Model   │     │  ComfyUI     │     │  Text Overlay │     │  Instagram   │
│  (Bonsai-27B) │     │  (Flux-2)    │     │  (Pillow)    │     │  Graph API   │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       ▲                   ▲                   │                    │
       │  start/kill       │  start/kill        │                   │
       ────── FastAPI ──────────────────────────┐                    │
       │         (port 4000)                     │                    │
       ───────── ngrok (port 4040) ─────────────┐                    │
                                                  ─── Webhook ────────┐
```

## Pipeline

1. **Text model generates art prompt** → Bonsai-27B via llama-server
2. **Kill llama-server** → free VRAM for ComfyUI
3. **ComfyUI generates art** → Flux-2 Klein 4B FP8 (your workflow)
4. **Kill ComfyUI** → free VRAM
5. **Reload text model** → generate quote + styling instructions
6. **Apply text overlay** → Pillow (CPU, no GPU needed)
7. **Upload to Instagram** → Graph API

## Quick Start

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your Instagram credentials, ngrok token, etc.

# 3. Start everything
chmod +x start-all.sh
./start-all.sh
```

## First-Time Setup

Open the dashboard (http://localhost:4000, or your LAN IP on port 4000).
A setup banner shows what's missing — click **Open setup** and walk the wizard:

1. **Access** — paste the `API_TOKEN` from `.env` (printed by `start-all.sh` on first boot). Session-only, never stored on disk.
2. **Model** — check the detected `.gguf` path or save a new one. Saved straight to `.env`, no restart needed.
3. **Instagram** — manual for now: add these two lines to `.env`, restart the app, then hit **Re-check link** in the wizard:
   ```bash
   INSTAGRAM_ACCESS_TOKEN=<your long-lived token>
   INSTAGRAM_BUSINESS_ID=<your IG business/page ID>
   ```
   (Requires an Instagram Professional account. One-click save inside the wizard lands with the upcoming server update.)
4. **Services & test** — confirm tunnel + ComfyUI are green, then **Run trial generation** (no upload) to prove the pipeline works end to end.

Re-run anytime via Settings → Run setup wizard. The banner stays hidden once all 5 checks pass.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/config` | Current config (masked) |
| POST | `/generate` | Generate a new quote image |
| POST | `/upload` | Upload an existing image to Instagram |
| GET | `/quotes` | Get stats (for debugging) |

### Example: Generate Content

```bash
curl -X POST http://localhost:4000/generate \
  -H "Content-Type: application/json" \
  -d '{"category": "philosophy", "upload": true}'
```

### Example: Upload Existing Image

```bash
curl -X POST http://localhost:4000/upload \
  -H "Content-Type: application/json" \
  -d '{"image_path": "/home/Darsh/codn/output/image.jpg", "caption": "Hello world"}'
```

## Config

All config in `.env` (see `.env.example`). Key vars:

| Var | Description |
|-----|-------------|
| `INSTAGRAM_ACCESS_TOKEN` | Long-lived IG access token |
| `INSTAGRAM_BUSINESS_ID` | Your IG business account ID |
| `NGROK_AUTH_TOKEN` | Your ngrok auth token |
| `PUBLIC_BASE_URL` | Optional stable public HTTPS URL serving `/media`; otherwise `start-all.sh` passes the live ngrok URL to the app and the uploader discovers ngrok on port 4040 |
| `LLAMA_MODEL_PATH` | Path to Bonsai-27B-Q1_0.gguf |
| `COMFYUI_URL` | ComfyUI URL (default http://localhost:8189) |

## Quote Sources

The text model generates creative quotes dynamically — no predefined list. Quotes are deduplicated via SQLite content hashing to prevent repetition.

## Music

Instagram image publishing requires an Instagram Professional account, a compatible access token, and a public HTTPS image URL. When ngrok is running, generated files are served from `/media/{filename}` and the live tunnel URL is used before any stale Cloudflare log entry. Set `PUBLIC_BASE_URL` when using another public host. On ngrok's free plan, browser-style HTML requests may show ngrok's interstitial; the media endpoint returns JPEG directly and the uploader uses a crawler-style request for its preflight. A paid ngrok plan or another stable media host is required if Meta still receives the interstitial.

Music overlay for Reels is attempted when the `music` option is provided. If Instagram's API doesn't support it for the account type, the reel uploads without music (no failure).
