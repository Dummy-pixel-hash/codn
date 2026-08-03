# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Primary user**: The developer/creator running `codn` on a local Linux workstation with a GPU (NVIDIA, for ComfyUI/Flux). They operate in a headless or semi-headless environment — starting services via `./start-all.sh` and triggering content generation through the API (curl, scripts, or a future UI).
- **Secondary audience**: Instagram followers who see the generated quote-art images in their feed. They are not the product's user — they are its output's consumer.

## Product Purpose

Automate the end-to-end creation of Instagram quote-art posts: generate an art background with a local diffusion model, compose a styled quote with text overlay via a local LLM, and publish the result to Instagram — all in one pipeline. Success means producing publishable quote-art images with minimal human intervention.

## Positioning

A self-contained, offline-first pipeline that combines a large language model (Bonsai-27B) for creative writing (art prompts, quotes, captions) with a diffusion model (Flux-2 Klein via ComfyUI) for visual generation — all running on local hardware. The VRAM-sharing orchestration (kill/swap llama-server ↔ ComfyUI) is the defining mechanism: it enables two heavy models to share one GPU without requiring 24 GB+ of VRAM.

## Operating Context

- **Local workstation**: All heavy lifting runs locally — llama-server, ComfyUI, Pillow overlay, Instagram uploader.
- **Startup**: `./start-all.sh` launches a tunnel (ngrok preferred, Cloudflare fallback), then the FastAPI app on port 4000.
- **Pipeline**: Each `/generate` call runs four phases sequentially: (1) art prompt from Bonsai-27B, (2) art generation via ComfyUI/Flux-2, (3) text overlay via Bonsai-27B again, (4) Instagram upload via Graph API.
- **Tunnel management**: ngrok provides a stable HTTPS endpoint for Meta's media crawler; Cloudflare Quick Tunnel is the fallback.
- **Data sources**: Curated quotes in `data/quotes.json` (anime, movie, literature categories, ~2700 entries). Real-time Google Trends feed (6h cache) seeds caption hashtags.
- **Deduplication**: SQLite database (`quotes.db`) tracks used art prompts by content hash to ensure each generated image is unique.

## Capabilities and Constraints

- **LLM**: Bonsai-27B-Q1_0.gguf via llama-server on port 8001 (configurable). Generates art prompts, quotes, overlay styling instructions, and captions.
- **Diffusion**: ComfyUI with Flux-2 Klein 4B FP8 model (1024×1024 square images). Workflow defined in `workflows/art_generation.json`.
- **Overlay**: Pillow-based text rendering with curated font library (`fonts.json`), 9 anchor positions, effects (shadow, outline, glow, gradient fill), bold weight, letter-spacing.
- **Instagram**: Requires Professional account, long-lived access token, public HTTPS image URL (tunnel). Supports both post and Reel (with music) upload.
- **VRAM constraint**: Models are started/stopped sequentially to share one GPU — llama-server dies before ComfyUI starts, and vice versa.
- **Categories**: `philosophy`, `anime`, `literature`, `literary`, `general` (and others from the quotes dataset).
- **Optional theme filter**: The `/generate` endpoint accepts a `theme` parameter to constrain art prompts.
- **Manual upload**: `/upload` endpoint for uploading pre-existing images with captions.
- **Undecided**: Whether the product will eventually ship a web UI, REST API only, or both. No pricing or deployment target confirmed yet.

## Brand Commitments

- **Name**: "codn" — styled in all lowercase throughout the codebase and documentation.
- **Tagline**: "Instagram Content Creator Machine."
- **Voice**: Technical, direct, no-nonsense README style. Uses emoji sparingly (⚡ for skill triggers, ⭐ for emphasis).
- **Assets**: `fonts.json` font manifest, `data/quotes.json` curated quote database, `workflows/art_generation.json` ComfyUI workflow definition.

## Evidence on Hand

- `README.md` — project documentation with architecture diagram, pipeline steps, API endpoints, config table.
- `data/quotes.json` — ~2700 curated quotes across anime, movie, and literature categories with source attribution.
- `workflows/art_generation.json` — ComfyUI workflow definition using Flux-2 Klein 4B FP8.
- `fonts.json` — font manifest for text overlay rendering.
- `.env.example` — configuration template with all required environment variables.
- **Absences**: No web UI, no DESIGN.md, no PRODUCT.md (this file), no Dockerfile, no CI/CD pipeline, no test suite (only `tests/` directory exists with `pytest.ini` but no tests written yet).

## Product Principles

1. **Offline-first**: Every model runs locally; the only network dependency is Instagram's API and the tunnel for public image URLs.
2. **Sequential orchestration over parallelism**: Two heavy models share one GPU by taking turns — start, use, kill, repeat.
3. **Deduplication by default**: Art prompts and quotes are hashed to prevent repetition across generations.
4. **Graceful degradation**: Missing trends feed, caption generation failure, or music overlay failure all degrade silently without breaking the pipeline.

## Accessibility & Inclusion

- **Platform**: Web (FastAPI server with REST API). No mobile app.
- **Text rendering**: Supports shadow, outline, and glow effects for readability against varied art backgrounds.
- **Font variety**: Multiple font families available via `fonts.json` manifest for stylistic flexibility across quote genres.
