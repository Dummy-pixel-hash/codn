#!/bin/bash
# codn — Start all services

set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "=== codn Setup ==="

# Load .env
if [ -f .env ]; then
    while IFS='=' read -r key value; do
        # Strip UTF-8 BOM and leading whitespace so comment lines are safe to
        # source even when .env was saved by a Windows editor.
        key="${key//$'\ufeff'/}"
        key="${key#"${key%%[![:space:]]*}"}"
        # Trim trailing whitespace from the key and split on the first '='
        # only (read already puts the remainder, including any '=' in the
        # value such as tokens, into $value).
        key="${key%"${key##*[![:space:]]}"}"
        [[ "$key" == \#* ]] && continue
        [[ -z "$key" ]] && continue
        # Strip one pair of matching surrounding quotes so
        # KEY="va lue" and KEY='a=b' export without the quotes.
        if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' && ${#value} -ge 2 ]]; then
            value="${value:1:-1}"
        elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" && ${#value} -ge 2 ]]; then
            value="${value:1:-1}"
        fi
        export "$key=$value"
    done < .env
else
    echo "[!] .env not found, copying from .env.example"
    cp .env.example .env
fi

# Create output dir
mkdir -p output

# Return PIDs currently listening on TCP port 4000.
port_pids() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -tiTCP:4000 -sTCP:LISTEN 2>/dev/null || true
    elif command -v fuser >/dev/null 2>&1; then
        fuser -n tcp 4000 2>/dev/null | tr ' ' '\n' | sed '/^$/d' || true
    fi
}

# Clear a stale server before starting a new one.
existing_port_pids="$(port_pids)"
if [ -n "$existing_port_pids" ]; then
    echo "[*] Killing existing process(es) on port 4000: $existing_port_pids"
    while read -r pid; do
        case "$pid" in
            ''|*[!0-9]*) ;;
            *) kill "$pid" 2>/dev/null || true ;;
        esac
    done <<< "$existing_port_pids"
fi

# Check dependencies
echo ""
echo "--- Checking dependencies ---"
command -v llama-server 2>/dev/null || echo "[WARN] llama-server not in PATH — use: /mnt/data/Models/venv/bin/llama-server"
command -v python3 || echo "[WARN] python3 not found"
command -v ngrok || echo "[INFO] ngrok not found"

# Start services
echo ""
echo "--- Starting services ---"

# Tunnel order of preference:
#   1. ngrok — the free plan binds the account's permanent dev domain
#      (<name>.ngrok-free.app), which is stable across restarts, so Instagram's
#      media crawler gets a reliable HTTPS host. A custom static domain
#      (NGROK_STATIC_DOMAIN, paid plans) is used when configured.
#   2. Cloudflare Quick Tunnel as a fallback when ngrok is not installed.
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-$SCRIPT_DIR/cloudflared}"
CLOUDFLARED_LOG="${CLOUDFLARED_LOG:-$SCRIPT_DIR/cloudflared.log}"
NGROK_STATIC_DOMAIN="${NGROK_STATIC_DOMAIN:-}"
if command -v ngrok >/dev/null 2>&1; then
    if [ -n "$NGROK_AUTH_TOKEN" ]; then
        ngrok config add-authtoken "$NGROK_AUTH_TOKEN" >/dev/null 2>&1 || \
            echo "[WARN] Could not register NGROK_AUTH_TOKEN"
    fi
    NGROK_LOG="${NGROK_LOG:-ngrok.log}"
    if [ -n "$NGROK_STATIC_DOMAIN" ]; then
        echo "[*] Starting ngrok on static domain $NGROK_STATIC_DOMAIN..."
        ngrok http --url="$NGROK_STATIC_DOMAIN" 4000 >"$NGROK_LOG" 2>&1 &
    else
        echo "[*] Starting ngrok (dev domain)..."
        ngrok http 4000 >"$NGROK_LOG" 2>&1 &
    fi
    NGROK_PID=$!
    ngrok_url=""
    for _ in $(seq 1 10); do
        ngrok_url="$(curl -sS http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c 'import json,sys; data=json.load(sys.stdin); print(next((t["public_url"] for t in data.get("tunnels", []) if t.get("public_url", "").startswith("https://")), ""))' 2>/dev/null || true)"
        [ -n "$ngrok_url" ] && break
        sleep 1
    done
    if kill -0 "$NGROK_PID" 2>/dev/null; then
        echo "[*] ngrok started (logs: $NGROK_LOG)"
        if [ -n "$ngrok_url" ]; then
            echo "[*] Public URL: $ngrok_url"
            # Pass the live tunnel URL to the app. This prevents the uploader
            # from selecting an old Cloudflare Quick Tunnel URL left in
            # cloudflared.log from a previous run.
            if [ -z "${PUBLIC_BASE_URL:-}" ]; then
                export PUBLIC_BASE_URL="$ngrok_url"
            fi
        fi
    else
        echo "[WARN] ngrok exited; check $NGROK_LOG"
        unset NGROK_PID
    fi
elif [ -x "$CLOUDFLARED_BIN" ]; then
    echo "[*] Starting Cloudflare Quick Tunnel..."
    "$CLOUDFLARED_BIN" tunnel --protocol http2 --edge-ip-version 4 --url http://localhost:4000 >"$CLOUDFLARED_LOG" 2>&1 &
    CLOUDFLARED_PID=$!
    tunnel_url=""
    for _ in $(seq 1 15); do
        tunnel_url="$(CLOUDFLARED_LOG="$CLOUDFLARED_LOG" python3 -c 'import os, re, pathlib; p=pathlib.Path(os.environ["CLOUDFLARED_LOG"]); text=p.read_text(errors="replace") if p.exists() else ""; urls=re.findall(r"https://[a-z0-9-]+\\.trycloudflare\\.com", text); print(urls[-1] if urls else "")' 2>/dev/null || true)"
        [ -n "$tunnel_url" ] && break
        sleep 1
    done
    if kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
        echo "[*] Cloudflare tunnel started (logs: $CLOUDFLARED_LOG)"
        [ -n "$tunnel_url" ] && echo "[*] Public URL: $tunnel_url"
    else
        echo "[WARN] Cloudflare tunnel exited; check $CLOUDFLARED_LOG"
        unset CLOUDFLARED_PID
    fi
elif [ -n "$NGROK_AUTH_TOKEN" ]; then
    echo "[WARN] No tunnel client is available"
fi

# Start the app (it manages llama-server and ComfyUI lifecycle internally)
echo ""
echo "--- Starting codn app ---"
python3 main.py &
APP_PID=$!
echo "[*] codn app PID: $APP_PID"

echo ""
echo "=== Setup Complete ==="
echo "API endpoints:"
echo "  POST /generate    - Generate and upload a quote image"
echo "  GET  /health      - Health check"
echo "  GET  /config      - Current config"
echo ""
echo "To generate content:"
echo '  curl -X POST http://localhost:4000/generate \\'
echo '    -H "Content-Type: application/json" \\'
echo "    -d '{\"category\": \"philosophy\", \"upload\": true}' &"

# Cleanup on exit
cleanup() {
    # Do not run twice if Ctrl+C is followed by another termination signal.
    trap - SIGINT SIGTERM
    echo ""
    echo "[*] Cleaning up..."

    if [ -n "${NGROK_PID:-}" ] && kill -0 "$NGROK_PID" 2>/dev/null; then
        kill "$NGROK_PID" 2>/dev/null || true
    fi
    if [ -n "${CLOUDFLARED_PID:-}" ] && kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
        kill "$CLOUDFLARED_PID" 2>/dev/null || true
    fi

    # The app PID can be stale when the script is restarted, or uvicorn can
    # survive as a child. Remove every listener left on the configured port.
    # Find the actual listener on TCP port 4000 and stop that PID directly.
    remaining_pids="$(port_pids)"
    app_was_port_listener=false
    if [ -n "$remaining_pids" ]; then
        echo "[*] Stopping process(es) listening on port 4000: $remaining_pids"
        while read -r pid; do
            case "$pid" in
                ''|*[!0-9]*) ;;
                *)
                    [ "$pid" = "${APP_PID:-}" ] && app_was_port_listener=true
                    kill "$pid" 2>/dev/null || true
                    ;;
            esac
        done <<< "$remaining_pids"
    fi

    # If the app never successfully bound the port, clean up its PID too.
    # When it did bind port 4000, it was already signaled above.
    if [ "$app_was_port_listener" = false ] && [ -n "${APP_PID:-}" ] && kill -0 "$APP_PID" 2>/dev/null; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi

    echo "[*] Done."
    exit 0
}
trap cleanup SIGINT SIGTERM

# Wait for processes
wait
