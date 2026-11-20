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


class TestRemoteTargets(unittest.TestCase):
    def test_start_ready_stop(self):
        # Target palsu: proses yang buka port TCP sampai di-SIGTERM
        import socket, tempfile, time
        from server.remote import RemoteTargets
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        holder = (f"import socket,time; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
                  f"s.bind(('127.0.0.1',{port})); s.listen(); time.sleep(60)")
        with tempfile.TemporaryDirectory() as d:
            rt = RemoteTargets({"remote_targets": {"box": {
                "label": "Box", "start": [sys.executable, "-c", holder],
                "ready_check": f"127.0.0.1:{port}", "url": "https://example.invalid/"}}}, Path(d))
            self.assertFalse(rt.status("box")["running"])
            rt.start("box")
            for _ in range(50):
                if rt.status("box")["ready"]:
                    break
                time.sleep(0.1)
            self.assertTrue(rt.status("box")["ready"])
            pid = rt._pid("box")
            rt.start("box")  # start kedua gak boleh bikin proses baru
            self.assertEqual(rt._pid("box"), pid)
            rt.stop("box")
            for _ in range(50):
                if not rt.status("box")["running"]:
                    break
                time.sleep(0.1)
            self.assertFalse(rt.status("box")["running"])


class TestTailscaleIdentity(unittest.TestCase):
    def _run(self, client_ip, login, qs=b""):
        import asyncio
        from server.tsidentity import TailscaleIdentityMiddleware
        seen = {}

        async def app(scope, receive, send):
            seen["qs"] = scope.get("query_string", b"")

        mw = TailscaleIdentityMiddleware(app, ["owner@github"], "CTRL-TOKEN")
        headers = [(b"tailscale-user-login", login.encode())] if login else []
        scope = {"type": "http", "client": (client_ip, 1234), "headers": headers, "query_string": qs}
        asyncio.run(mw(scope, None, None))
        return seen["qs"].decode()

    def test_trusted_login_via_loopback_gets_token(self):
        self.assertIn("token=CTRL-TOKEN", self._run("127.0.0.1", "Owner@GitHub", b"token=null"))

    def test_untrusted_login_ignored(self):
        self.assertEqual(self._run("127.0.0.1", "someone@gmail.com", b"token=x"), "token=x")

    def test_header_from_non_loopback_never_trusted(self):
        # LAN/IP Tailscale langsung ke port daemon: header bisa dipalsuin -> diabaikan
        self.assertEqual(self._run("192.168.18.50", "owner@github", b""), "")
        self.assertEqual(self._run("100.81.125.55", "owner@github", b""), "")


class TestMultiPlatformAdapter(unittest.TestCase):
    def test_input_controller_instantiation_and_methods(self):
        from server.input import InputController, DummyInputBackend
        ctrl = InputController(screen_size=(1920, 1080))
        self.assertIsNotNone(ctrl.backend)
        # Verify methods execute without exception
        ctrl.mouse_move(100, 100)
        ctrl.move(100, 100)
        ctrl.mouse_drag(150, 150)
        ctrl.drag(150, 150)
        ctrl.mouse_click(100, 100, "left", "click")
        ctrl.click(100, 100, "right", "down")
        ctrl.click(100, 100, "right", "up")
        ctrl.mouse_scroll(5)
        ctrl.scroll(-5)
        ctrl.key_event("a", "down")
        ctrl.key_event("a", "up")
        ctrl.key_event("Enter", "press")
        ctrl.release_all_modifiers()
        ctrl.paste_text("hello")
        ctrl.update_screen_size(2560, 1440)
        self.assertEqual(ctrl.screen_width, 2560)
        self.assertEqual(ctrl.screen_height, 1440)

    def test_dummy_backend_fallback(self):
        from server.input import DummyInputBackend
        dummy = DummyInputBackend((1920, 1080))
        dummy.mouse_move(50, 50)
        dummy.mouse_click(50, 50, "left", "click")
        dummy.mouse_scroll(10)
        dummy.key_event("Shift", "down")
        dummy.release_all_modifiers()
        dummy.paste_text("test")
        dummy.update_screen_size(800, 600)
        self.assertEqual(dummy.screen_width, 800)

    def test_linux_screen_resolution_fallback(self):
        from server.input import get_linux_screen_resolution
        # Without wlr-randr or xrandr matching, should return default 1920x1080
        w, h = get_linux_screen_resolution()
        self.assertIsInstance(w, int)
        self.assertIsInstance(h, int)
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

    def test_linux_key_mappings(self):
        from server.input import WEB_TO_EVDEV_KEY_MAP
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["Enter"], "KEY_ENTER")
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["Backspace"], "KEY_BACKSPACE")
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["Shift"], "KEY_LEFTSHIFT")
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["Control"], "KEY_LEFTCTRL")
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["Alt"], "KEY_LEFTALT")
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["Meta"], "KEY_LEFTMETA")
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["ArrowUp"], "KEY_UP")
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["a"], "KEY_A")
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["Z"], "KEY_Z")
        self.assertEqual(WEB_TO_EVDEV_KEY_MAP["1"], "KEY_1")

    def test_audio_engine_linux_fallback_non_crash(self):
        from server.audio import AudioEngine
        import asyncio
        engine = AudioEngine()
        engine.ensure_multi_output()

        async def run_test():
            await engine._stop_capture()
        asyncio.run(run_test())

    def test_linux_uinput_backend_mock(self):
        from unittest.mock import MagicMock, patch
        from server.input import LinuxUInputBackend
        with patch("server.input.LINUX_EVDEV_AVAILABLE", True), \
             patch("server.input.e") as mock_e, \
             patch("server.input.UInput") as mock_uinput, \
             patch("server.input.AbsInfo") as mock_absinfo:
            mock_e.ecodes = {"KEY_A": 30, "KEY_ENTER": 28, "BTN_LEFT": 272, "BTN_RIGHT": 273, "BTN_MIDDLE": 274, "REL_WHEEL": 8, "ABS_X": 0, "ABS_Y": 1}
            mock_e.KEY_A = 30
            mock_e.KEY_ENTER = 28
            mock_e.BTN_LEFT = 272
            mock_e.BTN_RIGHT = 273
            mock_e.BTN_MIDDLE = 274
            mock_e.REL_WHEEL = 8
            mock_e.ABS_X = 0
            mock_e.ABS_Y = 1
            mock_e.EV_KEY = 1
            mock_e.EV_ABS = 3
            mock_e.EV_REL = 2

            mock_mouse = MagicMock()
            mock_kbd = MagicMock()
            mock_uinput.side_effect = [mock_mouse, mock_kbd]

            backend = LinuxUInputBackend((1920, 1080))
            self.assertTrue(backend.available)

            backend.mouse_move(500, 400)
            mock_mouse.write.assert_any_call(mock_e.EV_ABS, mock_e.ABS_X, 500)
            mock_mouse.write.assert_any_call(mock_e.EV_ABS, mock_e.ABS_Y, 400)

            backend.mouse_click(500, 400, "left", "click")
            mock_mouse.write.assert_any_call(mock_e.EV_KEY, mock_e.BTN_LEFT, 1)
            mock_mouse.write.assert_any_call(mock_e.EV_KEY, mock_e.BTN_LEFT, 0)

            backend.mouse_scroll(-10)
            mock_mouse.write.assert_any_call(mock_e.EV_REL, mock_e.REL_WHEEL, 1)

            backend.key_event("Enter", "down")
            mock_kbd.write.assert_any_call(mock_e.EV_KEY, mock_e.KEY_ENTER, 1)

            backend.key_event("Enter", "up")
            mock_kbd.write.assert_any_call(mock_e.EV_KEY, mock_e.KEY_ENTER, 0)

            backend.release_all_modifiers()
            mock_kbd.syn.assert_called()

    def test_windows_screen_resolution_fallback(self):
        from server.input import get_windows_screen_resolution
        w, h = get_windows_screen_resolution()
        self.assertIsInstance(w, int)
        self.assertIsInstance(h, int)
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

    def test_windows_key_mappings(self):
        from server.input import WEB_TO_WIN_VK_MAP
        self.assertEqual(WEB_TO_WIN_VK_MAP["Enter"], 0x0D)
        self.assertEqual(WEB_TO_WIN_VK_MAP["Backspace"], 0x08)
        self.assertEqual(WEB_TO_WIN_VK_MAP["Tab"], 0x09)
        self.assertEqual(WEB_TO_WIN_VK_MAP["Escape"], 0x1B)
        self.assertEqual(WEB_TO_WIN_VK_MAP["Shift"], 0x10)
        self.assertEqual(WEB_TO_WIN_VK_MAP["Control"], 0x11)
        self.assertEqual(WEB_TO_WIN_VK_MAP["Alt"], 0x12)
        self.assertEqual(WEB_TO_WIN_VK_MAP["Meta"], 0x5B)
        self.assertEqual(WEB_TO_WIN_VK_MAP["ArrowUp"], 0x26)
        self.assertEqual(WEB_TO_WIN_VK_MAP["ArrowDown"], 0x28)
        self.assertEqual(WEB_TO_WIN_VK_MAP["a"], ord("A"))
        self.assertEqual(WEB_TO_WIN_VK_MAP["Z"], ord("Z"))
        self.assertEqual(WEB_TO_WIN_VK_MAP["1"], ord("1"))
        self.assertEqual(WEB_TO_WIN_VK_MAP["F1"], 0x70)
        self.assertEqual(WEB_TO_WIN_VK_MAP["F12"], 0x7B)

    def test_windows_input_backend_mock(self):
        from unittest.mock import MagicMock, patch
        from server.input import WindowsInputBackend

        mock_user32 = MagicMock()
        mock_windll = MagicMock(user32=mock_user32)

        with patch("sys.platform", "win32"), patch("ctypes.windll", mock_windll, create=True):
            backend = WindowsInputBackend((1920, 1080))
            self.assertTrue(backend.available)

            backend.mouse_move(500, 400)
            mock_user32.SetCursorPos.assert_called_with(500, 400)

            backend.mouse_click(500, 400, "left", "click")
            self.assertGreaterEqual(mock_user32.SendInput.call_count, 1)

            backend.mouse_scroll(-5)
            self.assertGreaterEqual(mock_user32.SendInput.call_count, 2)

            backend.key_event("Enter", "down")
            backend.key_event("Enter", "up")
            self.assertGreaterEqual(mock_user32.SendInput.call_count, 4)

            backend.key_event("A", "press", modifiers=["Control"])
            backend.release_all_modifiers()

            backend.paste_text("Hello Windows")

    def test_audio_engine_windows_fallback_non_crash(self):
        from server.audio import AudioEngine
        import asyncio
        from unittest.mock import patch

        with patch("sys.platform", "win32"):
            engine = AudioEngine()
            engine.ensure_multi_output()

            async def run_test():
                await engine._stop_capture()
            asyncio.run(run_test())

    def test_capture_engine_profiles(self):
        from server.capture import ScreenCaptureEngine
        cfg = {"display": {"width": 1920, "height": 1080, "fps": 30}}
        engine = ScreenCaptureEngine(cfg)
        self.assertIn("full", engine.profiles)
        self.assertIn("kafe", engine.profiles)


if __name__ == "__main__":
    unittest.main()

