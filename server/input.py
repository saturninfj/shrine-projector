"""
ShrineProjector Input Subsystem
Safe, Capability-Gated Synthetic Input Controller via macOS CoreGraphics & Linux evdev.UInput
Supports Mouse, Scroll Wheel, Keyboard typing, Special Keys, and Modifiers.
"""

import os
import sys
import time
import logging
from typing import Optional, Tuple, Dict, Any, List, Set

logger = logging.getLogger("shrine.input")

# ── macOS Native CoreGraphics bindings via ctypes ──
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


# ── Linux evdev / UInput bindings ──
try:
    import evdev
    from evdev import UInput, ecodes as e, AbsInfo
    LINUX_EVDEV_AVAILABLE = True
except (ImportError, Exception):
    evdev = None
    e = None
    UInput = None
    AbsInfo = None
    LINUX_EVDEV_AVAILABLE = False


def get_macos_screen_resolution() -> Tuple[int, int]:
    """Query current native macOS display resolution"""
    if MACOS_CG_AVAILABLE and sys.platform == "darwin":
        try:
            did = _app_services.CGMainDisplayID()
            w = int(_app_services.CGDisplayPixelsWide(did))
            h = int(_app_services.CGDisplayPixelsHigh(did))
            if w > 0 and h > 0:
                return w, h
        except Exception as ex:
            logger.warning(f"Failed to query macOS display resolution: {ex}")
    return 2560, 1440


def get_linux_screen_resolution() -> Tuple[int, int]:
    """Query current screen resolution on Linux using wlr-randr or xrandr with 1920x1080 fallback."""
    import shutil
    import subprocess
    import re

    # 1. Try wlr-randr (Wayland / wlroots)
    if shutil.which("wlr-randr"):
        try:
            out = subprocess.check_output(
                ["wlr-randr"], text=True, stderr=subprocess.DEVNULL, timeout=2.0
            )
            # Find mode marked as current, e.g. "3840x2160 px, 59.997002 Hz (preferred, current)"
            match = re.search(r"(\d+)x(\d+)\s+px.*?(?:current|\(current\))", out)
            if match:
                w, h = int(match.group(1)), int(match.group(2))
                if w > 0 and h > 0:
                    return w, h
            match = re.search(r"(\d+)x(\d+)\s+px", out)
            if match:
                w, h = int(match.group(1)), int(match.group(2))
                if w > 0 and h > 0:
                    return w, h
        except Exception as ex:
            logger.debug(f"wlr-randr query failed: {ex}")

    # 2. Try xrandr (X11 / Xwayland)
    if shutil.which("xrandr"):
        try:
            out = subprocess.check_output(
                ["xrandr"], text=True, stderr=subprocess.DEVNULL, timeout=2.0
            )
            match = re.search(r"current\s+(\d+)\s*x\s*(\d+)", out)
            if match:
                w, h = int(match.group(1)), int(match.group(2))
                if w > 0 and h > 0:
                    return w, h
            match = re.search(r"connected.*?\b(\d+)x(\d+)\+\d+\+\d+", out)
            if match:
                w, h = int(match.group(1)), int(match.group(2))
                if w > 0 and h > 0:
                    return w, h
        except Exception as ex:
            logger.debug(f"xrandr query failed: {ex}")

    return 1920, 1080


def get_screen_resolution() -> Tuple[int, int]:
    """Query screen resolution dynamically for current host platform."""
    if sys.platform == "darwin":
        return get_macos_screen_resolution()
    elif sys.platform == "linux":
        return get_linux_screen_resolution()
    return 1920, 1080


# Mapping from standard Web KeyboardEvent keys to Linux evdev ecodes string names
WEB_TO_EVDEV_KEY_MAP = {
    # Alphanumeric characters
    **{chr(c): f"KEY_{chr(c).upper()}" for c in range(ord('a'), ord('z') + 1)},
    **{chr(c): f"KEY_{chr(c)}" for c in range(ord('A'), ord('Z') + 1)},
    **{str(d): f"KEY_{d}" for d in range(10)},
    # Navigation & control
    "Enter": "KEY_ENTER",
    "Return": "KEY_ENTER",
    "Tab": "KEY_TAB",
    "Space": "KEY_SPACE",
    " ": "KEY_SPACE",
    "Backspace": "KEY_BACKSPACE",
    "Escape": "KEY_ESC",
    "Esc": "KEY_ESC",
    "Delete": "KEY_DELETE",
    "Insert": "KEY_INSERT",
    "Home": "KEY_HOME",
    "End": "KEY_END",
    "PageUp": "KEY_PAGEUP",
    "PageDown": "KEY_PAGEDOWN",
    "ArrowLeft": "KEY_LEFT",
    "ArrowRight": "KEY_RIGHT",
    "ArrowDown": "KEY_DOWN",
    "ArrowUp": "KEY_UP",
    "CapsLock": "KEY_CAPSLOCK",
    # Modifiers
    "Shift": "KEY_LEFTSHIFT",
    "ShiftLeft": "KEY_LEFTSHIFT",
    "ShiftRight": "KEY_RIGHTSHIFT",
    "Control": "KEY_LEFTCTRL",
    "Ctrl": "KEY_LEFTCTRL",
    "ControlLeft": "KEY_LEFTCTRL",
    "ControlRight": "KEY_RIGHTCTRL",
    "Alt": "KEY_LEFTALT",
    "Option": "KEY_LEFTALT",
    "AltLeft": "KEY_LEFTALT",
    "AltRight": "KEY_RIGHTALT",
    "Meta": "KEY_LEFTMETA",
    "Command": "KEY_LEFTMETA",
    "Cmd": "KEY_LEFTMETA",
    "MetaLeft": "KEY_LEFTMETA",
    "MetaRight": "KEY_RIGHTMETA",
    # Punctuation & symbols
    "`": "KEY_GRAVE",
    "~": "KEY_GRAVE",
    "-": "KEY_MINUS",
    "_": "KEY_MINUS",
    "=": "KEY_EQUAL",
    "+": "KEY_EQUAL",
    "[": "KEY_LEFTBRACE",
    "{": "KEY_LEFTBRACE",
    "]": "KEY_RIGHTBRACE",
    "}": "KEY_RIGHTBRACE",
    "\\": "KEY_BACKSLASH",
    "|": "KEY_BACKSLASH",
    ";": "KEY_SEMICOLON",
    ":": "KEY_SEMICOLON",
    "'": "KEY_APOSTROPHE",
    '"': "KEY_APOSTROPHE",
    ",": "KEY_COMMA",
    "<": "KEY_COMMA",
    ".": "KEY_DOT",
    ">": "KEY_DOT",
    "/": "KEY_SLASH",
    "?": "KEY_SLASH",
    "!": "KEY_1",
    "@": "KEY_2",
    "#": "KEY_3",
    "$": "KEY_4",
    "%": "KEY_5",
    "^": "KEY_6",
    "&": "KEY_7",
    "*": "KEY_8",
    "(": "KEY_9",
    ")": "KEY_0",
    # Function keys
    **{f"F{i}": f"KEY_F{i}" for i in range(1, 13)},
}


# ── Platform Backends ──

class MacOSInputBackend:
    """macOS Native CoreGraphics synthetic input implementation."""

    def __init__(self, screen_size: Tuple[int, int]):
        self.screen_width, self.screen_height = screen_size
        self._active_modifiers: Set[str] = set()
        self._is_mouse_down = False

    def update_screen_size(self, width: int, height: int):
        self.screen_width = width
        self.screen_height = height

    def _post_event(self, evt):
        if not evt or not MACOS_CG_AVAILABLE:
            return
        _app_services.CGEventPost(InputGuard.kCGHIDEventTap, evt)
        _core_foundation.CFRelease(evt)

    def mouse_move(self, x: float, y: float, is_drag: bool = False):
        if not MACOS_CG_AVAILABLE:
            return
        pt = CGPoint(x, y)
        _app_services.CGWarpMouseCursorPosition(pt)
        event_type = (
            InputGuard.kCGEventLeftMouseDragged
            if is_drag
            else InputGuard.kCGEventMouseMoved
        )
        evt = _app_services.CGEventCreateMouseEvent(
            None, event_type, pt, InputGuard.kCGMouseButtonLeft
        )
        if evt:
            self._post_event(evt)

    def mouse_click(self, x: float, y: float, button: str, action: str):
        if not MACOS_CG_AVAILABLE:
            return
        pt = CGPoint(x, y)
        is_right = button == "right"
        mouse_button = InputGuard.kCGMouseButtonRight if is_right else InputGuard.kCGMouseButtonLeft
        _app_services.CGWarpMouseCursorPosition(pt)

        evt_move = _app_services.CGEventCreateMouseEvent(
            None, InputGuard.kCGEventMouseMoved, pt, InputGuard.kCGMouseButtonLeft
        )
        if evt_move:
            self._post_event(evt_move)

        if action in ("down", "click"):
            evt_type = (
                InputGuard.kCGEventRightMouseDown if is_right else InputGuard.kCGEventLeftMouseDown
            )
            evt = _app_services.CGEventCreateMouseEvent(
                None, evt_type, pt, mouse_button
            )
            if evt:
                _app_services.CGEventSetIntegerValueField(evt, 1, 1)
                self._post_event(evt)
            if not is_right:
                self._is_mouse_down = True

        if action == "click":
            time.sleep(0.05)

        if action in ("up", "click"):
            evt_type = (
                InputGuard.kCGEventRightMouseUp if is_right else InputGuard.kCGEventLeftMouseUp
            )
            evt = _app_services.CGEventCreateMouseEvent(
                None, evt_type, pt, mouse_button
            )
            if evt:
                _app_services.CGEventSetIntegerValueField(evt, 1, 1)
                self._post_event(evt)
            if not is_right:
                self._is_mouse_down = False

    def mouse_scroll(self, dy: float):
        if not MACOS_CG_AVAILABLE:
            return
        scroll_delta = int(max(-100, min(100, -dy)))
        evt = _app_services.CGEventCreateScrollWheelEvent(None, 0, 1, scroll_delta)
        if evt:
            self._post_event(evt)

    def key_event(
        self,
        key: str,
        action: str,
        modifiers: Optional[list] = None,
        exact_mods: bool = False,
    ):
        if not MACOS_CG_AVAILABLE or not key:
            return
        is_down = action in ("down", "press")

        norm_key = key
        if key in ("Cmd", "Meta"):
            norm_key = "Command"
        elif key == "Ctrl":
            norm_key = "Control"
        elif key == "Esc":
            norm_key = "Escape"
        elif key == "Alt":
            norm_key = "Option"

        if norm_key in ("Command", "Control", "Option", "Shift"):
            if action in ("down", "press"):
                self._active_modifiers.add(norm_key)
            elif action == "up":
                self._active_modifiers.discard(norm_key)

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
            mod_flags |= InputGuard.MODIFIER_MASKS.get(m, 0)

        def _apply_flags(evt):
            if evt and mod_flags:
                cur = _app_services.CGEventGetFlags(evt)
                _app_services.CGEventSetFlags(evt, cur | mod_flags)

        if norm_key in InputGuard.SPECIAL_KEYCODES:
            keycode = InputGuard.SPECIAL_KEYCODES[norm_key]
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

        if len(key) == 1 and key.lower() in InputGuard.ANSI_KEYCODES:
            kc = InputGuard.ANSI_KEYCODES[key.lower()]
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

        if is_down:
            for ch in key:
                ch_low = ch.lower()
                kc = InputGuard.ANSI_KEYCODES.get(ch_low, 0)
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
        if not MACOS_CG_AVAILABLE:
            self._active_modifiers.clear()
            return
        for mod in list(self._active_modifiers):
            kc = InputGuard.SPECIAL_KEYCODES.get(mod)
            if kc is not None:
                evt = _app_services.CGEventCreateKeyboardEvent(None, kc, False)
                if evt:
                    _app_services.CGEventSetFlags(evt, 0)
                    self._post_event(evt)
        self._active_modifiers.clear()

    def paste_text(self, text: str):
        if not MACOS_CG_AVAILABLE or not text:
            return
        import subprocess
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            time.sleep(0.04)

            kc_cmd = InputGuard.SPECIAL_KEYCODES.get("Command", 55)
            kc_v = InputGuard.ANSI_KEYCODES.get("v", 9)
            flag_cmd = InputGuard.MODIFIER_MASKS.get("Command", 0x00100000)

            evt_cmd_d = _app_services.CGEventCreateKeyboardEvent(None, kc_cmd, True)
            if evt_cmd_d:
                self._post_event(evt_cmd_d)
            time.sleep(0.02)

            evt_v_d = _app_services.CGEventCreateKeyboardEvent(None, kc_v, True)
            if evt_v_d:
                _app_services.CGEventSetFlags(evt_v_d, _app_services.CGEventGetFlags(evt_v_d) | flag_cmd)
                self._post_event(evt_v_d)
            time.sleep(0.03)

            evt_v_u = _app_services.CGEventCreateKeyboardEvent(None, kc_v, False)
            if evt_v_u:
                _app_services.CGEventSetFlags(evt_v_u, _app_services.CGEventGetFlags(evt_v_u) | flag_cmd)
                self._post_event(evt_v_u)
            time.sleep(0.02)

            evt_cmd_u = _app_services.CGEventCreateKeyboardEvent(None, kc_cmd, False)
            if evt_cmd_u:
                self._post_event(evt_cmd_u)
        except Exception as ex:
            logger.error(f"Failed to paste text via pbcopy: {ex}")


class LinuxUInputBackend:
    """Linux native evdev.UInput synthetic input backend."""

    def __init__(self, screen_size: Tuple[int, int]):
        self.screen_width, self.screen_height = screen_size
        self._active_modifiers: Set[int] = set()
        self._is_mouse_down = False
        self._mouse_ui = None
        self._kbd_ui = None
        self.available = False

        if not LINUX_EVDEV_AVAILABLE or e is None or UInput is None:
            return

        try:
            # Virtual mouse with absolute coordinates, buttons, and wheel
            mouse_caps = {
                e.EV_KEY: [
                    e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA
                ],
                e.EV_ABS: [
                    (e.ABS_X, AbsInfo(value=0, min=0, max=max(1, self.screen_width), fuzz=0, flat=0, resolution=1)),
                    (e.ABS_Y, AbsInfo(value=0, min=0, max=max(1, self.screen_height), fuzz=0, flat=0, resolution=1)),
                ],
                e.EV_REL: [
                    e.REL_WHEEL, e.REL_HWHEEL
                ],
            }
            self._mouse_ui = UInput(mouse_caps, name="ShrineProjector Virtual Mouse")

            # Virtual keyboard with standard keycodes
            kbd_keys = [
                val for name, val in e.ecodes.items()
                if name.startswith("KEY_") and isinstance(val, int) and val < 256
            ]
            kbd_caps = {
                e.EV_KEY: kbd_keys
            }
            self._kbd_ui = UInput(kbd_caps, name="ShrineProjector Virtual Keyboard")
            self.available = True
            logger.info("Linux evdev UInput virtual mouse and keyboard initialized successfully")
        except Exception as ex:
            logger.warning(f"Could not initialize evdev UInput devices ({ex}). Input will run in mock mode.")
            self._close_devices()
            self.available = False

    def _close_devices(self):
        if self._mouse_ui:
            try:
                self._mouse_ui.close()
            except Exception:
                pass
            self._mouse_ui = None
        if self._kbd_ui:
            try:
                self._kbd_ui.close()
            except Exception:
                pass
            self._kbd_ui = None

    def update_screen_size(self, width: int, height: int):
        self.screen_width = width
        self.screen_height = height

    def mouse_move(self, x: float, y: float, is_drag: bool = False):
        if not self.available or not self._mouse_ui:
            return
        ix = int(max(0, min(x, self.screen_width)))
        iy = int(max(0, min(y, self.screen_height)))
        self._mouse_ui.write(e.EV_ABS, e.ABS_X, ix)
        self._mouse_ui.write(e.EV_ABS, e.ABS_Y, iy)
        self._mouse_ui.syn()

    def mouse_click(self, x: float, y: float, button: str, action: str):
        if not self.available or not self._mouse_ui:
            return
        self.mouse_move(x, y)

        btn_code = e.BTN_RIGHT if button == "right" else (e.BTN_MIDDLE if button == "middle" else e.BTN_LEFT)

        if action in ("down", "click"):
            self._mouse_ui.write(e.EV_KEY, btn_code, 1)
            self._mouse_ui.syn()
            if button != "right":
                self._is_mouse_down = True

        if action == "click":
            time.sleep(0.03)

        if action in ("up", "click"):
            self._mouse_ui.write(e.EV_KEY, btn_code, 0)
            self._mouse_ui.syn()
            if button != "right":
                self._is_mouse_down = False

    def mouse_scroll(self, dy: float):
        if not self.available or not self._mouse_ui:
            return
        wheel_val = -1 if dy > 0 else (1 if dy < 0 else 0)
        if wheel_val != 0:
            self._mouse_ui.write(e.EV_REL, e.REL_WHEEL, wheel_val)
            self._mouse_ui.syn()

    def key_event(
        self,
        key: str,
        action: str,
        modifiers: Optional[list] = None,
        exact_mods: bool = False,
    ):
        if not self.available or not self._kbd_ui or not key:
            return

        ecode_name = WEB_TO_EVDEV_KEY_MAP.get(key) or WEB_TO_EVDEV_KEY_MAP.get(key.lower())
        keycode = getattr(e, ecode_name, None) if ecode_name else getattr(e, f"KEY_{key.upper()}", None)

        if keycode is None:
            logger.debug(f"Unmapped Linux key event: {key}")
            return

        is_down = action in ("down", "press")

        # Track modifier state
        if key in ("Shift", "ShiftLeft", "ShiftRight", "Control", "Ctrl", "ControlLeft", "ControlRight", "Alt", "Option", "AltLeft", "AltRight", "Meta", "Command", "Cmd", "MetaLeft", "MetaRight"):
            if is_down:
                self._active_modifiers.add(keycode)
            elif action == "up":
                self._active_modifiers.discard(keycode)

        # Handle active modifiers
        if modifiers:
            for m in modifiers:
                m_name = WEB_TO_EVDEV_KEY_MAP.get(m)
                m_code = getattr(e, m_name, None) if m_name else None
                if m_code:
                    self._kbd_ui.write(e.EV_KEY, m_code, 1)
            self._kbd_ui.syn()

        if action in ("click", "press"):
            self._kbd_ui.write(e.EV_KEY, keycode, 1)
            self._kbd_ui.syn()
            time.sleep(0.02)
            self._kbd_ui.write(e.EV_KEY, keycode, 0)
            self._kbd_ui.syn()
        else:
            self._kbd_ui.write(e.EV_KEY, keycode, 1 if is_down else 0)
            self._kbd_ui.syn()

        if modifiers and action in ("click", "press"):
            for m in modifiers:
                m_name = WEB_TO_EVDEV_KEY_MAP.get(m)
                m_code = getattr(e, m_name, None) if m_name else None
                if m_code and m_code not in self._active_modifiers:
                    self._kbd_ui.write(e.EV_KEY, m_code, 0)
            self._kbd_ui.syn()

    def release_all_modifiers(self):
        if not self.available or not self._kbd_ui:
            self._active_modifiers.clear()
            return
        all_mods = set(self._active_modifiers)
        for m_name in ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_LEFTMETA", "KEY_RIGHTMETA"):
            code = getattr(e, m_name, None)
            if code is not None:
                all_mods.add(code)

        for code in all_mods:
            self._kbd_ui.write(e.EV_KEY, code, 0)
        self._kbd_ui.syn()
        self._active_modifiers.clear()

    def paste_text(self, text: str):
        if not self.available or not text:
            return
        import shutil, subprocess
        if shutil.which("wl-copy"):
            try:
                p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
                p.communicate(text.encode("utf-8"), timeout=1.0)
            except Exception as ex:
                logger.debug(f"wl-copy failed: {ex}")
        elif shutil.which("xclip"):
            try:
                p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
                p.communicate(text.encode("utf-8"), timeout=1.0)
            except Exception as ex:
                logger.debug(f"xclip failed: {ex}")

        # Trigger Ctrl+V
        ctrl_code = getattr(e, "KEY_LEFTCTRL", 29)
        v_code = getattr(e, "KEY_V", 47)
        if self._kbd_ui:
            self._kbd_ui.write(e.EV_KEY, ctrl_code, 1)
            self._kbd_ui.syn()
            time.sleep(0.02)
            self._kbd_ui.write(e.EV_KEY, v_code, 1)
            self._kbd_ui.syn()
            time.sleep(0.02)
            self._kbd_ui.write(e.EV_KEY, v_code, 0)
            self._kbd_ui.syn()
            time.sleep(0.02)
            self._kbd_ui.write(e.EV_KEY, ctrl_code, 0)
            self._kbd_ui.syn()


class DummyInputBackend:
    """Mock fallback input backend for testing or unsupported headless environments."""

    def __init__(self, screen_size: Tuple[int, int] = (1920, 1080)):
        self.screen_width, self.screen_height = screen_size

    def update_screen_size(self, width: int, height: int):
        self.screen_width = width
        self.screen_height = height

    def mouse_move(self, x: float, y: float, is_drag: bool = False):
        pass

    def mouse_click(self, x: float, y: float, button: str, action: str):
        pass

    def mouse_scroll(self, dy: float):
        pass

    def key_event(
        self,
        key: str,
        action: str,
        modifiers: Optional[list] = None,
        exact_mods: bool = False,
    ):
        pass

    def release_all_modifiers(self):
        pass

    def paste_text(self, text: str):
        pass


# ── Unified InputController ──

class InputController:
    """
    Unified multi-platform synthetic input controller.
    Dispatches to:
    - MacOSInputBackend (CoreGraphics) on macOS
    - LinuxUInputBackend (evdev.UInput) on Linux
    - DummyInputBackend if running headless or missing libraries
    """

    def __init__(self, screen_size: Optional[Tuple[int, int]] = None):
        if screen_size:
            self.screen_width, self.screen_height = screen_size
        else:
            self.screen_width, self.screen_height = get_screen_resolution()

        self.backend = None
        if sys.platform == "darwin" and MACOS_CG_AVAILABLE:
            try:
                self.backend = MacOSInputBackend((self.screen_width, self.screen_height))
                logger.info("InputController initialized with macOS CoreGraphics backend")
            except Exception as ex:
                logger.warning(f"Failed to initialize macOS CoreGraphics backend: {ex}")

        elif sys.platform == "linux" and LINUX_EVDEV_AVAILABLE:
            try:
                linux_backend = LinuxUInputBackend((self.screen_width, self.screen_height))
                if getattr(linux_backend, "available", False):
                    self.backend = linux_backend
                    logger.info("InputController initialized with Linux evdev.UInput backend")
                else:
                    self.backend = None
            except Exception as ex:
                logger.warning(f"Failed to initialize Linux evdev backend: {ex}")

        if self.backend is None:
            self.backend = DummyInputBackend((self.screen_width, self.screen_height))
            logger.info("InputController initialized with Dummy (mock) backend")

    def update_screen_size(self, width: int, height: int):
        self.screen_width = width
        self.screen_height = height
        self.backend.update_screen_size(width, height)

    def mouse_move(self, x: float, y: float, is_drag: bool = False):
        self.backend.mouse_move(x, y, is_drag=is_drag)

    def move(self, x: float, y: float, is_drag: bool = False):
        self.mouse_move(x, y, is_drag=is_drag)

    def mouse_click(self, x: float, y: float, button: str = "left", action: str = "click"):
        self.backend.mouse_click(x, y, button, action)

    def click(self, x: float, y: float, button: str = "left", action: str = "click"):
        self.mouse_click(x, y, button, action)

    def mouse_drag(self, x: float, y: float):
        self.mouse_move(x, y, is_drag=True)

    def drag(self, x: float, y: float):
        self.mouse_drag(x, y)

    def mouse_scroll(self, dy: float):
        self.backend.mouse_scroll(dy)

    def scroll(self, dy: float):
        self.mouse_scroll(dy)

    def key_event(
        self,
        key: str,
        action: str = "down",
        modifiers: Optional[list] = None,
        exact_mods: bool = False,
    ):
        self.backend.key_event(key, action, modifiers=modifiers, exact_mods=exact_mods)

    def release_all_modifiers(self):
        self.backend.release_all_modifiers()

    def paste_text(self, text: str):
        self.backend.paste_text(text)


class InputGuard:
    """
    Security gatekeeper for input injection.
    Ensures input is rate-limited, bounds-checked, sanitized, and only processed
    when client is in interactive mode.
    """
    # macOS CG Event Constants (preserved for backwards-compatibility)
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
        controller: Optional[InputController] = None,
    ):
        self.max_rate = max_rate_per_sec
        if screen_size:
            self.screen_width, self.screen_height = screen_size
        else:
            self.screen_width, self.screen_height = get_screen_resolution()
        self.max_clipboard_chars = max_clipboard_chars
        self._last_event_time = 0.0
        self._event_count = 0
        self._is_mouse_down = False
        self._active_modifiers: set = set()
        self.controller = controller or InputController(screen_size=(self.screen_width, self.screen_height))

    def update_screen_size(self, width: int, height: int):
        """Update active screen dimensions for coordinate clamping"""
        if width > 0 and height > 0:
            self.screen_width = width
            self.screen_height = height
            self.controller.update_screen_size(width, height)
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
        """Legacy helper for posting CGEvent on macOS"""
        if hasattr(self.controller.backend, "_post_event"):
            self.controller.backend._post_event(evt)

    def handle_mouse_move(self, x: float, y: float, is_interactive: bool):
        if not is_interactive:
            return
        if not self.check_rate_limit():
            return

        cx, cy = self.clamp_coordinates(x, y)
        self.controller.mouse_move(cx, cy, is_drag=self._is_mouse_down)

    def handle_mouse_click(
        self, x: float, y: float, button: str, action: str, is_interactive: bool
    ):
        if not is_interactive:
            logger.warning("[INPUT] Click ignored: client not in interactive mode")
            return
        if not self.check_rate_limit():
            return

        cx, cy = self.clamp_coordinates(x, y)
        is_right = button == "right"
        logger.info(f"[INPUT CLICK] ({cx:.1f}, {cy:.1f}) btn={button} act={action}")

        if action in ("down", "click"):
            if not is_right:
                self._is_mouse_down = True

        self.controller.mouse_click(cx, cy, button, action)

        if action in ("up", "click"):
            if not is_right:
                self._is_mouse_down = False

    def handle_mouse_scroll(self, dy: float, is_interactive: bool):
        """Scroll wheel injection"""
        if not is_interactive:
            return
        if not self.check_rate_limit():
            return
        self.controller.mouse_scroll(dy)

    def handle_key_event(
        self,
        key: str,
        action: str,
        is_interactive: bool,
        modifiers: Optional[list] = None,
        exact_mods: bool = False,
    ):
        """Synthesizes keyboard events across platforms"""
        if not is_interactive:
            logger.warning("[INPUT] Key ignored: client not in interactive mode")
            return
        if not key or not self.check_rate_limit():
            return

        logger.info(f"[INPUT KEY] key={key!r} act={action} mods={modifiers}")
        norm_key = key
        if key in ("Cmd", "Meta"):
            norm_key = "Command"
        elif key == "Ctrl":
            norm_key = "Control"
        elif key == "Esc":
            norm_key = "Escape"
        elif key == "Alt":
            norm_key = "Option"

        if norm_key in ("Command", "Control", "Option", "Shift"):
            if action in ("down", "press"):
                self._active_modifiers.add(norm_key)
            elif action == "up":
                self._active_modifiers.discard(norm_key)

        self.controller.key_event(key, action, modifiers=modifiers, exact_mods=exact_mods)

    def release_all_modifiers(self):
        """Release all held modifiers across backends"""
        self.controller.release_all_modifiers()
        self._active_modifiers.clear()

    def handle_paste_text(self, text: str, is_interactive: bool):
        """Safe sanitized clipboard paste across platforms"""
        if not is_interactive or not text:
            return
        sanitized = self.sanitize_clipboard_text(text)
        if not sanitized:
            return
        logger.info(f"[INPUT PASTE] {len(sanitized)} chars")
        self.controller.paste_text(sanitized)

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
