"""
Upload generated content to Instagram via the Graph API.
Handles both feed posts and Reels (with optional music).
"""

import os
import time
import requests
from pathlib import Path
from urllib.parse import quote

from config import (
    INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_BUSINESS_ID,
    INSTAGRAM_ACCOUNT_ID,
    INSTAGRAM_GRAPH_URL,
    PUBLIC_BASE_URL,
    NGROK_STATIC_DOMAIN,
    CLOUDFLARED_LOG,
)


def _graph_url(path: str) -> str:
    return f"{INSTAGRAM_GRAPH_URL.rstrip('/')}/{path.lstrip('/')}"


def _public_image_url(image_path: str) -> str | None:
    """Build a public URL Instagram can fetch for a generated image."""
    path = Path(image_path)
    if not path.is_file():
        print(f"[ig] Image not found: {image_path}")
        return None

    base_url = PUBLIC_BASE_URL.strip().rstrip("/") if PUBLIC_BASE_URL else ""
    if not base_url and NGROK_STATIC_DOMAIN:
        # Reserved static domain — stable across restarts, unlike rotating
        # tunnel URLs, so Meta never sees a broken or blocked media host.
        base_url = f"https://{NGROK_STATIC_DOMAIN.strip().strip('/')}"
    if not base_url:
        # start-all.sh launches ngrok on port 4040. Prefer its live HTTPS
        # tunnel over any URL cached in cloudflared.log from an older run.
        try:
            tunnels = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2).json().get("tunnels", [])
            base_url = next(
                (t["public_url"] for t in tunnels if t.get("public_url", "").startswith("https://")),
                "",
            ).rstrip("/")
        except (requests.RequestException, ValueError, KeyError):
            pass
    if not base_url:
        # Cloudflare Quick Tunnel has no local inspector API; read the URL
        # printed by cloudflared instead. This is deliberately last so a
        # stale log entry cannot override a running ngrok tunnel.
        try:
            import re
            log_text = CLOUDFLARED_LOG.read_text(errors="replace")
            matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", log_text)
            if matches:
                base_url = matches[-1]
        except OSError:
            pass

    if not base_url:
        print("[ig] No public image URL available. Set PUBLIC_BASE_URL or run ngrok on port 4040.")
        return None
    # ngrok's free browser-warning page is HTML, not the JPEG Meta expects.
    # Cloudflare Quick Tunnels do not need this parameter.
    suffix = "?ngrok-skip-browser-warning=true" if "ngrok" in base_url else ""
    image_url = f"{base_url}/media/{quote(path.name)}{suffix}"
    print(f"[ig] Public image URL: {image_url}")
    return image_url


def _check_public_image(image_url: str) -> bool:
    """Verify the exact URL is a direct JPEG before asking Instagram to fetch it."""
    try:
        resp = requests.get(
            image_url,
            headers={"User-Agent": "facebookexternalhit/1.1"},
            stream=True,
            timeout=15,
        )
        content_type = resp.headers.get("Content-Type", "")
        print(f"[ig] Media preflight: {resp.status_code} {content_type}")
        valid = resp.status_code == 200 and content_type.lower().split(";", 1)[0] in {
            "image/jpeg",
            "image/jpg",
        }
        resp.close()
        if not valid:
            print("[ig] Public URL did not return a direct JPEG")
        return valid
    except requests.RequestException as exc:
        # Some machines cannot hairpin through their own ngrok public URL.
        # That does not mean Instagram cannot fetch it; Meta has already
        # reached this app externally in the successful /media request logs.
        print(f"[ig] Media preflight warning (continuing): {exc}")
        return True


def _print_api_error(prefix: str, resp: requests.Response) -> None:
    try:
        error = resp.json().get("error", {})
        code = error.get("code")
        subcode = error.get("error_subcode")
        message = error.get("message", resp.text)
        if code == 190:
            message += " (access token is invalid, expired, or incompatible with this Instagram account/API)"
        if code == 9004 or subcode == 2207052:
            message += " (Meta could not download the public image URL; use a stable HTTPS media host, not a local-only URL or blocked tunnel)"
        detail = f" code={code}" if code is not None else ""
        detail += f" subcode={subcode}" if subcode is not None else ""
        user_message = error.get("error_user_msg")
        if user_message:
            message += f" — {user_message}"
        print(f"[ig] {prefix}: {resp.status_code}{detail} {message}")
    except ValueError:
        print(f"[ig] {prefix}: {resp.status_code} {resp.text[:500]}")


def _wait_for_container(container_id: str, timeout: int = 120) -> bool:
    """Instagram processes the remote image asynchronously before publishing."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(
            _graph_url(container_id),
            params={"fields": "status_code,status", "access_token": INSTAGRAM_ACCESS_TOKEN},
            timeout=15,
        )
        if resp.status_code != 200:
            _print_api_error("Container status failed", resp)
            return False
        status = resp.json().get("status_code")
        if status == "FINISHED":
            return True
        if status in {"ERROR", "EXPIRED"}:
            print(f"[ig] Container processing failed: {resp.text[:500]}")
            return False
        time.sleep(3)
    print("[ig] Container processing timed out")
    return False


def upload_post(image_path: str, caption: str = "") -> dict | None:
    """
    Upload an image as a feed post.

    Args:
        image_path: Path to the image file
        caption: Caption text (may include hashtags)

    Returns:
        API response dict or None on failure
    """
    if not INSTAGRAM_ACCESS_TOKEN or not INSTAGRAM_BUSINESS_ID:
        print("[ig] Missing Instagram credentials")
        return None

    image_url = _public_image_url(image_path)
    if not image_url or not _check_public_image(image_url):
        return None

    # Instagram fetches image_url from the public internet; it does not accept
    # the local multipart file upload used by the previous implementation.
    url = _graph_url(f"{INSTAGRAM_BUSINESS_ID}/media")
    # Meta documents this request as multipart/form-data. Sending image_url
    # as a multipart field avoids it being interpreted as missing/empty by
    # some Graph API versions.
    form = {
        "image_url": (None, image_url),
        "access_token": (None, INSTAGRAM_ACCESS_TOKEN),
    }
    if caption:
        form["caption"] = (None, caption)
    resp = requests.post(url, files=form, timeout=30)

    if resp.status_code != 200:
        _print_api_error("Create container failed", resp)
        return None

    result = resp.json()
    container_id = result.get("id")
    print(f"[ig] Container created: {container_id}")

    if not container_id or not _wait_for_container(container_id):
        return None

    # Step 2: Publish
    publish_url = _graph_url(f"{INSTAGRAM_BUSINESS_ID}/media_publish")
    publish_data = {"creation_id": container_id, "access_token": INSTAGRAM_ACCESS_TOKEN}

    resp = requests.post(publish_url, data=publish_data, timeout=30)

    if resp.status_code != 200:
        _print_api_error("Publish failed", resp)
        return None

    publish_result = resp.json()
    print(f"[ig] Published successfully: {publish_result.get('id', 'unknown')}")
    return publish_result


def upload_reel(image_path: str, caption: str = "", music_url: str = "") -> dict | None:
    """
    Upload an image as a Reel (static image Reel).

    Args:
        image_path: Path to the image file
        caption: Caption text
        music_url: Optional music/audio URL (may not be supported for all accounts)

    Returns:
        API response dict or None on failure
    """
    if not INSTAGRAM_ACCESS_TOKEN or not INSTAGRAM_BUSINESS_ID:
        print("[ig] Missing Instagram credentials")
        return None

    # Step 1: Create a container with video-like media (image as reel)
    url = f"https://graph.instagram.com/{INSTAGRAM_BUSINESS_ID}/media"
    files = {"image": (os.path.basename(image_path), open(image_path, "rb"), "image/jpeg")}
    data = {
        "caption": caption,
        "is_reel": "true",
    }
    if music_url:
        data["music_asset_id"] = music_url

    resp = requests.post(url, headers={"Authorization": f"Bearer {INSTAGRAM_ACCESS_TOKEN}"},
                         files=files, data=data)

    if resp.status_code != 200:
        print(f"[ig] Reel container failed: {resp.status_code} {resp.text}")
        # Retry without music
        if music_url:
            return upload_reel(image_path, caption, music_url="")
        return None

    result = resp.json()
    container_id = result.get("id")
    print(f"[ig] Reel container created: {container_id}")

    # Step 2: Publish
    publish_url = f"https://graph.instagram.com/{INSTAGRAM_BUSINESS_ID}/media_publish"
    publish_data = {"creation_id": container_id}

    resp = requests.post(publish_url, headers={"Authorization": f"Bearer {INSTAGRAM_ACCESS_TOKEN}"},
                         data=publish_data)

    if resp.status_code != 200:
        print(f"[ig] Reel publish failed: {resp.status_code} {resp.text}")
        return None

    publish_result = resp.json()
    print(f"[ig] Reel published successfully: {publish_result.get('id', 'unknown')}")
    return publish_result


def get_account_info() -> dict | None:
    """Get current Instagram account info."""
    if not INSTAGRAM_ACCESS_TOKEN or not INSTAGRAM_BUSINESS_ID:
        return None

    url = _graph_url(INSTAGRAM_BUSINESS_ID)
    resp = requests.get(url, params={"fields": "username,account_type,name",
                                      "access_token": INSTAGRAM_ACCESS_TOKEN})

    if resp.status_code == 200:
        return resp.json()
    return None
