# ShrineProjector

A small, self-hosted screen viewer and remote control for a Mac, meant to be used only inside your own private network (Tailscale or LAN). The client is a plain web page, so an iPhone, iPad or another laptop can view and control the Mac without installing anything.

It is a personal project built for one household's setup, shared in case it is useful to others who want a remote desktop with as little exposure as possible. It is not a drop-in replacement for a full remote desktop product.

> **Disclaimer: this project is a teardown-inspired rebuild, not a fork.**
> The design started from a teardown (architecture and threat review) of [RustDesk](https://github.com/rustdesk/rustdesk). ShrineProjector contains **no RustDesk source code**: it is an independent Python/JavaScript implementation and is **not affiliated with, endorsed by, or derived from the code of** the RustDesk project. RustDesk is licensed under AGPL-3.0; this repository is MIT and shares no code with it. The notes below describe the trade-offs that motivated a narrower tool. They are not a claim that RustDesk is unsafe for its intended use: RustDesk solves a much harder problem (connecting arbitrary machines across the public internet) and many of its design choices follow from that.

## Why a narrower tool

A general-purpose remote desktop has to reach machines that share no network, so it needs rendezvous and relay infrastructure, NAT traversal, file transfer, clipboard sync across platforms, and so on. Each of those is attack surface. Two published RustDesk advisories illustrate the kind of risk that comes with breadth:

- [CVE-2026-76840](https://nvd.nist.gov/vuln/detail/cve-2026-76840): heap buffer overflow in the Windows clipboard redirection (RustDesk through 1.4.9). That code lives in `libs/clipboard/src/windows/wf_cliprdr.c`, which is derived from FreeRDP's C implementation, a reminder that a Rust codebase can still carry memory-unsafe C.
- [CVE-2026-73108](https://www.tenable.com/cve/CVE-2026-73108): uncontrolled memory allocation in `BytesCodec` (RustDesk before 1.4.7).

Check the advisories and RustDesk's release notes for current fixed versions before drawing conclusions about any particular install.

If all of your devices are already on one private network, most of that machinery is unnecessary. ShrineProjector drops it:

- **No relay or rendezvous server.** Reachability is your network's job (Tailscale/WireGuard or LAN). The daemon only accepts clients from allow-listed subnets.
- **No file transfer, no shell, no port forwarding** in the protocol. Use SSH or `tailscale file` for that.
- **No client binary.** A browser page served by the daemon.
- **Small scope:** screen frames out; mouse, keyboard, scroll and plain-text paste in; system audio out.

## What it actually does (and does not)

Being precise here matters more than sounding impressive.

| Area | Current behaviour |
|---|---|
| Platform | macOS host only (Apple Silicon tested). Linux/Windows hosts are not implemented. |
| Video | `ffmpeg` avfoundation screen capture → MJPEG frames over WebSocket. Two profiles: `full` (native res, ~30 fps) and `kafe` (1280 px, 12 fps, for slow networks). |
| Audio | System audio via the [BlackHole](https://github.com/ExistentialAudio/BlackHole) loopback driver, captured with PortAudio (`sounddevice`), PCM over WebSocket. |
| Input | Mouse, scroll, keyboard and paste injected with CoreGraphics via `ctypes`. Rate-limited (default 120 events/s), coordinates clamped to the screen. |
| Auth | Static bearer tokens from a local config file, compared in constant time. Each token is either `admin` (view + control) or `viewer` (view only; input is dropped server-side). The daemon refuses to start with placeholder or short tokens. |
| Network gate | Source-IP allow-list (default: loopback + Tailscale CGNAT range). Bind address is configurable; default is `127.0.0.1`, intended to sit behind `tailscale serve` for HTTPS. |
| Clipboard | Plain UTF-8 text only, null bytes stripped, length-capped (default 64 KiB). Host → client clipboard sync does not exist. |
| Power | Capture is off until a viewer connects and turns itself off after 5 minutes without viewers. |

Known limitations, stated plainly:

- Tokens do not expire and are not tied to a device identity; anyone holding the admin token who can reach the port can control the Mac. Treat it like an SSH key.
- There is no in-app TLS. Use `tailscale serve` (or another reverse proxy) for HTTPS; plain HTTP on a LAN exposes the token to anyone sniffing that LAN.
- The token is passed as a URL query parameter for convenience, so it can end up in browser history.
- It relies on native code you should trust separately: `ffmpeg`, PortAudio, macOS frameworks.
- Code comments are partly in Indonesian.

## Setup (macOS)

Requirements: Python 3.11+, `ffmpeg` (`brew install ffmpeg`), optionally BlackHole 2ch + a Multi-Output Device for audio, and `switchaudio-osx` if you want the daemon to select that output automatically.

```bash
git clone https://github.com/saturninfj/shrine-projector.git
cd shrine-projector
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 scripts/init-config          # writes config/shrine-config.json (mode 600) with random tokens
./scripts/run.sh
```

Grant the terminal (or the launchd job) **Screen Recording** and **Accessibility** permissions in System Settings → Privacy & Security, otherwise frames are black and input is ignored.

Expose it to your tailnet over HTTPS, without opening a LAN port:

```bash
tailscale serve --bg --https=8766 http://127.0.0.1:8765
```

Then open `https://<your-machine>.<tailnet>.ts.net:8766/` on the client and paste the admin or viewer token when asked. The browser remembers the token after the server accepts it.

To run at login, render `scripts/shrine-projector.plist.template` (instructions inside the file). Rotate tokens any time with `python3 scripts/init-config --rotate` and restart the daemon.

`scripts/projector` is a small CLI: `status`, `on`, `off`, `toggle`, `url`, `open`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Reporting security issues

Please open a private security advisory on GitHub rather than a public issue.

## License

MIT, see [LICENSE](LICENSE).
