"""
codn — Instagram Content Creator Machine

Orchestrates: text model → art generation → text overlay → Instagram upload
"""

import os
import sys
import json
import time
import uuid
import asyncio
import requests
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Optional

import config
import database
import llama_manager
import comfy_manager
import text_overlay
import instagram_uploader
import trending

# ── Lifecycle ──────────────────────────────────────────────────────────────

app = FastAPI(title="codn", version="0.1.0")

_llama_proc = None
_comfy_proc = None
_shutdown_requested = False


@asynccontextmanager
async def lifespan(app):
    """Handle startup and shutdown lifecycle."""
    # Startup
    database.init_db()
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("[codn] Initialized")
    yield
    # Shutdown
    print("[codn] Shutting down...")
    if _llama_proc:
        print("[codn] Stopping llama-server...")
        llama_manager.stop_llama(_llama_proc)
    if _comfy_proc:
        print("[codn] Stopping ComfyUI...")
        comfy_manager.stop_comfyui(_comfy_proc)
    print("[codn] Shutdown complete")


app.router.lifespan_context = lifespan


# ── Models ─────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    theme: Optional[str] = None
    category: Optional[str] = None  # philosophy, anime, literature, etc.
    upload: bool = True
    music: Optional[str] = None  # music asset ID for Reels


class ConfigResponse(BaseModel):
    instagram_account: Optional[str] = None
    output_dir: str
    prompts_generated: int
    llama_running: bool
    comfy_running: bool


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Allow Meta's crawler to fetch generated media through the tunnel."""
    return "User-agent: *\nAllow: /\n"


@app.get("/media/{filename}")
async def serve_media(filename: str):
    """Serve generated images to Instagram's media fetcher via ngrok."""
    output_dir = config.OUTPUT_DIR.resolve()
    image_path = (output_dir / filename).resolve()
    if image_path.parent != output_dir or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(
        image_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/comfy/status")
async def comfy_status():
    """Check ComfyUI status and installed nodes."""
    try:
        r = requests.get(f"{config.COMFYUI_URL}/object_info", timeout=5)
        if r.status_code == 200:
            info = r.json()
            node_types = list(info.keys())
            required = ["Flux2Scheduler", "EmptyFlux2LatentImage", "ConditioningZeroOut"]
            missing = [n for n in required if n not in node_types]
            return {
                "connected": True,
                "nodes_count": len(node_types),
                "required_nodes": required,
                "missing": missing,
            }
        return {"connected": False, "error": r.text[:200]}
    except Exception as e:
        return {"connected": False, "error": str(e)}


@app.get("/config")
async def get_config():
    account = instagram_uploader.get_account_info()
    return ConfigResponse(
        instagram_account=account["username"] if account else None,
        output_dir=str(config.OUTPUT_DIR),
        prompts_generated=database.get_used_count(),
        llama_running=llama_manager.is_running(),
        comfy_running=comfy_manager.is_running(),
    )


@app.post("/generate")
async def generate(req: GenerateRequest):
    """Generate a new quote image and optionally upload it."""
    global _llama_proc, _comfy_proc

    # Phase 1: Generate art prompt using text model
    print("[codn] Phase 1: Generating art prompt...")
    if not llama_manager.is_running():
        print("[codn] Starting llama-server...")
        _llama_proc = llama_manager.start_llama()
        if not _llama_proc:
            raise HTTPException(status_code=500, detail="Failed to start llama-server")

    art_prompt = llama_manager.generate_art_prompt(
        _llama_proc,
        system_prompt=f"Generate an art prompt for the category: {req.category or 'general'}. {f'Constraint: {req.theme}' if req.theme else ''}",
    )

    # Check deduplication
    if database.prompt_exists(art_prompt):
        print("[codn] Prompt already used, regenerating...")
        art_prompt = llama_manager.generate_art_prompt(
            _llama_proc,
            system_prompt=f"Generate a DIFFERENT art prompt. Category: {req.category or 'general'}. Constraint: {req.theme or 'none'}",
        )

    database.save_prompt(art_prompt)
    print(f"[codn] Art prompt: {art_prompt[:120]}...")

    # Kill llama-server to free VRAM — MUST happen before ComfyUI starts
    if _llama_proc:
        print("[codn] Stopping llama-server (freeing VRAM)...")
        llama_manager.stop_llama(_llama_proc, wait_for_vram=True)
        _llama_proc = None
        # Double-check it's actually dead
        for _ in range(10):
            if not llama_manager.is_running():
                break
            print("[codn] Waiting for llama-server to fully stop...")
            await asyncio.sleep(1)
        print("[codn] llama-server stopped, VRAM freed")

    # Phase 2: Generate art using ComfyUI
    print("[codn] Phase 2: Generating art...")
    if not comfy_manager.is_running():
        print("[codn] Starting ComfyUI...")
        _comfy_proc = comfy_manager.start_comfyui()
        if not _comfy_proc:
            raise HTTPException(status_code=500, detail="Failed to start ComfyUI")

    image_path = comfy_manager.generate_art(art_prompt)

    # Kill ComfyUI to free VRAM
    if _comfy_proc:
        print("[codn] Stopping ComfyUI (freeing VRAM)...")
        comfy_manager.stop_comfyui(_comfy_proc)
        _comfy_proc = None
        await asyncio.sleep(2)

    if not image_path:
        raise HTTPException(status_code=500, detail="Art generation failed — check ComfyUI logs")

    print(f"[codn] Art generated: {image_path}")

    # Phase 3: Add text overlay using text model
    print("[codn] Phase 3: Generating text overlay...")
    if not llama_manager.is_running():
        print("[codn] Starting llama-server for text overlay...")
        _llama_proc = llama_manager.start_llama()
        if not _llama_proc:
            raise HTTPException(status_code=500, detail="Failed to start llama-server for text overlay")

    # Give the overlay model the actual creative context. A file path contains
    # no useful visual or thematic information, so passing it produced generic
    # quotes and arbitrary placement decisions.
    overlay_style = llama_manager.generate_text_overlay(
        _llama_proc,
        image_path,
        art_prompt=art_prompt,
        category=req.category,
        theme=req.theme,
    )
    print(f"[codn] Overlay style: {json.dumps(overlay_style)}")

    # Apply overlay (Pillow — no GPU needed)
    output_name = f"{uuid.uuid4().hex[:8]}_quote.jpg"
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = str(config.OUTPUT_DIR / output_name)
    text_overlay.add_text_overlay(image_path, output_path, overlay_style)
    print(f"[codn] Final image: {output_path}")

    # Generate the upload caption + hashtags while llama-server is still
    # running — it gets killed below to free VRAM.
    caption_data = None
    if req.upload:
        print("[codn] Generating upload caption + hashtags...")
        try:
            caption_data = llama_manager.generate_caption(
                _llama_proc,
                quote=overlay_style.get("quote", ""),
                author=overlay_style.get("author", ""),
                art_prompt=art_prompt,
                category=req.category,
                theme=req.theme,
                trending_terms=trending.get_trending_terms(),
            )
        except Exception as e:
            print(f"[codn] Caption generation failed, falling back: {e}")
            caption_data = None

    # Kill llama-server (we're done with it)
    if _llama_proc:
        llama_manager.stop_llama(_llama_proc)
        _llama_proc = None

    # Phase 4: Upload to Instagram
    if req.upload:
        print("[codn] Phase 4: Uploading to Instagram...")
        caption = llama_manager.format_caption(
            overlay_style["quote"], overlay_style.get("author"), caption_data
        )
        if req.music:
            # Instagram fetches the public image while this call is in
            # progress. Keep Uvicorn's event loop free to serve /media.
            result = await asyncio.to_thread(
                instagram_uploader.upload_reel, output_path, caption, req.music
            )
        else:
            result = await asyncio.to_thread(
                instagram_uploader.upload_post, output_path, caption
            )

        return JSONResponse({
            "status": "uploaded" if result else "upload_failed",
            "image": output_path,
            "prompt": art_prompt,
            "overlay": overlay_style,
            "upload_result": result,
        })

    return JSONResponse({
        "status": "generated",
        "image": output_path,
        "prompt": art_prompt,
        "overlay": overlay_style,
    })


@app.post("/upload")
async def upload_manual(body: dict):
    """Upload an existing image to Instagram."""
    image_path = body.get("image_path", "")
    caption = body.get("caption", "")
    is_reel = body.get("is_reel", False)
    music = body.get("music", None)

    if not image_path:
        raise HTTPException(status_code=400, detail="image_path required")

    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="Image not found")

    if is_reel:
        # upload_reel/upload_post use synchronous HTTP requests. Running
        # them in a worker lets Meta reach /robots.txt and /media through
        # ngrok while its Graph API request is still pending.
        result = await asyncio.to_thread(
            instagram_uploader.upload_reel, image_path, caption, music
        )
    else:
        result = await asyncio.to_thread(
            instagram_uploader.upload_post, image_path, caption
        )

    return JSONResponse({
        "status": "uploaded" if result else "failed",
        "result": result,
    })


@app.get("/quotes")
async def get_quotes(category: str = None):
    """Get used prompts (for debugging/monitoring)."""
    return {"message": "Use /generate to create new content", "prompts_tracked": database.get_used_count()}


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print(f"[codn] Starting on {config.APP_HOST}:{config.APP_PORT}")
    uvicorn.run(
        "main:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        reload=False,
    )
