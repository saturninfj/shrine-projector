"""
ShrineProjector Automated Security & Functionality Verification Suite
"""

import sys
import unittest
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from server.auth import AuthGate
from server.input import InputGuard


class TestShrineProjectorSecurity(unittest.TestCase):
    def setUp(self):
        self.mock_config = {
            "server": {
                "bind_host": "127.0.0.1",
                "port": 8765,
                "reject_public_0_0_0_0": True
            },
            "security": {
                "default_mode": "PROJECTOR",
                "allow_interactive_mode": True,
                "max_clipboard_chars": 100,
                "auth_tokens": [
                    {
                        "token": "test-admin-secret-token",
                        "role": "admin",
                        "can_control": True
                    },
                    {
                        "token": "test-viewer-read-only-token",
                        "role": "viewer",
                        "can_control": False
                    }
                ]
            },
            "display": {
                "width": 1920,
                "height": 1080
            }
        }
        self.auth = AuthGate(self.mock_config)
        self.guard = InputGuard(
            max_rate_per_sec=100,
            screen_size=(1920, 1080),
            max_clipboard_chars=100
        )

    def test_token_authentication(self):
        # Valid admin token
        admin = self.auth.verify_token("test-admin-secret-token")
        self.assertIsNotNone(admin)
        self.assertEqual(admin["role"], "admin")
        self.assertTrue(self.auth.can_control(admin))

        # Valid viewer token
        viewer = self.auth.verify_token("test-viewer-read-only-token")
        self.assertIsNotNone(viewer)
        self.assertEqual(viewer["role"], "viewer")
        self.assertFalse(self.auth.can_control(viewer))

        # Invalid token
        invalid = self.auth.verify_token("random-hacker-guess")
        self.assertIsNone(invalid)

    def test_coordinate_clamping(self):
        # Out-of-bounds negative coordinates
        cx, cy = self.guard.clamp_coordinates(-50, -100)
        self.assertEqual(cx, 0.0)
        self.assertEqual(cy, 0.0)

        # Out-of-bounds oversize coordinates
        cx, cy = self.guard.clamp_coordinates(2500, 3000)
        self.assertEqual(cx, 1920.0)
        self.assertEqual(cy, 1080.0)

        # Normal coordinates
        cx, cy = self.guard.clamp_coordinates(500, 600)
        self.assertEqual(cx, 500.0)
        self.assertEqual(cy, 600.0)

    def test_screen_size_update(self):
        self.guard.update_screen_size(2560, 1440)
        self.assertEqual(self.guard.screen_width, 2560)
        self.assertEqual(self.guard.screen_height, 1440)
        cx, cy = self.guard.clamp_coordinates(2500, 1400)
        self.assertEqual(cx, 2500.0)
        self.assertEqual(cy, 1400.0)

    def test_clipboard_sanitizer(self):
        # Null byte stripping
        dirty = "hello\x00world\x00test"
        clean = self.guard.sanitize_clipboard_text(dirty)
        self.assertEqual(clean, "helloworldtest")

        # Length cap enforcement
        oversize = "A" * 200
        capped = self.guard.sanitize_clipboard_text(oversize)
        self.assertEqual(len(capped), 100)

    def test_weak_tokens_rejected(self):
        # Token pendek & placeholder harus ketahuan; token acak 256-bit lolos
        strong = AuthGate.generate_secure_token()
        gate = AuthGate({"security": {"auth_tokens": [
            {"token": strong},
            {"token": "short"},
            {"token": "REPLACE-ME-run-scripts-init-config-padding-padding"},
        ]}})
        weak = gate.weak_tokens()
        self.assertNotIn(strong, weak)
        self.assertEqual(len(weak), 2)

    def test_example_config_is_not_usable_as_is(self):
        # Config contoh di repo wajib ditolak daemon sampai init-config dijalankan
        import json
        example = json.loads((PROJECT_ROOT / "config" / "shrine-config.example.json").read_text())
        gate = AuthGate(example)
        self.assertEqual(len(gate.weak_tokens()), len(example["security"]["auth_tokens"]))


if __name__ == "__main__":
    unittest.main()
