"""
ShrineProjector Input Subsystem
Safe, Capability-Gated Synthetic Input Controller via macOS CoreGraphics & Linux Fallback
Supports Mouse, Scroll Wheel, Keyboard typing, and Special Keys.
"""

import sys
import time
import logging
from typing import Optional, Tuple

logger = logging.getLogger("shrine.input")

# macOS Native CoreGraphics bindings via ctypes
if sys.platform == "darwin":
    import ctypes
    from ctypes import c_double, c_uint32, c_void_p, c_bool, c_uint16, c_int32, POINTER, Structure

    class CGPoint(Structure):
        _fields_ = [("x", c_double), ("y", c_double)]

    try:
        _app_services = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        _core_foundation = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )

        # CGEventCreateMouseEvent
        _app_services.CGEventCreateMouseEvent.argtypes = [
            c_void_p, c_uint32, CGPoint, c_uint32
        ]
        _app_services.CGEventCreateMouseEvent.restype = c_void_p

        # CGEventCreateScrollWheelEvent
        _app_services.CGEventCreateScrollWheelEvent.argtypes = [
            c_void_p, c_uint32, c_uint32, c_int32
        ]
        _app_services.CGEventCreateScrollWheelEvent.restype = c_void_p

        # CGEventCreateKeyboardEvent
        _app_services.CGEventCreateKeyboardEvent.argtypes = [
            c_void_p, c_uint16, c_bool
        ]
        _app_services.CGEventCreateKeyboardEvent.restype = c_void_p

        # CGEventKeyboardSetUnicodeString
        # 64-bit Darwin UniCharCount is unsigned long (c_ulong), NOT c_uint32 (prevents SIGSEGV 139)
        _app_services.CGEventKeyboardSetUnicodeString.argtypes = [
            c_void_p, ctypes.c_ulong, POINTER(c_uint16)
        ]
        _app_services.CGEventKeyboardSetUnicodeString.restype = None

        # CGEventSetIntegerValueField (e.g. kCGMouseEventClickState)
        _app_services.CGEventSetIntegerValueField.argtypes = [
            c_void_p, c_uint32, ctypes.c_int64
        ]
        _app_services.CGEventSetIntegerValueField.restype = None

        # CGEventGetFlags
        _app_services.CGEventGetFlags.argtypes = [c_void_p]
        _app_services.CGEventGetFlags.restype = ctypes.c_uint64

        # CGEventSetFlags
        _app_services.CGEventSetFlags.argtypes = [c_void_p, ctypes.c_uint64]
        _app_services.CGEventSetFlags.restype = None

        # CGEventPost
        _app_services.CGEventPost.argtypes = [c_uint32, c_void_p]
        _app_services.CGEventPost.restype = None

        # CGWarpMouseCursorPosition (Hardware Cursor Movement)
        _app_services.CGWarpMouseCursorPosition.argtypes = [CGPoint]
        _app_services.CGWarpMouseCursorPosition.restype = ctypes.c_int

        # Display Metrics
        _app_services.CGMainDisplayID.argtypes = []
        _app_services.CGMainDisplayID.restype = c_uint32
        _app_services.CGDisplayPixelsWide.argtypes = [c_uint32]
        _app_services.CGDisplayPixelsWide.restype = ctypes.c_size_t
        _app_services.CGDisplayPixelsHigh.argtypes = [c_uint32]
        _app_services.CGDisplayPixelsHigh.restype = ctypes.c_size_t

        # CFRelease
        _core_foundation.CFRelease.argtypes = [c_void_p]
        _core_foundation.CFRelease.restype = None

        MACOS_CG_AVAILABLE = True
    except Exception as e:
        logger.warning(f"Failed to load macOS CoreGraphics bindings: {e}")
        MACOS_CG_AVAILABLE = False
else:
    MACOS_CG_AVAILABLE = False


def get_macos_screen_resolution() -> Tuple[int, int]:
    """Query current native macOS display resolution"""
    if MACOS_CG_AVAILABLE and sys.platform == "darwin":
        try:
            did = _app_services.CGMainDisplayID()
            w = int(_app_services.CGDisplayPixelsWide(did))
            h = int(_app_services.CGDisplayPixelsHigh(did))
            if w > 0 and h > 0:
                return w, h
        except Exception as e:
            logger.warning(f"Failed to query macOS display resolution: {e}")
    return 2560, 1440


class InputGuard:
    """
    Security gatekeeper for input injection.
    Ensures input is only processed when explicitly authorized.
    """
    # macOS CG Event Constants
    kCGEventLeftMouseDown = 1
    kCGEventLeftMouseUp = 2
    kCGEventRightMouseDown = 3
    kCGEventRightMouseUp = 4
    kCGEventMouseMoved = 5
    kCGEventLeftMouseDragged = 6
    kCGEventRightMouseDragged = 7
    kCGHIDEventTap = 0
    kCGSessionEventTap = 1
    kCGMouseButtonLeft = 0
    kCGMouseButtonRight = 1

    # Special macOS Key Codes
    SPECIAL_KEYCODES = {
        "Enter": 36,
        "Return": 36,
        "Tab": 48,
        "Space": 49,
        " ": 49,
        "Backspace": 51,
        "Escape": 53,
        "Esc": 53,
        "ArrowLeft": 123,
        "ArrowRight": 124,
        "ArrowDown": 125,
        "ArrowUp": 126,
        "Delete": 117,
        "Home": 115,
        "End": 119,
        "PageUp": 116,
        "PageDown": 121,
        "Meta": 55,       # Command Left
        "Command": 55,
        "Cmd": 55,
        "Control": 59,
        "Ctrl": 59,
        "Alt": 58,        # Option
        "Option": 58,
        "Shift": 56,
        "CapsLock": 57,
    }

    # Standard macOS Virtual Keycodes for ANSI characters (for shortcuts & combos)
    ANSI_KEYCODES = {
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
        "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
        "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26,
        "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
        "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45,
        "m": 46, ".": 47, "`": 50,
    }

    # CoreGraphics Modifier Flag bitmasks
    MODIFIER_MASKS = {
        "Shift": 0x00020000,
        "Control": 0x00040000,
        "Ctrl": 0x00040000,
        "Alt": 0x00080000,
        "Option": 0x00080000,
        "Command": 0x00100000,
        "Cmd": 0x00100000,
        "Meta": 0x00100000,
    }

    def __init__(
        self,
        max_rate_per_sec: int = 120,
        screen_size: Optional[Tuple[int, int]] = None,
        max_clipboard_chars: int = 65536,
    ):
        self.max_rate = max_rate_per_sec
        if screen_size:
            self.screen_width, self.screen_height = screen_size
        else:
            self.screen_width, self.screen_height = get_macos_screen_resolution()
        self.max_clipboard_chars = max_clipboard_chars
        self._last_event_time = 0.0
        self._event_count = 0
        self._is_mouse_down = False
        self._active_modifiers: set = set()

    def update_screen_size(self, width: int, height: int):
        """Update active screen dimensions for coordinate clamping"""
        if width > 0 and height > 0:
            self.screen_width = width
            self.screen_height = height
            logger.info(f"InputGuard screen bounds updated to {width}x{height}")

    def check_rate_limit(self) -> bool:
        """Rate limiter to mitigate input flooding attacks"""
        now = time.time()
        if now - self._last_event_time > 1.0:
            self._last_event_time = now
            self._event_count = 0
        self._event_count += 1
        return self._event_count <= self.max_rate

    def clamp_coordinates(self, x: float, y: float) -> Tuple[float, float]:
        """Strict coordinate bounds enforcement"""
        cx = max(0.0, min(float(x), float(self.screen_width)))
        cy = max(0.0, min(float(y), float(self.screen_height)))
        return cx, cy

    def _post_event(self, evt):
        if not evt:
            return
        _app_services.CGEventPost(self.kCGHIDEventTap, evt)
        _core_foundation.CFRelease(evt)

    def handle_mouse_move(self, x: float, y: float, is_interactive: bool):
        if not is_interactive or not MACOS_CG_AVAILABLE:
            return
        if not self.check_rate_limit():
            return

        cx, cy = self.clamp_coordinates(x, y)
        pt = CGPoint(cx, cy)

        # Warp hardware cursor so WindowServer and UI elements track the cursor
        _app_services.CGWarpMouseCursorPosition(pt)

        event_type = (
            self.kCGEventLeftMouseDragged
            if self._is_mouse_down
            else self.kCGEventMouseMoved
        )
        evt = _app_services.CGEventCreateMouseEvent(
            None, event_type, pt, self.kCGMouseButtonLeft
        )
        if evt:
            self._post_event(evt)

    def handle_mouse_click(
        self, x: float, y: float, button: str, action: str, is_interactive: bool
    ):
        if not is_interactive:
            logger.warning("[INPUT] Click ignored: client not in interactive mode")
            return
        if not MACOS_CG_AVAILABLE:
            logger.warning("[INPUT] Click ignored: macOS CoreGraphics unavailable")
            return
        if not self.check_rate_limit():
            return

        cx, cy = self.clamp_coordinates(x, y)
        pt = CGPoint(cx, cy)
        is_right = button == "right"
        mouse_button = self.kCGMouseButtonRight if is_right else self.kCGMouseButtonLeft
        logger.info(f"[INPUT CLICK] ({cx:.1f}, {cy:.1f}) btn={button} act={action}")

        # Crucial: Warp hardware cursor position to target before generating click events
        _app_services.CGWarpMouseCursorPosition(pt)

        # Emit MouseMoved event so AppKit / Cocoa window focus and hover track immediately
        evt_move = _app_services.CGEventCreateMouseEvent(
            None, self.kCGEventMouseMoved, pt, self.kCGMouseButtonLeft
        )
        if evt_move:
            self._post_event(evt_move)

        if action in ("down", "click"):
            evt_type = (
                self.kCGEventRightMouseDown if is_right else self.kCGEventLeftMouseDown
            )
            evt = _app_services.CGEventCreateMouseEvent(
                None, evt_type, pt, mouse_button
            )
            if evt:
                _app_services.CGEventSetIntegerValueField(evt, 1, 1) # kCGMouseEventClickState = 1
                self._post_event(evt)
            if not is_right:
                self._is_mouse_down = True

        if action == "click":
            # 50ms transition gap so macOS WindowServer registers mouse down -> up as a distinct click
            time.sleep(0.05)

        if action in ("up", "click"):
            evt_type = (
                self.kCGEventRightMouseUp if is_right else self.kCGEventLeftMouseUp
            )
            evt = _app_services.CGEventCreateMouseEvent(
                None, evt_type, pt, mouse_button
            )
            if evt:
                _app_services.CGEventSetIntegerValueField(evt, 1, 1) # kCGMouseEventClickState = 1
                self._post_event(evt)
            if not is_right:
                self._is_mouse_down = False

    def handle_mouse_scroll(self, dy: float, is_interactive: bool):
        """Scroll wheel injection (trackpad two-finger drag or mouse wheel)"""
        if not is_interactive or not MACOS_CG_AVAILABLE:
            return
        if not self.check_rate_limit():
            return

        # macOS scroll wheel units: pixel (0) or line (1)
        # Invert or normalize delta
        scroll_delta = int(max(-100, min(100, -dy)))
        evt = _app_services.CGEventCreateScrollWheelEvent(None, 0, 1, scroll_delta)
        if evt:
            self._post_event(evt)

    def handle_key_event(
        self,
        key: str,
        action: str,
        is_interactive: bool,
        modifiers: Optional[list] = None,
        exact_mods: bool = False,
    ):
        """
        Synthesizes keyboard events on macOS:
        - Special keys (Enter, Backspace, Arrows) use virtual keycodes.
        - Printable characters use Unicode string injection and standard ANSI keycodes for combos.
        - Modifier flags (Control, Command, Shift, Option) are tracked and applied directly to CGEvent.
        """
        if not is_interactive:
            logger.warning("[INPUT] Key ignored: client not in interactive mode")
            return
        if not MACOS_CG_AVAILABLE or not key:
            return
        if not self.check_rate_limit():
            return

        is_down = action in ("down", "press")
        logger.info(f"[INPUT KEY] key={key!r} act={action} mods={modifiers}")

        # Normalization for modifier keys
        norm_key = key
        if key in ("Cmd", "Meta"):
            norm_key = "Command"
        elif key == "Ctrl":
            norm_key = "Control"
        elif key == "Esc":
            norm_key = "Escape"
        elif key == "Alt":
            norm_key = "Option"

        # Track modifier state
        if norm_key in ("Command", "Control", "Option", "Shift"):
            if action in ("down", "press"):
                self._active_modifiers.add(norm_key)
            elif action == "up":
                self._active_modifiers.discard(norm_key)

        # Build current flag mask
        # exact_mods: klien (keyboard fisik) kirim daftar modifier lengkap per tombol,
        # jadi state server yang mungkin nyangkut (keyup ketelen iPadOS) diabaikan.
        if exact_mods and norm_key not in ("Command", "Control", "Option", "Shift"):
            active_mods = set()
        else:
            active_mods = set(self._active_modifiers)
        if modifiers:
            for m in modifiers:
                nm = "Command" if m in ("Cmd", "Meta") else ("Control" if m == "Ctrl" else ("Option" if m == "Alt" else m))
                active_mods.add(nm)

        mod_flags = 0
        for m in active_mods:
            mod_flags |= self.MODIFIER_MASKS.get(m, 0)

        def _apply_flags(evt):
            if evt and mod_flags:
                cur = _app_services.CGEventGetFlags(evt)
                _app_services.CGEventSetFlags(evt, cur | mod_flags)

        # Case 1: Special key
        if norm_key in self.SPECIAL_KEYCODES:
            keycode = self.SPECIAL_KEYCODES[norm_key]
            if action in ("click", "press"):
                evt_d = _app_services.CGEventCreateKeyboardEvent(None, keycode, True)
                if evt_d:
                    _apply_flags(evt_d)
                    self._post_event(evt_d)
                time.sleep(0.03)
                evt_u = _app_services.CGEventCreateKeyboardEvent(None, keycode, False)
                if evt_u:
                    _apply_flags(evt_u)
                    self._post_event(evt_u)
            else:
                evt = _app_services.CGEventCreateKeyboardEvent(None, keycode, is_down)
                if evt:
                    _apply_flags(evt)
                    self._post_event(evt)
            return

        # Case 2: Printable characters / alphanumeric single character
        if len(key) == 1 and key.lower() in self.ANSI_KEYCODES:
            kc = self.ANSI_KEYCODES[key.lower()]
            if action in ("click", "press"):
                evt_d = _app_services.CGEventCreateKeyboardEvent(None, kc, True)
                if evt_d:
                    u_chars = (c_uint16 * 1)(ord(key))
                    _app_services.CGEventKeyboardSetUnicodeString(evt_d, 1, u_chars)
                    _apply_flags(evt_d)
                    self._post_event(evt_d)
                time.sleep(0.02)
                evt_u = _app_services.CGEventCreateKeyboardEvent(None, kc, False)
                if evt_u:
                    u_chars = (c_uint16 * 1)(ord(key))
                    _app_services.CGEventKeyboardSetUnicodeString(evt_u, 1, u_chars)
                    _apply_flags(evt_u)
                    self._post_event(evt_u)
            else:
                evt = _app_services.CGEventCreateKeyboardEvent(None, kc, is_down)
                if evt:
                    u_chars = (c_uint16 * 1)(ord(key))
                    _app_services.CGEventKeyboardSetUnicodeString(evt, 1, u_chars)
                    _apply_flags(evt)
                    self._post_event(evt)
            return

        # Case 3: Arbitrary multi-character text or unmapped Unicode
        if is_down:
            for ch in key:
                ch_low = ch.lower()
                kc = self.ANSI_KEYCODES.get(ch_low, 0)
                evt_d = _app_services.CGEventCreateKeyboardEvent(None, kc, True)
                if evt_d:
                    u_chars = (c_uint16 * 1)(ord(ch))
                    _app_services.CGEventKeyboardSetUnicodeString(evt_d, 1, u_chars)
                    _apply_flags(evt_d)
                    self._post_event(evt_d)

                time.sleep(0.01)
                evt_u = _app_services.CGEventCreateKeyboardEvent(None, kc, False)
                if evt_u:
                    _apply_flags(evt_u)
                    self._post_event(evt_u)

    def release_all_modifiers(self):
        """Lepas semua modifier yang masih dianggap ketahan (dipanggil saat klien putus/pindah app)."""
        if not MACOS_CG_AVAILABLE:
            self._active_modifiers.clear()
            return
        for mod in list(self._active_modifiers):
            kc = self.SPECIAL_KEYCODES.get(mod)
            if kc is not None:
                evt = _app_services.CGEventCreateKeyboardEvent(None, kc, False)
                if evt:
                    _app_services.CGEventSetFlags(evt, 0)
                    self._post_event(evt)
        if self._active_modifiers:
            logger.info(f"[INPUT] release_all_modifiers: {sorted(self._active_modifiers)}")
        self._active_modifiers.clear()

    def handle_paste_text(self, text: str, is_interactive: bool):
        """
        Instant host clipboard paste via pbcopy and Command+V
        Safely sanitizes text, loads into macOS pasteboard, and triggers paste shortcut.
        """
        if not is_interactive or not text:
            return
        sanitized = self.sanitize_clipboard_text(text)
        if not sanitized:
            return
        logger.info(f"[INPUT PASTE] {len(sanitized)} chars")

        if sys.platform == "darwin":
            import subprocess
            try:
                p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                p.communicate(sanitized.encode("utf-8"))
                time.sleep(0.04)

                kc_cmd = self.SPECIAL_KEYCODES.get("Command", 55)
                kc_v = self.ANSI_KEYCODES.get("v", 9)
                flag_cmd = self.MODIFIER_MASKS.get("Command", 0x00100000)

                # Emit Cmd down
                evt_cmd_d = _app_services.CGEventCreateKeyboardEvent(None, kc_cmd, True)
                if evt_cmd_d:
                    self._post_event(evt_cmd_d)
                time.sleep(0.02)

                # Emit V down (with Cmd flag)
                evt_v_d = _app_services.CGEventCreateKeyboardEvent(None, kc_v, True)
                if evt_v_d:
                    _app_services.CGEventSetFlags(evt_v_d, _app_services.CGEventGetFlags(evt_v_d) | flag_cmd)
                    self._post_event(evt_v_d)
                time.sleep(0.03)

                # Emit V up (with Cmd flag)
                evt_v_u = _app_services.CGEventCreateKeyboardEvent(None, kc_v, False)
                if evt_v_u:
                    _app_services.CGEventSetFlags(evt_v_u, _app_services.CGEventGetFlags(evt_v_u) | flag_cmd)
                    self._post_event(evt_v_u)
                time.sleep(0.02)

                # Emit Cmd up
                evt_cmd_u = _app_services.CGEventCreateKeyboardEvent(None, kc_cmd, False)
                if evt_cmd_u:
                    self._post_event(evt_cmd_u)
            except Exception as e:
                logger.error(f"Failed to paste text via pbcopy: {e}")

    def sanitize_clipboard_text(self, text: str) -> Optional[str]:
        """
        Anti-FreeRDP Exploit Sanitizer:
        - Plaintext UTF-8 string only
        - Null-bytes stripped
        - Hard length cap
        """
        if not isinstance(text, str):
            return None
        sanitized = text.replace("\x00", "")
        if len(sanitized) > self.max_clipboard_chars:
            sanitized = sanitized[: self.max_clipboard_chars]
        return sanitized
