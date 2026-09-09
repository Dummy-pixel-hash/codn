"""
codn — Instagram Content Creator Machine

Orchestrates: text model → art generation → text overlay → Instagram upload
"""

import os
import shutil
import sys
import json
import mimetypes
import secrets
import threading
import time
import uuid
import asyncio
import requests
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Literal, Optional

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
    database.init_generations_table()
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _sweep_previews()
    print("[codn] Initialized")
    global _worker_task, _job_queue
    _job_queue = asyncio.Queue()  # fresh queue bound to this loop
    _worker_task = asyncio.create_task(_worker())
    yield
    # Shutdown
    print("[codn] Shutting down...")
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    if _llama_proc:
        print("[codn] Stopping llama-server...")
        llama_manager.stop_llama(_llama_proc)
    if _comfy_proc:
        print("[codn] Stopping ComfyUI...")
        comfy_manager.stop_comfyui(_comfy_proc)
    print("[codn] Shutdown complete")


app.router.lifespan_context = lifespan


# ── Generation job queue ───────────────────────────────────────────────────
# Single-GPU box: two heavy models time-share VRAM, so generations run one
# at a time in a background worker. POST /generate enqueues (202 + job_id);
# clients poll GET /jobs/{id}. DELETE /jobs/{id} requests cooperative cancel.

class _Cancelled(Exception):
    """Raised when a job's cancel flag is observed between phases."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    request: dict
    status: str = "queued"  # queued|running|done|error|cancelled
    phase: str = "queued"  # queued|art-prompt|art-gen|text-overlay|upload|done
    result: dict | None = None
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    position: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)


_jobs: dict[str, Job] = {}
# Created in lifespan (bound to the serving event loop). _get_queue()
# recreates it lazily so module import never binds a loop — otherwise test
# clients (one loop each) and reloads hit "attached to a different loop".
_job_queue: asyncio.Queue[str] | None = None
_worker_task: asyncio.Task | None = None
_MAX_JOBS = 50


def _get_queue() -> asyncio.Queue[str]:
    global _job_queue
    if _job_queue is None:
        _job_queue = asyncio.Queue()
    return _job_queue


def _job_public(job: Job) -> dict:
    return {
        "job_id": job.id,
        "status": job.status,
        "phase": job.phase,
        "position": job.position,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _prune_jobs() -> None:
    """Bound memory: drop oldest finished jobs beyond _MAX_JOBS."""
    if len(_jobs) <= _MAX_JOBS:
        return
    finished = sorted(
        (j for j in _jobs.values() if j.status in ("done", "error", "cancelled")),
        key=lambda j: j.finished_at or "",
    )
    for job in finished[: len(_jobs) - _MAX_JOBS]:
        del _jobs[job.id]


async def _worker() -> None:
    while True:
        queue = _get_queue()
        job_id = await queue.get()
        try:
            job = _jobs.get(job_id)
            if job is None:
                continue
            # Refresh queue positions for jobs still waiting.
            for i, j in enumerate(_jobs.values()):
                if j.status == "queued":
                    j.position = i
            if job.cancel.is_set():
                job.status = "cancelled"
                job.phase = "done"
                job.finished_at = _utcnow()
                continue
            await _run_job(job)
        finally:
            _get_queue().task_done()


# ── Request ID + Host guard ────────────────────────────────────────────────

@app.middleware("http")
async def _request_id(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]
    request.state.request_id = rid
    resp = await call_next(request)
    resp.headers["X-Request-ID"] = rid
    return resp


def _configure_host_guard():
    """Restrict Host headers to local + LAN + tunnel + configured hosts.

    Localhost, private LAN IPs (10/8, 172.16/12, 192.168/16, Tailscale
    100.64/10, link-local), `.local`/`.lan` names and the machine hostname
    are always allowed so the dashboard works over the LAN without extra
    config. Public hosts must match the tunnel patterns, PUBLIC_BASE_URL,
    or ALLOWED_HOSTS. "testserver" is the Starlette TestClient default.
    """
    import fnmatch
    import ipaddress
    import socket
    from urllib.parse import urlparse

    allowed_exact = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "testserver",
    }
    allowed_wildcards = [
        "*.ngrok-free.app",
        "*.ngrok.app",
        "*.ngrok.io",
        "*.trycloudflare.com",
        "*.local",
        "*.lan",
        "*.home",
        "*.internal",
    ]
    try:
        allowed_exact.add(socket.gethostname().lower())
    except OSError:
        pass
    if config.PUBLIC_BASE_URL:
        try:
            host = urlparse(config.PUBLIC_BASE_URL).hostname
            if host:
                allowed_exact.add(host.lower())
        except ValueError:
            pass
    extra = os.getenv("ALLOWED_HOSTS", "")
    for h in extra.split(","):
        h = h.strip().lower()
        if not h:
            continue
        if h == "*" or h.startswith("*."):
            if h not in allowed_wildcards:
                allowed_wildcards.append(h)
        else:
            allowed_exact.add(h)

    def _host_allowed(raw: str) -> bool:
        host = (raw or "").strip().lower()
        # Strip port (careful with IPv6 literals like [::1]:4000).
        if host.startswith("["):
            end = host.find("]")
            host = host[1:end] if end != -1 else host.strip("[]")
        elif host.count(":") == 1:
            host = host.split(":")[0]
        host = host.strip().strip(".")
        if not host:
            return False
        if host in allowed_exact:
            return True
        if any(fnmatch.fnmatch(host, pat) for pat in allowed_wildcards):
            return True
        # Single-label LAN hostnames (e.g. http://fedora:4000).
        if "." not in host and host.replace("-", "").replace("_", "").isalnum():
            return True
        try:
            ip = ipaddress.ip_address(host)
            # Allow any non-publicly-routable IP: loopback, RFC1918 LAN,
            # link-local, and shared CGNAT space (100.64/10, e.g. Tailscale).
            if not ip.is_global:
                return True
        except ValueError:
            pass
        return False

    class _HostGuardMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if not _host_allowed(request.headers.get("host", "")):
                return PlainTextResponse(
                    "Invalid host header — add this hostname to ALLOWED_HOSTS in .env "
                    "and restart (LAN IPs and .local names are allowed automatically).",
                    status_code=400,
                )
            return await call_next(request)

    app.add_middleware(_HostGuardMiddleware)
    print(f"[codn] Host guard: LAN + {sorted(allowed_exact)} + {sorted(allowed_wildcards)}")


_configure_host_guard()


def _server_error(request: Request | None, where: str, exc: Exception) -> HTTPException:
    """Log the full error server-side; return a generic 500 to the caller."""
    rid = getattr(getattr(request, "state", None), "request_id", "?")
    print(f"[codn] {where} failed (req {rid}): {exc!r}")
    return HTTPException(status_code=500, detail="Internal server error")


# ── Auth ───────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


async def require_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> bool:
    """Fail-closed bearer guard for mutating + sensitive read endpoints.

    Public (Meta's crawler can't present a token): /health, /robots.txt,
    /media/*, and the static dashboard. Everything else requires
    `Authorization: Bearer <API_TOKEN>`.
    """
    configured = (config.API_TOKEN or "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="API token not configured — set API_TOKEN in .env",
        )
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Missing API token")
    if not secrets.compare_digest(creds.credentials, configured):
        raise HTTPException(status_code=403, detail="Invalid API token")
    return True


# ── Models ─────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    theme: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=100)  # philosophy, anime, literature, etc.
    upload: bool = True
    music: Optional[str] = Field(default=None, max_length=200)  # music asset ID for Reels


# ─── Post-edit overlay model ──────────────────────────────────────

POSITION_ANCHORS = [
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
]

FONT_SIZES = ["small", "medium", "large", "xlarge"]

OverlayPosition = Literal[
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
    # Short forms the model also emits; normalized to the *-center anchors.
    "top", "bottom",
]

EditFontSize = Literal["small", "medium", "large", "xlarge"]

# Short-form position aliases from generation overlays.
_POSITION_ALIASES = {"top": "top-center", "bottom": "bottom-center"}


class OverlayFontPassthrough(BaseModel):
    """Generated typography the edit modal preserves verbatim.

    The modal only exposes 5 editable fields; everything here round-trips
    from the stored overlay so edits never reset the font/effects. All
    optional — absent for legacy rows, where renderer defaults apply.
    """
    font_style: Optional[str] = Field(default=None, max_length=32)
    font: Optional[str] = Field(default=None, max_length=128)
    font_weight: Optional[Literal["regular", "bold"]] = None
    alignment: Optional[Literal["center", "left", "right"]] = None
    x_offset: Optional[float] = None
    y_offset: Optional[float] = None
    background_overlay: Optional[str] = Field(default=None, max_length=32)
    text_effect: Optional[Literal["none", "shadow", "outline", "glow"]] = None
    glow_color: Optional[str] = Field(default=None, max_length=16)
    text_gradient_from: Optional[str] = Field(default=None, max_length=16)
    text_gradient_to: Optional[str] = Field(default=None, max_length=16)
    letter_spacing: Optional[int] = None


class PostEditOverlayRequest(OverlayFontPassthrough):
    """User-edited text overlay params — submit from the frontend editing modal.

    filename: the composition being edited (updated in place — one
    composition, never a second file). source_filename: clean base art to
    render from; when omitted the composition itself is used (legacy rows
    without a stored source, which can stack text).
    """
    filename: str = Field(max_length=512)
    source_filename: Optional[str] = Field(default=None, max_length=512)
    quote_text: str = Field(max_length=2000)
    author: str = Field(default="", max_length=200)
    font_size: EditFontSize = "medium"
    text_color: str = Field(default="#FFFFFF", max_length=16)
    position: OverlayPosition = "bottom-center"

class PreviewOverlayRequest(OverlayFontPassthrough):
    filename: str = Field(max_length=512)
    source_filename: Optional[str] = Field(default=None, max_length=512)
    quote_text: str = Field(max_length=2000)
    author: str = Field(default="", max_length=200)
    font_size: EditFontSize = "medium"
    text_color: str = Field(default="#FFFFFF", max_length=16)
    position: OverlayPosition = "bottom-center"


def _resolve_edit_source(req: PreviewOverlayRequest | PostEditOverlayRequest) -> tuple[str, str]:
    """Resolve (render_source, target) for an edit request.

    render_source is the clean base art (or the composition itself for
    legacy rows without a stored source). target is the composition file
    that previews display and apply updates. Both jailed to OUTPUT_DIR.
    """
    target_path, _ = _resolve_image(req.filename)
    if not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="Source image not found")
    src_name = (req.source_filename or "").strip()
    if not src_name:
        # Fall back to the row's stored clean-base art so API clients and
        # older frontends that don't send source_filename still avoid
        # stacking text onto the composition.
        try:
            for row in database.get_generations(limit=200):
                if row.get("image_filename") == os.path.basename(target_path):
                    src_name = row.get("source_filename") or ""
                    break
        except Exception:
            src_name = ""
    src_name = src_name or req.filename
    src_path, _ = _resolve_image(src_name)
    if not os.path.isfile(src_path):
        # Stored source pruned (e.g. output dir cleaned): fall back to the
        # composition itself rather than failing the edit.
        src_path = target_path
    return src_path, target_path


def _build_edit_style(req: PreviewOverlayRequest | PostEditOverlayRequest) -> dict:
    """Merge the 5 editable fields over preserved typography, then validate.

    Runs through the same FontRegistry validation as generation overlays,
    so unknown fonts clamp to real ones and offsets/hex colors are safe
    before reaching Pillow.
    """
    raw = req.model_dump(exclude_none=True)
    # Edit models use quote_text; the registry/renderer contract uses quote.
    if "quote_text" in raw:
        raw["quote"] = raw.pop("quote_text")
    # Tolerate bare hex ("FF0000") like the old hand-rolled style dict did.
    tc = raw.get("text_color", "")
    if isinstance(tc, str) and not tc.startswith("#") and len(tc) in (3, 6):
        raw["text_color"] = f"#{tc}"
    raw["position"] = _POSITION_ALIASES.get(raw.get("position"), raw.get("position"))
    return llama_manager._normalise_overlay(raw)

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


@app.get("/readyz")
async def readyz():
    """Readiness: DB writable, output dir writable, disk not full.

    Liveness stays on /health (trivial). Orchestrators and start-all.sh
    should gate traffic on this endpoint, not /health.
    """
    checks: dict[str, str] = {}
    try:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        probe = config.OUTPUT_DIR / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        checks["output_dir"] = "ok"
    except OSError as e:
        checks["output_dir"] = f"error: {e}"
    try:
        database.init_db()
        database.init_generations_table()
        database.get_used_count()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
    try:
        usage = shutil.disk_usage(config.OUTPUT_DIR)
        free_gb = usage.free / (1024 ** 3)
        checks["disk_free_gb"] = f"{free_gb:.1f}"
        if free_gb < 1.0:
            checks["disk"] = "low"
        else:
            checks["disk"] = "ok"
    except OSError as e:
        checks["disk"] = f"error: {e}"
    ok = all(v == "ok" or k == "disk_free_gb" for k, v in checks.items())
    return JSONResponse(
        {"ready": ok, "checks": checks}, status_code=200 if ok else 503
    )


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Allow Meta's crawler to fetch generated media through the tunnel."""
    return "User-agent: *\nAllow: /\n"


@app.get("/media/{filename:path}")
async def serve_media(filename: str):
    """Serve generated images to Instagram's media fetcher via ngrok.

    Serves files jailed under OUTPUT_DIR, including the single `_previews`
    subdirectory used by the overlay live-preview endpoint.
    """
    rel = filename.strip().lstrip("/")
    if not rel or rel.startswith("..") or "//" in rel.replace("\\", "/"):
        raise HTTPException(status_code=404, detail="Media not found")
    output_dir = config.OUTPUT_DIR.resolve()
    image_path = (output_dir / rel).resolve()
    try:
        image_path.relative_to(output_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail="Media not found")
    if image_path.parent != output_dir and image_path.parent != output_dir / "_previews":
        raise HTTPException(status_code=404, detail="Media not found")
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    media_type, _ = mimetypes.guess_type(image_path.name)
    if not media_type or not media_type.startswith("image/"):
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(
        image_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/comfy/status")
async def comfy_status(_: bool = Depends(require_token)):
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
async def get_config(_: bool = Depends(require_token)):
    account = instagram_uploader.get_account_info()
    return ConfigResponse(
        instagram_account=account["username"] if account else None,
        output_dir=str(config.OUTPUT_DIR),
        prompts_generated=database.get_used_count(),
        llama_running=llama_manager.is_running(),
        comfy_running=comfy_manager.is_running(),
    )


@app.post("/generate", status_code=202)
async def generate(
    req: GenerateRequest,
    request: Request,
    _: bool = Depends(require_token),
):
    """Enqueue a quote-image generation run; poll GET /jobs/{job_id}."""
    queue = _get_queue()
    job = Job(
        id=uuid.uuid4().hex[:12],
        request=req.model_dump(),
        created_at=_utcnow(),
        position=queue.qsize(),
    )
    _jobs[job.id] = job
    _prune_jobs()
    await queue.put(job.id)
    print(f"[codn] Enqueued job {job.id} (depth {queue.qsize()})")
    return {"job_id": job.id, "status": "queued", "position": job.position}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, _: bool = Depends(require_token)):
    """Poll a generation job's status, phase, and (when done) result."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_public(job)


@app.get("/jobs")
async def list_jobs(limit: int = 20, _: bool = Depends(require_token)):
    """List recent generation jobs, newest first."""
    limit = max(1, min(limit, 100))
    ordered = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
    return {"jobs": [_job_public(j) for j in ordered[:limit]], "total": len(_jobs)}


@app.delete("/jobs/{job_id}")
async def cancel_job(job_id: str, _: bool = Depends(require_token)):
    """Request cooperative cancel of a queued or running job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("done", "error", "cancelled"):
        return {"job_id": job.id, "status": job.status}
    job.cancel.set()
    if job.status == "queued":
        job.status = "cancelled"
        job.phase = "done"
        job.finished_at = _utcnow()
    print(f"[codn] Cancel requested for job {job.id}")
    return {"job_id": job.id, "status": job.status}


def _check_cancel(job: Job) -> None:
    if job.cancel.is_set():
        raise _Cancelled()


async def _run_job(job: Job) -> None:
    """Execute one generation run; the worker's single-flight VRAM owner."""
    global _llama_proc, _comfy_proc
    req = GenerateRequest(**job.request)
    job.status = "running"
    job.started_at = _utcnow()

    art_prompt = None
    overlay_style = None
    output_path = None

    try:
        # Phase 1: Pick the quote FIRST (local pool, then free web search),
        # so the art is directed by the actual words on the poster.
        # No LLM/GPU needed here.
        job.phase = "art-prompt"
        print(f"[codn:{job.id}] Phase 1: Picking quote...")
        picked = await asyncio.to_thread(
            llama_manager.pick_quote, req.category, req.theme
        )
        picked_quote = picked[0] if picked else None
        if picked_quote:
            print(f"[codn:{job.id}] Quote: {picked_quote['quote'][:80]}... — "
                  f"{picked_quote.get('character') or picked_quote.get('author', '')}")
        _check_cancel(job)

        # Phase 1b: Generate art prompt using text model (illustrates the quote)
        print(f"[codn:{job.id}] Phase 1b: Generating art prompt...")
        if not await asyncio.to_thread(llama_manager.is_running):
            print(f"[codn:{job.id}] Starting llama-server...")
            _llama_proc = await asyncio.to_thread(llama_manager.start_llama)
            if not await asyncio.to_thread(llama_manager.is_running):
                job.status = "error"
                job.error = "Failed to start llama-server"
                job.finished_at = _utcnow()
                return
        _check_cancel(job)

        quote_brief = ""
        subject_brief = ""
        if picked_quote:
            quote_brief = (f" The artwork must illustrate this exact quote that will "
                           f"appear on the poster: \"{picked_quote['quote']}\"")
            who = picked_quote.get("character") or picked_quote.get("author") or ""
            src = picked_quote.get("source") or ""
            if who:
                descs = llama_manager.subject_descriptors(who)
                if descs:
                    subject_brief = (
                        f" This post features {who} ({src}). Depict their world "
                        f"using: {', '.join(descs)}. Do NOT show the face — keep "
                        f"any figure faceless, turned away, or blurred beyond "
                        f"recognition; suggest identity through iconic details "
                        f"only, never a photorealistic portrait."
                    )
                elif src:
                    subject_brief = (
                        f" This post features {who} ({src}). Include imagery "
                        f"evocative of {src}."
                    )
        art_prompt = await asyncio.to_thread(
            llama_manager.generate_art_prompt,
            _llama_proc,
            f"Generate an art prompt for the category: {req.category or 'general'}. {f'Constraint: {req.theme}' if req.theme else ''}.{quote_brief}{subject_brief}",
        )

        # Check deduplication
        if await asyncio.to_thread(database.prompt_exists, art_prompt):
            print(f"[codn:{job.id}] Prompt already used, regenerating...")
            art_prompt = await asyncio.to_thread(
                llama_manager.generate_art_prompt,
                _llama_proc,
                f"Generate a DIFFERENT art prompt. Category: {req.category or 'general'}. Constraint: {req.theme or 'none'}.{quote_brief}{subject_brief}",
            )

        await asyncio.to_thread(database.save_prompt, art_prompt)
        print(f"[codn:{job.id}] Art prompt: {art_prompt[:120]}...")

        # Kill llama-server to free VRAM — MUST happen before ComfyUI starts
        if _llama_proc:
            print(f"[codn:{job.id}] Stopping llama-server (freeing VRAM)...")
            await asyncio.to_thread(llama_manager.stop_llama, _llama_proc, True)
            _llama_proc = None
            # Double-check it's actually dead
            for _ in range(10):
                if not await asyncio.to_thread(llama_manager.is_running):
                    break
                print(f"[codn:{job.id}] Waiting for llama-server to fully stop...")
                await asyncio.sleep(1)
            print(f"[codn:{job.id}] llama-server stopped, VRAM freed")
        _check_cancel(job)

        # Phase 2: Generate art using ComfyUI
        job.phase = "art-gen"
        print(f"[codn:{job.id}] Phase 2: Generating art...")
        if not await asyncio.to_thread(comfy_manager.is_running):
            print(f"[codn:{job.id}] Starting ComfyUI...")
            _comfy_proc = await asyncio.to_thread(comfy_manager.start_comfyui)
            if not await asyncio.to_thread(comfy_manager.is_running):
                job.status = "error"
                job.error = "Failed to start ComfyUI"
                job.finished_at = _utcnow()
                return

        image_path = await asyncio.to_thread(
            comfy_manager.generate_art, art_prompt, config.OUTPUT_DIR, job.cancel.is_set
        )

        # Kill ComfyUI to free VRAM
        if _comfy_proc:
            print(f"[codn:{job.id}] Stopping ComfyUI (freeing VRAM)...")
            await asyncio.to_thread(comfy_manager.stop_comfyui, _comfy_proc)
            _comfy_proc = None
            await asyncio.sleep(2)

        if job.cancel.is_set():
            raise _Cancelled()
        if not image_path:
            job.status = "error"
            job.error = "Art generation failed — check ComfyUI logs"
            job.finished_at = _utcnow()
            return

        print(f"[codn:{job.id}] Art generated: {image_path}")
        # Clean base art (pre-overlay). Edits always re-render from this so
        # text never stacks onto already-overlayed pixels (single composition).
        source_filename = os.path.basename(image_path) if image_path else None

        # Phase 3: Add text overlay using text model
        job.phase = "text-overlay"
        print(f"[codn:{job.id}] Phase 3: Generating text overlay...")
        if not await asyncio.to_thread(llama_manager.is_running):
            print(f"[codn:{job.id}] Starting llama-server for text overlay...")
            _llama_proc = await asyncio.to_thread(llama_manager.start_llama)
            if not await asyncio.to_thread(llama_manager.is_running):
                job.status = "error"
                job.error = "Failed to start llama-server for text overlay"
                job.finished_at = _utcnow()
                return
        _check_cancel(job)

        # Give the overlay model the actual creative context. A file path contains
        # no useful visual or thematic information, so passing it produced generic
        # quotes and arbitrary placement decisions.
        overlay_style = await asyncio.to_thread(
            llama_manager.generate_text_overlay,
            _llama_proc,
            image_path,
            art_prompt,
            req.category,
            req.theme,
            512,
            picked,
        )
        print(f"[codn:{job.id}] Overlay style: {json.dumps(overlay_style)}")
        if not picked:
            # Original line written by the model — record it so it isn't reused.
            try:
                await asyncio.to_thread(
                    database.save_quote, overlay_style.get("quote", "")
                )
            except Exception as e:
                print(f"[codn:{job.id}] Failed to record original quote: {e!r}")
        # Model-derived grouping tag (free-form UI input has no category).
        # Falls back to the API-supplied category, if any.
        group_tag = overlay_style.get("tag") or req.category

        # Apply overlay (Pillow — no GPU needed)
        output_name = f"{uuid.uuid4().hex[:8]}_quote.jpg"
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(config.OUTPUT_DIR / output_name)
        await asyncio.to_thread(
            text_overlay.add_text_overlay, image_path, output_path, overlay_style
        )
        print(f"[codn:{job.id}] Final image: {output_path}")

        # Generate the upload caption + hashtags while llama-server is still
        # running — it gets killed below to free VRAM.
        caption_data = None
        if req.upload:
            print(f"[codn:{job.id}] Generating upload caption + hashtags...")
            try:
                trending_terms = await asyncio.to_thread(trending.get_trending_terms)
                caption_data = await asyncio.to_thread(
                    llama_manager.generate_caption,
                    _llama_proc,
                    overlay_style.get("quote", ""),
                    overlay_style.get("author", ""),
                    art_prompt,
                    req.category,
                    req.theme,
                    400,
                    trending_terms,
                )
            except Exception as e:
                print(f"[codn:{job.id}] Caption generation failed, falling back: {e}")
                caption_data = None
        _check_cancel(job)

        # Kill llama-server (we're done with it)
        if _llama_proc:
            await asyncio.to_thread(llama_manager.stop_llama, _llama_proc, True)
            _llama_proc = None

        # Phase 4: Upload to Instagram
        if req.upload:
            job.phase = "upload"
            print(f"[codn:{job.id}] Phase 4: Uploading to Instagram...")
            caption = llama_manager.format_caption(
                overlay_style["quote"], overlay_style.get("author"), caption_data
            )
            if req.music:
                # Instagram fetches the public image while this call is in
                # progress. Keep the worker off the event loop's critical path.
                result = await asyncio.to_thread(
                    instagram_uploader.upload_reel, output_path, caption, req.music
                )
            else:
                result = await asyncio.to_thread(
                    instagram_uploader.upload_post, output_path, caption
                )

            # Record this generation in the database for the dashboard
            upload_status = "success" if result else "upload_failed"
            image_filename = os.path.basename(output_path) if output_path else None
            await asyncio.to_thread(
                database.save_generation,
                upload_status,
                group_tag,
                req.theme,
                overlay_style.get("quote"),
                overlay_style.get("author"),
                art_prompt,
                image_filename,
                None,
                overlay_style,
                source_filename,
            )

            job.status = "done"
            job.phase = "done"
            job.result = {
                "status": upload_status,
                "image": output_path,
                "prompt": art_prompt,
                "overlay": overlay_style,
                "upload_result": result,
                "image_url": f"/media/{os.path.basename(output_path)}" if output_path else "",
                "source_filename": source_filename,
            }
            job.finished_at = _utcnow()
            return

        # Record non-upload generation as success
        image_filename = os.path.basename(output_path) if output_path else None
        await asyncio.to_thread(
            database.save_generation,
            "success",
            group_tag,
            req.theme,
            overlay_style.get("quote"),
            overlay_style.get("author"),
            art_prompt,
            image_filename,
            None,
            overlay_style,
            source_filename,
        )

        job.status = "done"
        job.phase = "done"
        job.result = {
            "status": "generated",
            "image": output_path,
            "prompt": art_prompt,
            "overlay": overlay_style,
            "image_url": f"/media/{os.path.basename(output_path)}" if output_path else "",
            "source_filename": source_filename,
        }
        job.finished_at = _utcnow()

    except _Cancelled:
        print(f"[codn:{job.id}] Cancelled — cleaning up GPU procs...")
        if _llama_proc:
            await asyncio.to_thread(llama_manager.stop_llama, _llama_proc, False)
            _llama_proc = None
        if _comfy_proc:
            await asyncio.to_thread(comfy_manager.stop_comfyui, _comfy_proc)
            _comfy_proc = None
        job.status = "cancelled"
        job.phase = "done"
        job.finished_at = _utcnow()
    except Exception as e:
        # Record unexpected failures; full error is logged, never leaked.
        print(f"[codn:{job.id}] Job failed: {e!r}")
        image_filename = os.path.basename(output_path) if output_path else None
        try:
            await asyncio.to_thread(
                database.save_generation,
                "failed",
                req.category,
                req.theme,
                overlay_style.get("quote") if overlay_style else None,
                overlay_style.get("author") if overlay_style else None,
                art_prompt,
                image_filename,
                str(e)[:500],
            )
        except Exception as db_e:
            print(f"[codn:{job.id}] Failed to record failure: {db_e!r}")
        job.status = "error"
        job.phase = "done"
        job.error = "Internal server error"
        job.finished_at = _utcnow()


# ─── Post-edit Overlay Endpoints ──────────────────────────────

# Suffixes the overlay/upload endpoints accept. Pillow reads more, but the
# pipeline only ever produces these and Instagram only fetches images.
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

# Live-preview images are debounced per keystroke; without cleanup the
# _previews dir grows forever. Entries older than this are swept at startup
# and opportunistically on each preview request.
_PREVIEW_TTL_SECONDS = 24 * 3600


def _sweep_previews() -> int:
    """Delete _previews files older than the TTL. Returns count removed."""
    prev_dir = config.OUTPUT_DIR / "_previews"
    if not prev_dir.is_dir():
        return 0
    cutoff = time.time() - _PREVIEW_TTL_SECONDS
    removed = 0
    try:
        for child in prev_dir.iterdir():
            try:
                if child.is_file() and child.stat().st_mtime < cutoff:
                    child.unlink()
                    removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    if removed:
        print(f"[codn] Swept {removed} stale preview(s)")
    return removed


def _resolve_image(input_value: str) -> tuple[str, bool]:
    """Resolve a filename to an absolute path jailed under OUTPUT_DIR.

    Accepts a bare filename (looked up directly under OUTPUT_DIR) or a
    relative path such as `_previews/abc_preview.jpg`. Absolute paths and
    any path escaping OUTPUT_DIR are rejected with 400 — the overlay
    endpoints must never read/write arbitrary server paths.

    Returns (absolute_path, is_basename).
    """
    name = (input_value or "").strip()
    if not name or len(name) > 512:
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Reject Windows drive paths and backslash escapes outright.
    if "\\" in name or (len(name) > 1 and name[1] == ":"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    output_dir = config.OUTPUT_DIR.resolve()
    p = Path(name)
    candidate = p.resolve() if p.is_absolute() else (output_dir / name).resolve()
    try:
        candidate.relative_to(output_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if candidate.parent != output_dir and candidate.parent != output_dir / "_previews":
        raise HTTPException(status_code=400, detail="Invalid filename")
    if candidate.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return str(candidate), candidate.parent == output_dir


@app.post("/preview-overlay")
async def preview_overlay(
    req: PreviewOverlayRequest,
    request: Request,
    _: bool = Depends(require_token),
):
    """Render an edited text overlay onto the source image for live-preview.

    Renders from the clean base art (source_filename), never from the
    already-overlayed composition, so previews show exactly one text layer.
    """
    src_path, _target_path = _resolve_edit_source(req)

    style = _build_edit_style(req)

    output_name = f"{uuid.uuid4().hex[:8]}_preview.jpg"
    out_dir = config.OUTPUT_DIR / "_previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    _sweep_previews()  # previews are per-keystroke; keep the dir bounded
    output_path = str(out_dir / output_name)

    try:
        text_overlay.add_text_overlay(src_path, output_path, style)
    except Exception as e:
        print(f"[codn] Preview overlay failed: {e}")
        raise _server_error(request, "preview-overlay", e)

    return JSONResponse({
        "image_url": f"/media/_previews/{output_name}",
    })


@app.post("/apply-overlay")
async def apply_overlay(
    req: PostEditOverlayRequest,
    request: Request,
    _: bool = Depends(require_token),
):
    """Apply edited overlay parameters to the one composition, in place.

    Re-renders from the clean base art directly onto the composition file
    (no second file, no stacked text) and updates its history row.
    """
    src_path, target_path = _resolve_edit_source(req)

    style = _build_edit_style(req)

    try:
        text_overlay.add_text_overlay(src_path, target_path, style)
    except Exception as e:
        print(f"[codn] Apply overlay failed: {e}")
        raise _server_error(request, "apply-overlay", e)

    # Update the composition's history row in place (single composition).
    # Falls back to inserting when the file has no row (legacy/edge case).
    target_name = os.path.basename(target_path)
    try:
        updated = database.update_generation(
            target_name,
            quote_text=style.get("quote"),
            author=style.get("author"),
            overlay=style,
        )
        if not updated:
            database.save_generation(
                status="success",
                quote_text=style.get("quote"),
                author=style.get("author"),
                art_prompt=None,
                image_filename=target_name,
                overlay=style,
                source_filename=os.path.basename(src_path),
            )
    except Exception as e:
        print(f"[codn] Failed to record edit: {e!r}")

    row = None
    try:
        rows = database.get_generations(limit=200)
        row = next((r for r in rows if r.get("image_filename") == target_name), None)
    except Exception:
        pass
    bust = ""
    if row and row.get("updated_at"):
        bust = f"?v={''.join(c for c in str(row['updated_at']) if c.isdigit())}"

    return JSONResponse({
        "status": "applied",
        "image_url": f"/media/{target_name}{bust}",
    })


@app.post("/upload")
async def upload_manual(body: dict, _: bool = Depends(require_token)):
    """Upload an existing image to Instagram."""
    image_path = body.get("image_path", "")
    caption = body.get("caption", "")
    is_reel = body.get("is_reel", False)
    music = body.get("music", None)

    if not image_path:
        raise HTTPException(status_code=400, detail="image_path required")

    # Jail to OUTPUT_DIR so callers can't exfiltrate arbitrary server files
    # to Instagram. Absolute paths inside OUTPUT_DIR still work.
    image_path, _ = _resolve_image(image_path)
    if not os.path.isfile(image_path):
        raise HTTPException(status_code=404, detail="Image not found")

    if not isinstance(caption, str):
        raise HTTPException(status_code=400, detail="Invalid caption")
    caption = caption[:2200]  # Instagram's caption limit

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
async def get_quotes(category: str = None, _: bool = Depends(require_token)):
    """Return all used quotes with their metadata."""
    quotes = database.get_used_quotes(limit=200)
    if category:
        quotes = [q for q in quotes if q.get("category") == category]
    return {"quotes": quotes, "total": len(quotes)}


@app.get("/history")
async def get_history(status: str = None, category: str = None, _: bool = Depends(require_token)):
    """Return generation history for the dashboard."""
    generations = database.get_generations(status=status, category=category, limit=100)
    return {"generations": generations, "total": len(generations)}


class AddQuoteRequest(BaseModel):
    quote_text: str = Field(max_length=2000)
    author: str = Field(default="", max_length=200)
    category: Optional[str] = Field(default=None, max_length=100)


@app.post("/api/quotes")
async def add_quote(req: AddQuoteRequest, _: bool = Depends(require_token)):
    """Save a manually-entered quote to the library.

    Records it in the dedup list (so retrieve_quotes won't re-offer it)
    and as a text-only success row so it shows in /quotes and /history.
    """
    quote = " ".join(req.quote_text.split())
    if not quote:
        raise HTTPException(status_code=400, detail="quote_text required")
    author = " ".join(req.author.split()) or None
    database.save_quote(quote)
    database.save_generation(
        status="success",
        category=req.category,
        quote_text=quote,
        author=author,
    )
    return {"status": "saved"}


@app.get("/api/tunnel")
async def api_tunnel():
    """Public tunnel discovery for the dashboard status card.

    Returns only the public URL + engine name (no secrets). Public because
    the media URL it reports must be fetchable by Meta's crawler anyway.
    """
    base_url, engine = instagram_uploader._discover_base_url()
    return {"url": base_url, "engine": engine}


class TextModelRequest(BaseModel):
    path: str = Field(max_length=1024)


def _persist_env_var(key: str, value: str) -> None:
    """Update (or append) one KEY=value line in the project .env file."""
    env_path = config.BASE_DIR / ".env"
    lines: list[str] = []
    if env_path.is_file():
        lines = env_path.read_text().splitlines()
    updated = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}="):
            prefix = "export " if stripped.startswith("export ") else ""
            lines[i] = f"{prefix}{key}={value}"
            updated = True
    if not updated:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


@app.get("/api/text-model")
async def get_text_model(_: bool = Depends(require_token)):
    """Return the active text-generation model path (+ existence check)."""
    p = Path(config.LLAMA_MODEL_PATH)
    return {"path": str(config.LLAMA_MODEL_PATH), "exists": p.is_file()}


@app.post("/api/text-model")
async def set_text_model(req: TextModelRequest, _: bool = Depends(require_token)):
    """Switch the text-generation model. Takes effect on the next job.

    Validates the file exists and is a .gguf, applies live (llama-server
    spawns fresh per job, so no restart needed) and persists to .env.
    """
    raw = req.path.strip().strip('"').strip("'")
    if not raw:
        raise HTTPException(status_code=400, detail="path required")
    p = Path(raw).expanduser()
    if p.suffix.lower() != ".gguf" or not p.is_file():
        raise HTTPException(
            status_code=400, detail="Model file not found (expected an existing .gguf file)"
        )
    resolved = str(p.resolve())
    # llama_manager bound LLAMA_MODEL_PATH at import — update both refs.
    config.LLAMA_MODEL_PATH = Path(resolved)
    llama_manager.LLAMA_MODEL_PATH = Path(resolved)
    try:
        _persist_env_var("LLAMA_MODEL_PATH", resolved)
    except OSError as e:
        print(f"[codn] Applied model live but failed to persist to .env: {e}")
        return {"path": resolved, "persisted": False}
    print(f"[codn] Text model switched to {resolved}")
    return {"path": resolved, "persisted": True}


# ── Static Files (Dashboard) ───────────────────────────────────────────────

FRONTEND_DIR = config.BASE_DIR / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

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
