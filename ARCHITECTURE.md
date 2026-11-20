# ShrineProjector architecture

Privacy-first, capability-gated remote desktop and screen projection architecture for macOS, Windows, and Linux hosts.

This describes the code as it is, not a roadmap. See the README for the disclaimer about RustDesk (design inspiration only, no shared code).

## Components

```
 Browser client (web/index.html)                      Host daemon (server/)
 ─────────────────────────────                        ─────────────────────────────────────────
 canvas  ◄── JPEG frames ───────── /ws/stream ──────  capture.py   macOS: ffmpeg avfoundation → MJPEG
                                                                   Linux: wf-recorder (Wayland) / ffmpeg
                                                                   Windows: ffmpeg gdigrab → MJPEG
 WebAudio◄── PCM chunks ────────── /ws/audio  ──────  audio.py     macOS: BlackHole via PortAudio
                                                                   Linux: PulseAudio / PipeWire monitor
                                                                   Windows: WASAPI / Stereo Mix loopback
 pointer/keyboard ── JSON events ─ /ws/stream ──────► input.py     InputGuard →
                                                                   macOS: CoreGraphics (ctypes)
                                                                   Linux: /dev/uinput (python-evdev)
                                                                   Windows: Win32 SendInput (ctypes)
 HTTP fallback ───── /stream.mjpg, /api/input/* ────► app.py       FastAPI + uvicorn
                                                      auth.py      token table, constant-time compare
```

Everything is in one Python process. Screen capture runs as a child process (`ffmpeg` or `wf-recorder`) only while capture is active.

## Request path and gates

Every HTTP request and WebSocket goes through, in order:

1. **Source IP allow-list** (`security.allowed_subnets`, middleware in `app.py`). Requests from other addresses get HTTP 403 or WebSocket close 1008. Behind `tailscale serve` the daemon sees `127.0.0.1`; Tailscale's ACLs decide who reaches the proxy.
2. **Token check** (`AuthGate.verify_token`). The token comes from the `token` query parameter and is compared against every configured token with `hmac.compare_digest`. Missing or wrong token: HTTP 401 or WebSocket close 1008.
3. **Capability check** for input. `can_control` is true only if `security.allow_interactive_mode` is true and the token has `can_control: true`. Input HTTP endpoints return 403 otherwise; on the WebSocket, input messages from a view-only session are ignored.

At startup the daemon refuses to run if any configured token is shorter than 32 characters or still looks like a placeholder (`AuthGate.weak_tokens`).

## Input guard

`InputGuard` (server/input.py) sits between the network and the host input system (CoreGraphics on macOS, `/dev/uinput` on Linux, Win32 `SendInput` on Windows):

- rate limit (sliding one-second window, `rate_limit_events_per_sec`)
- coordinates clamped to the detected screen size
- keyboard: modifiers are applied per event from the client's flags, and every held key is released when the client blurs, hides or disconnects, so a dropped connection cannot leave Cmd/Ctrl stuck down
- paste: text is reduced to plain UTF-8, null bytes removed, length capped (`max_clipboard_chars`), written with clipboard commands (`pbcopy` on macOS, `wl-copy`/`xclip` on Linux, PowerShell/SendInput on Windows), then Cmd+V / Ctrl+V is sent

## Capture lifecycle

- Capture starts when the first viewer connects (unless the user pressed Deactivate, which is remembered in `.stream-manual-off` until Activate).
- An idle watchdog stops capture after `capture.idle_timeout_seconds` (default 300) with no viewers.
- Profiles: `full` (native resolution, configured fps) and `kafe` (1280 px wide, 12 fps, lower JPEG quality), switchable at runtime via `POST /api/stream/profile?mode=...`.

## Wire protocol (WebSocket `/ws/stream`)

Host → client: binary messages are single JPEG frames; text messages are JSON (`init` with mode, `can_control` and display size; `stream_state`; `pong`).

Client → host (JSON), honoured only for control-capable sessions:

```json
{"type": "mouse_move", "x": 960, "y": 540}
{"type": "mouse_click", "x": 960, "y": 540, "button": "left", "action": "click"}
{"type": "mouse_scroll", "dy": -120}
{"type": "key_event", "key": "a", "action": "down", "modifiers": ["cmd"]}
{"type": "clipboard_paste", "text": "plain text"}
```

Check `app.py` for the exact field names; it is the source of truth.

## Non-goals

No file transfer, remote shell, port forwarding, relay/rendezvous service, NAT traversal, host-to-client clipboard sync, or multi-monitor selection UI.
