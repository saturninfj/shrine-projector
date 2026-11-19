#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=================================================="
echo "          SHRINEPROJECTOR DAEMON BOOT             "
echo "=================================================="

# Check requirements
if ! command -v ffmpeg &> /dev/null; then
    echo "[-] Error: ffmpeg is required but not installed."
    exit 1
fi

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -f "$PYTHON_BIN" ]; then
    if [ -f "/opt/homebrew/bin/python3" ]; then
        PYTHON_BIN="/opt/homebrew/bin/python3"
    else
        PYTHON_BIN="python3"
    fi
fi

CONFIG_FILE="$PROJECT_DIR/config/shrine-config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "[-] Error: $CONFIG_FILE not found. Run: python3 scripts/init-config"
    exit 1
fi
BIND_HOST=$("$PYTHON_BIN" -c "import json; print(json.load(open('$CONFIG_FILE'))['server']['bind_host'])")
PORT=$("$PYTHON_BIN" -c "import json; print(json.load(open('$CONFIG_FILE'))['server']['port'])")

# Find Tailscale or LAN IP if available
export PATH="/sbin:/usr/sbin:$PATH"
TAILSCALE_IP=$(ifconfig 2>/dev/null | grep -A 1 "100\." | grep "inet " | awk '{print $2}' || true)
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)

echo "[+] Project Root: $PROJECT_DIR"
echo "[+] Bind Host:    $BIND_HOST"
echo "[+] Port:         $PORT"
if [ -n "$TAILSCALE_IP" ]; then
    echo "[+] Tailscale:    http://$TAILSCALE_IP:$PORT/  (token: see config, or run scripts/projector url)"
fi
if [ -n "$LAN_IP" ]; then
    echo "[+] LAN:          http://$LAN_IP:$PORT/  (token: see config, or run scripts/projector url)"
fi
echo "[+] Localhost:    http://127.0.0.1:$PORT/  (token: see config, or run scripts/projector url)"
echo "--------------------------------------------------"
echo "[*] Launching uvicorn ASGI server on $BIND_HOST:$PORT..."

exec "$PYTHON_BIN" -c "
import uvicorn
uvicorn.run(
    'server.app:app',
    host='$BIND_HOST',
    port=$PORT,
    log_level='info',
    ws_ping_interval=None,
    ws_ping_timeout=None
)
"
