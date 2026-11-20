"""Login tanpa token lewat identitas Tailscale (header dari `tailscale serve`)."""

from typing import Optional
from urllib.parse import parse_qsl, urlencode


class TailscaleIdentityMiddleware:
    """Login lewat identitas Tailscale, tanpa token.

    `tailscale serve` nambahin header `Tailscale-User-Login` (gak bisa dipalsuin dari tailnet) lalu
    nerusin ke daemon dari 127.0.0.1. Kalau request datang dari loopback DAN login-nya ada di
    `security.trusted_tailscale_logins`, request diperlakukan seolah bawa token kontrol pertama.
    Akses langsung (LAN / IP Tailscale ke port daemon) TIDAK pernah dipercaya lewat header ini.
    Catatan: proses lokal di mesin yang sama bisa malsuin header ke 127.0.0.1; itu batas kepercayaan
    yang diterima (proses lokal user yang sama memang udah bisa banyak hal).
    ASGI murni biar berlaku buat HTTP dan WebSocket."""

    LOOPBACK = {"127.0.0.1", "::1"}

    def __init__(self, app, trusted_logins, token: Optional[str]):
        self.app = app
        self.trusted = {x.strip().lower() for x in (trusted_logins or []) if x.strip()}
        self.token = token

    async def __call__(self, scope, receive, send):
        if self.trusted and self.token and scope.get("type") in ("http", "websocket"):
            client = scope.get("client")
            if client and client[0] in self.LOOPBACK:
                login = b""
                for k, v in scope.get("headers") or []:
                    if k == b"tailscale-user-login":
                        login = v
                        break
                if login.decode("latin-1").strip().lower() in self.trusted:
                    qs = [(k, v) for k, v in parse_qsl(scope.get("query_string", b"").decode("latin-1"),
                                                         keep_blank_values=True) if k != "token"]
                    qs.append(("token", self.token))
                    scope = dict(scope, query_string=urlencode(qs).encode("latin-1"))
        await self.app(scope, receive, send)
