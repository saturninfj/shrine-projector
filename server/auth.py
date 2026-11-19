"""
ShrineProjector Authentication & Capability Gate
Zero-Trust, Constant-Time Token Verification & Tailscale Verification
"""

import hmac
import secrets
from typing import Optional, Dict, Any


class AuthGate:
    MIN_TOKEN_LEN = 32
    PLACEHOLDER_MARKERS = ("change-me", "changeme", "replace", "example", "placeholder")

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.security_cfg = config.get("security", {})
        self.tokens_db = {
            t["token"]: t for t in self.security_cfg.get("auth_tokens", [])
        }

    def weak_tokens(self) -> list:
        """Token yang kependekan atau masih placeholder. Kosong = aman dipakai."""
        weak = []
        for tok in self.tokens_db:
            low = tok.lower()
            if len(tok) < self.MIN_TOKEN_LEN or any(m in low for m in self.PLACEHOLDER_MARKERS):
                weak.append(tok)
        return weak

    def verify_token(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Verify bearer token using constant-time comparison.
        Returns token payload if valid, None if invalid.
        """
        if not token:
            return None

        # Constant-time comparison across registered tokens
        for registered_token, info in self.tokens_db.items():
            if hmac.compare_digest(registered_token, token):
                return info
        return None

    def can_control(self, token_info: Optional[Dict[str, Any]]) -> bool:
        """
        Check if the token grants interactive control (mouse/keyboard).
        Must be enabled globally in config AND granted to the specific token.
        """
        if not token_info:
            return False
        global_allowed = self.security_cfg.get("allow_interactive_mode", False)
        return global_allowed and token_info.get("can_control", False)

    @staticmethod
    def generate_secure_token(prefix: str = "shrine-proj-") -> str:
        """Generate high-entropy 256-bit URL-safe token"""
        return f"{prefix}{secrets.token_urlsafe(32)}"
