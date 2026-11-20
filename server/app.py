"""
ShrineProjector Main Daemon Server
FastAPI + WebSockets + Native Hardware Video Streaming + Subnet Firewall + Stream Power Gate
"""

import os
import sys
import json
import asyncio
import logging
import ipaddress
from pathlib import Path
from typing import Optional, Set
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, status, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from server.auth import AuthGate
from server.capture import ScreenCaptureEngine
from server.input import InputGuard
from server.audio import audio_engine
from server.remote import RemoteTargets
from server.tsidentity import TailscaleIdentityMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("shrine.server")

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "shrine-config.json"
WEB_DIR = BASE_DIR / "web"

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError(
        f"Config file not found at {CONFIG_PATH}. "
        "Create one with: python3 scripts/init-config"
    )

CONFIG = load_config()
auth_gate = AuthGate(CONFIG)

# Token lemah / placeholder = daemon menolak jalan (jangan sampai token contoh dipakai di produksi).
_weak = auth_gate.weak_tokens()
if _weak:
    raise SystemExit(
        f"Refusing to start: {len(_weak)} auth token(s) are placeholders or too short "
        f"(min {AuthGate.MIN_TOKEN_LEN} chars). Run: python3 scripts/init-config --rotate"
    )
capture_engine = ScreenCaptureEngine(CONFIG)
remote_targets = RemoteTargets(CONFIG, BASE_DIR / ".state")
input_guard = InputGuard(
    max_rate_per_sec=CONFIG.get("security", {}).get("rate_limit_events_per_sec", 120),
    screen_size=(capture_engine.width, capture_engine.height),
    max_clipboard_chars=CONFIG.get("security", {}).get("max_clipboard_chars", 65536)
)

ALLOWED_SUBNETS = [
    ipaddress.ip_network(net) for net in CONFIG.get("security", {}).get(
        "allowed_subnets",
        ["127.0.0.0/8", "100.64.0.0/10"]
    )
]

def is_ip_allowed(ip_str: Optional[str]) -> bool:
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in ALLOWED_SUBNETS)
    except ValueError:
        return False

class SubnetFirewallMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_host = request.client.host if request.client else None
        if client_host and not is_ip_allowed(client_host):
            logger.warning(f"SECURITY ALERT: Blocked untrusted connection attempt from {client_host}")
            return Response("Forbidden: Untrusted Network Interface", status_code=403)
        return await call_next(request)

active_websockets: Set[WebSocket] = set()

async def broadcast_stream_state():
    """Broadcast stream active/standby state to all connected viewers"""
    state_msg = {
        "type": "stream_state",
        "active": capture_engine.is_active,
        "profile": capture_engine.profile
    }
    for ws in list(active_websockets):
        try:
            await ws.send_json(state_msg)
        except Exception:
            pass

CAPTURE_CFG = CONFIG.get("capture", {})
# Matiin capture otomatis kalau gak ada viewer selama N detik (0 = nonaktif)
IDLE_TIMEOUT_SECONDS = int(CAPTURE_CFG.get("idle_timeout_seconds", 300))
IDLE_CHECK_INTERVAL = 15


# Kalau user matiin capture manual (tombol Deactivate), capture JANGAN nyala sendiri
# waktu viewer buka halaman. Baru nyala lagi kalau user pencet Activate.
# Disimpan ke file biar tetap kepegang walau daemon restart / Mac reboot.
MANUAL_OFF_FLAG = BASE_DIR / ".stream-manual-off"


def is_manual_off() -> bool:
    return MANUAL_OFF_FLAG.exists()


async def manual_activate():
    MANUAL_OFF_FLAG.unlink(missing_ok=True)
    await capture_engine.activate()


async def manual_deactivate():
    MANUAL_OFF_FLAG.touch()
    await capture_engine.deactivate()


async def auto_activate_for_viewer() -> bool:
    """Nyalain capture buat viewer baru, kecuali lagi dimatiin manual"""
    if is_manual_off():
        return False
    return await capture_engine.activate()


def viewer_count() -> int:
    """Viewer = koneksi WebSocket + subscriber MJPEG/HTTP yang lagi narik frame"""
    return len(active_websockets) + capture_engine.subscriber_count


async def idle_watchdog():
    """Deactivate capture setelah IDLE_TIMEOUT_SECONDS tanpa viewer sama sekali"""
    if IDLE_TIMEOUT_SECONDS <= 0:
        return
    loop = asyncio.get_running_loop()
    idle_since: Optional[float] = None
    while True:
        await asyncio.sleep(IDLE_CHECK_INTERVAL)
        try:
            if not capture_engine.is_active or viewer_count() > 0:
                idle_since = None
                continue
            now = loop.time()
            if idle_since is None:
                idle_since = now
            elif now - idle_since >= IDLE_TIMEOUT_SECONDS:
                logger.info(f"Gak ada viewer {IDLE_TIMEOUT_SECONDS} detik, capture dimatiin otomatis")
                await capture_engine.deactivate()
                await broadcast_stream_state()
                idle_since = None
        except Exception as e:
            logger.warning(f"Idle watchdog error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    server_cfg = CONFIG.get("server", {})
    bind_host = server_cfg.get("bind_host", "0.0.0.0")
    logger.info(f"Initializing ShrineProjector daemon on {bind_host}:{server_cfg.get('port', 8765)}...")
    logger.info(f"Subnet Firewall active: Whitelisted networks {[str(n) for n in ALLOWED_SUBNETS]}")
    # Default: standby saat boot. Capture nyala otomatis waktu ada viewer connect.
    if CAPTURE_CFG.get("start_on_boot", False) and not is_manual_off():
        await capture_engine.start()
    else:
        logger.info("Capture standby saat boot (nyala otomatis saat viewer connect)")
    watchdog = asyncio.create_task(idle_watchdog())
    yield
    logger.info("Shutting down ShrineProjector daemon...")
    watchdog.cancel()
    await capture_engine.stop()

_control_token = next((t["token"] for t in CONFIG.get("security", {}).get("auth_tokens", [])
                       if t.get("can_control")), None)

app = FastAPI(title="ShrineProjector", lifespan=lifespan)
app.add_middleware(SubnetFirewallMiddleware)
app.add_middleware(TailscaleIdentityMiddleware,
                   trusted_logins=CONFIG.get("security", {}).get("trusted_tailscale_logins", []),
                   token=_control_token)

def _html(name: str) -> HTMLResponse:
    page = WEB_DIR / name
    if not page.exists():
        raise HTTPException(status_code=404, detail="Web client not found")
    return HTMLResponse(content=page.read_text(encoding="utf-8"))

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_index():
    # Ada remote target = halaman pilih device dulu; gak ada = langsung stream Mac ini.
    return _html("picker.html" if remote_targets.targets else "index.html")

@app.api_route("/mac", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_host_stream():
    return _html("index.html")

@app.get("/api/status")
async def get_status(token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    ax_trusted = False
    if sys.platform == "darwin":
        try:
            import ctypes
            app_services = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
            app_services.AXIsProcessTrusted.argtypes = []
            app_services.AXIsProcessTrusted.restype = ctypes.c_bool
            ax_trusted = bool(app_services.AXIsProcessTrusted())
        except Exception as e:
            logger.warning(f"AX check failed: {e}")
    else:
        ax_trusted = True

    return {
        "status": "online",
        "service": "ShrineProjector",
        "host_label": CONFIG.get("server", {}).get("host_label", "This Mac" if sys.platform == "darwin" else ("This PC" if sys.platform == "win32" else "This Linux Host")),
        "stream_active": capture_engine.is_active,
        "manual_off": is_manual_off(),
        "profile": capture_engine.profile,
        "profiles": list(capture_engine.profiles.keys()),
        "role": token_info.get("role", "viewer"),
        "can_control": auth_gate.can_control(token_info),
        "accessibility_trusted": ax_trusted,
        "connected_viewers": len(active_websockets),
        "display": {
            "width": capture_engine.width,
            "height": capture_engine.height,
            "fps": capture_engine.fps
        }
    }

@app.post("/api/stream/activate")
async def activate_stream(token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    await manual_activate()
    await broadcast_stream_state()
    return {"status": "ok", "stream_active": True, "message": "Capture engine activated"}

@app.post("/api/stream/deactivate")
async def deactivate_stream(token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    await manual_deactivate()
    await broadcast_stream_state()
    return {"status": "ok", "stream_active": False, "message": "Capture engine deactivated (0% RAM/CPU)"}

@app.post("/api/stream/profile")
async def set_stream_profile(mode: str = Query(...), token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if mode not in capture_engine.profiles:
        raise HTTPException(status_code=400, detail=f"Profil gak dikenal: {mode}")

    await capture_engine.set_profile(mode)
    await broadcast_stream_state()
    return {"status": "ok", "profile": capture_engine.profile, "stream_active": capture_engine.is_active}

@app.post("/api/stream/toggle")
async def toggle_stream(token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if capture_engine.is_active:
        await manual_deactivate()
        active = False
    else:
        await manual_activate()
        active = True

    await broadcast_stream_state()
    return {"status": "ok", "stream_active": active}

# ── Remote targets (sesi remote ke mesin lain; admin/control token saja) ──
def _require_control(token: Optional[str]):
    token_info = auth_gate.verify_token(token)
    if not token_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not auth_gate.can_control(token_info):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Control capability required")

def _require_target(name: str):
    if name not in remote_targets.targets:
        raise HTTPException(status_code=404, detail="Unknown remote target")

@app.get("/api/remote")
async def remote_list(token: Optional[str] = Query(None)):
    _require_control(token)
    return {"targets": await asyncio.to_thread(remote_targets.list)}

@app.post("/api/remote/{name}/start")
async def remote_start(name: str, token: Optional[str] = Query(None)):
    _require_control(token)
    _require_target(name)
    return await asyncio.to_thread(remote_targets.start, name)

@app.post("/api/remote/{name}/stop")
async def remote_stop(name: str, token: Optional[str] = Query(None)):
    _require_control(token)
    _require_target(name)
    return await asyncio.to_thread(remote_targets.stop, name)

@app.get("/stream.mjpg")
async def mjpg_stream(token: Optional[str] = Query(None)):
    """Simple HTTP multipart MJPEG stream for lightweight browsers / preview"""
    token_info = auth_gate.verify_token(token)
    if not token_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing authentication token")

    await auto_activate_for_viewer()

    async def frame_generator():
        async for frame in capture_engine.subscribe():
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                frame + b"\r\n"
            )

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.get("/audio.mp3")
async def audio_stream(token: Optional[str] = Query(None)):
    """Live system audio broadcast from Mac Mini via BlackHole (128kbps stereo MP3)"""
    token_info = auth_gate.verify_token(token)
    if not token_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing authentication token")

    async def stream_generator():
        async for chunk in audio_engine.subscribe():
            yield chunk

    return StreamingResponse(
        stream_generator(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Accept-Ranges": "none",
            "Connection": "keep-alive"
        }
    )

@app.post("/api/input/move")
async def api_input_move(request: Request, token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info or not auth_gate.can_control(token_info):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Control not permitted")
    data = await request.json()
    input_guard.handle_mouse_move(data.get("x", 0), data.get("y", 0), True)
    return {"status": "ok"}

@app.post("/api/input/click")
async def api_input_click(request: Request, token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info or not auth_gate.can_control(token_info):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Control not permitted")
    data = await request.json()
    input_guard.handle_mouse_click(
        data.get("x", 0),
        data.get("y", 0),
        data.get("button", "left"),
        data.get("action", "click"),
        True
    )
    return {"status": "ok"}

@app.post("/api/input/scroll")
async def api_input_scroll(request: Request, token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info or not auth_gate.can_control(token_info):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Control not permitted")
    data = await request.json()
    input_guard.handle_mouse_scroll(data.get("dy", 0), True)
    return {"status": "ok"}

@app.post("/api/input/key")
async def api_input_key(request: Request, token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info or not auth_gate.can_control(token_info):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Control not permitted")
    data = await request.json()
    input_guard.handle_key_event(
        data.get("key", ""),
        data.get("action", "down"),
        True,
        modifiers=data.get("modifiers"),
        exact_mods=bool(data.get("exact_mods")),
    )
    return {"status": "ok"}

@app.post("/api/input/paste")
async def api_input_paste(request: Request, token: Optional[str] = Query(None)):
    token_info = auth_gate.verify_token(token)
    if not token_info or not auth_gate.can_control(token_info):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Control not permitted")
    data = await request.json()
    input_guard.handle_paste_text(data.get("text", ""), True)
    return {"status": "ok"}

@app.websocket("/ws/audio")
async def websocket_audio(websocket: WebSocket, token: Optional[str] = Query(None)):
    """Ultra-low latency real-time system audio streaming (44.1kHz stereo s16le PCM)"""
    client_ip = websocket.client.host if websocket.client else None
    if not is_ip_allowed(client_ip):
        logger.warning(f"Rejected Audio WebSocket from untrusted IP: {client_ip}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    token_info = auth_gate.verify_token(token)
    if not token_info:
        logger.warning(f"Rejected unauthenticated Audio WebSocket from {websocket.client}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    logger.info(f"[AUDIO WS] Client connected: {websocket.client}")
    try:
        async for chunk in audio_engine.subscribe():
            await websocket.send_bytes(chunk)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[AUDIO WS] Connection ended: {e}")
    finally:
        logger.info(f"[AUDIO WS] Client disconnected: {websocket.client}")

@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket, token: Optional[str] = Query(None)):
    client_ip = websocket.client.host if websocket.client else None
    if not is_ip_allowed(client_ip):
        logger.warning(f"Rejected WebSocket from untrusted IP: {client_ip}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    token_info = auth_gate.verify_token(token)
    if not token_info:
        logger.warning(f"Rejected unauthenticated WebSocket attempt from {websocket.client}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    active_websockets.add(websocket)
    # Viewer datang → capture nyala otomatis
    if await auto_activate_for_viewer():
        await broadcast_stream_state()
    can_control = auth_gate.can_control(token_info)
    mode = "INTERACTIVE" if can_control else "PROJECTOR"

    logger.info(f"Client connected: {websocket.client} | Role: {token_info.get('role')} | Mode: {mode}")

    ws_lock = asyncio.Lock()

    async def safe_send_bytes(data: bytes):
        async with ws_lock:
            await websocket.send_bytes(data)

    async def safe_send_json(data: dict):
        async with ws_lock:
            await websocket.send_json(data)

    # Send initial welcome metadata with stream_active flag
    await safe_send_json({
        "type": "init",
        "mode": mode,
        "can_control": can_control,
        "stream_active": capture_engine.is_active,
        "profile": capture_engine.profile,
        "width": capture_engine.width,
        "height": capture_engine.height,
        "fps": capture_engine.fps
    })

    # Task 1: Frame Sender
    async def send_frames():
        try:
            while websocket in active_websockets:
                if not capture_engine.is_active:
                    await asyncio.sleep(0.3)
                    continue
                try:
                    async for frame in capture_engine.subscribe():
                        if websocket in active_websockets and capture_engine.is_active:
                            await safe_send_bytes(frame)
                        else:
                            break
                except Exception as frame_err:
                    logger.debug(f"Frame send error: {frame_err}")
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Sender task exited: {e}")

    # Task 2: Command/Input Receiver
    async def receive_commands():
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    data = json.loads(msg)
                except Exception:
                    continue

                msg_type = data.get("type")
                if msg_type == "mouse_move":
                    input_guard.handle_mouse_move(data.get("x", 0), data.get("y", 0), can_control)
                elif msg_type == "mouse_click":
                    input_guard.handle_mouse_click(
                        data.get("x", 0),
                        data.get("y", 0),
                        data.get("button", "left"),
                        data.get("action", "click"),
                        can_control
                    )
                elif msg_type == "mouse_scroll":
                    input_guard.handle_mouse_scroll(data.get("dy", 0), can_control)
                elif msg_type == "key_event":
                    input_guard.handle_key_event(
                        data.get("key", ""),
                        data.get("action", "down"),
                        can_control,
                        modifiers=data.get("modifiers"),
                        exact_mods=bool(data.get("exact_mods")),
                    )
                elif msg_type == "key_release_all":
                    if can_control:
                        input_guard.release_all_modifiers()
                elif msg_type == "clipboard_paste":
                    input_guard.handle_paste_text(data.get("text", ""), can_control)
                elif msg_type in ("stream_toggle", "stream_power"):
                    action = data.get("action")
                    if action == "activate":
                        await manual_activate()
                    elif action == "deactivate":
                        await manual_deactivate()
                    else:
                        if capture_engine.is_active:
                            await manual_deactivate()
                        else:
                            await manual_activate()
                    await broadcast_stream_state()
                elif msg_type == "ping":
                    await safe_send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning(f"Receive loop ended: {e}")

    sender_task = asyncio.create_task(send_frames())
    receiver_task = asyncio.create_task(receive_commands())

    try:
        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    finally:
        active_websockets.discard(websocket)
        # Klien putus pas Cmd/Ctrl masih ketahan -> jangan biarin modifier nyangkut di Mac
        if can_control:
            input_guard.release_all_modifiers()
        logger.info(f"Client disconnected: {websocket.client} (Active viewers: {len(active_websockets)})")
