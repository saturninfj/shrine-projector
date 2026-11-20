"""
ShrineProjector Screen Capture Engine
Hardware-accelerated continuous frame capture with zero-lag atomic buffer broadcasting
Supports macOS (AVFoundation) and Linux (wf-recorder / ffmpeg x11grab/kmsgrab).
Supports dynamic activate/deactivate to completely release RAM & CPU when idle.
"""

import os
import shutil
import asyncio
import logging
import subprocess
import sys
from typing import Optional, Tuple, AsyncGenerator, Dict, Any

logger = logging.getLogger("shrine.capture")


class ScreenCaptureEngine:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        disp_cfg = config.get("display", {})
        self.fps = disp_cfg.get("fps", 30)
        self.quality = disp_cfg.get("quality", 75)
        # Map 1-100 quality to ffmpeg mjpeg q:v (2-31, lower is better)
        self.ffmpeg_q = max(2, min(31, int(31 - (self.quality / 100.0) * 29)))
        self.screen_index = disp_cfg.get("screen_index", 0)
        self.width = disp_cfg.get("width", 1920)
        self.height = disp_cfg.get("height", 1080)

        if sys.platform == "darwin":
            try:
                from .input import get_macos_screen_resolution
                w, h = get_macos_screen_resolution()
                if w > 0 and h > 0:
                    self.width, self.height = w, h
                    logger.info(f"ScreenCaptureEngine dynamically detected native resolution: {w}x{h}")
            except Exception as e:
                logger.warning(f"Failed to auto-detect native resolution: {e}")
        elif sys.platform == "linux":
            try:
                from .input import get_linux_screen_resolution
                w, h = get_linux_screen_resolution()
                if w > 0 and h > 0:
                    self.width, self.height = w, h
                    logger.info(f"ScreenCaptureEngine dynamically detected Linux resolution: {w}x{h}")
            except Exception as e:
                logger.warning(f"Failed to auto-detect Linux resolution: {e}")

        # Profil kualitas stream. "kafe" = hemat bandwidth buat WiFi kafe/seluler.
        # Ukuran canvas di client tetap resolusi asli (koordinat mouse gak berubah),
        # cuma frame yang dikirim yang dikecilin.
        self.profiles: Dict[str, Dict[str, Any]] = {
            "full": {"fps": self.fps, "q": self.ffmpeg_q, "width": None},
            "kafe": {"fps": 12, "q": 22, "width": 1280},
        }
        self.profiles.update(config.get("profiles", {}))
        self.profile = disp_cfg.get("default_profile", "full")
        if self.profile not in self.profiles:
            self.profile = "full"

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._running = False
        self._latest_frame: Optional[bytes] = None
        self._subscribers: set = set()
        self._worker_task: Optional[asyncio.Task] = None

    @property
    def is_active(self) -> bool:
        return self._running

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def resolution(self) -> Tuple[int, int]:
        return self.width, self.height

    @property
    def latest_frame(self) -> Optional[bytes]:
        return self._latest_frame

    async def start(self):
        """Start the capture worker (Activate)"""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._capture_loop())
        logger.info(f"Screen capture engine activated at ~{self.fps} FPS")

    async def stop(self):
        """Stop the capture worker and kill capture process (Deactivate / 0% RAM)"""
        if not self._running:
            return
        self._running = False

        for q in list(self._subscribers):
            try:
                q.put_nowait(b"")
            except Exception:
                pass

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass
            self._worker_task = None

        if self._proc:
            try:
                self._proc.kill()
                await self._proc.wait()
            except Exception:
                pass
            self._proc = None

        self._latest_frame = None
        logger.info("Screen capture engine deactivated (FFmpeg/wf-recorder terminated, RAM freed)")

    async def set_profile(self, name: str) -> bool:
        """Ganti profil kualitas. Kalau capture lagi jalan, worker di-restart pakai profil baru."""
        if name not in self.profiles:
            raise ValueError(f"profil gak dikenal: {name}")
        if name == self.profile:
            return False
        self.profile = name
        logger.info(f"Profil stream diganti ke '{name}': {self.profiles[name]}")
        if self._running:
            await self.stop()
            await self.start()
        return True

    async def activate(self) -> bool:
        """Alias for start"""
        if not self._running:
            await self.start()
            return True
        return False

    async def deactivate(self) -> bool:
        """Alias for stop"""
        if self._running:
            await self.stop()
            return True
        return False

    async def _capture_loop(self):
        """Continuous frame reader using atomic boundary detection"""
        while self._running:
            try:
                prof = self.profiles.get(self.profile, self.profiles["full"])
                fps_val = str(prof.get("fps", self.fps))
                q_val = str(prof.get("q", self.ffmpeg_q))

                if sys.platform == "darwin":
                    # Explicit AVFoundation screen device name to prevent picking up USB cameras (e.g. Osmo Pocket, webcams)
                    screen_dev = str(self.screen_index)
                    if isinstance(self.screen_index, int) or str(self.screen_index).isdigit():
                        screen_dev = f"Capture screen {self.screen_index}"
                    else:
                        screen_dev = str(self.screen_index)

                    cmd = [
                        "ffmpeg",
                        "-f", "avfoundation",
                        "-r", fps_val,
                        "-capture_cursor", "1",
                        "-i", screen_dev,
                    ]
                    if prof.get("width"):
                        # filter fps ngunci frame rate keluar (avfoundation -r cuma petunjuk, bisa bablas)
                        cmd += ["-vf", f"fps={int(prof.get('fps', self.fps))},scale={int(prof['width'])}:-2:flags=fast_bilinear"]
                    cmd += [
                        "-f", "mjpeg",
                        "-q:v", q_val,
                        "-flush_packets", "1",
                        "-an",
                        "pipe:1"
                    ]
                elif sys.platform == "linux":
                    if shutil.which("wf-recorder"):
                        # Wayland / wlroots native capture via wf-recorder
                        cmd = [
                            "wf-recorder",
                            "-y",
                            "-D",
                            "-r", fps_val,
                            "-m", "mjpeg",
                            "-c", "mjpeg",
                        ]
                        if prof.get("width"):
                            cmd += ["-F", f"scale={int(prof['width'])}:-2"]
                        cmd += ["-f", "/dev/stdout"]
                    else:
                        # Fallback to ffmpeg with x11grab or kmsgrab
                        display_str = os.environ.get("DISPLAY")
                        if not display_str and os.environ.get("WAYLAND_DISPLAY"):
                            cmd = [
                                "ffmpeg",
                                "-f", "kmsgrab",
                                "-framerate", fps_val,
                                "-i", "-",
                            ]
                        else:
                            cmd = [
                                "ffmpeg",
                                "-f", "x11grab",
                                "-framerate", fps_val,
                                "-draw_mouse", "1",
                                "-i", display_str or ":0.0",
                            ]
                        if prof.get("width"):
                            cmd += ["-vf", f"fps={int(prof.get('fps', self.fps))},scale={int(prof['width'])}:-2:flags=fast_bilinear"]
                        cmd += [
                            "-f", "mjpeg",
                            "-q:v", q_val,
                            "-flush_packets", "1",
                            "-an",
                            "pipe:1"
                        ]
                else:
                    logger.error(f"Unsupported capture platform: {sys.platform}")
                    await asyncio.sleep(2)
                    continue

                logger.info(f"Launching capture worker: {' '.join(cmd)}")
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )

                buf = bytearray()
                while self._running and self._proc.returncode is None:
                    chunk = await self._proc.stdout.read(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)

                    # Scan for JPEG boundaries: SOI (0xFF 0xD8) to EOI (0xFF 0xD9)
                    while True:
                        soi = buf.find(b"\xff\xd8")
                        if soi == -1:
                            buf.clear()
                            break
                        eoi = buf.find(b"\xff\xd9", soi)
                        if eoi == -1:
                            break
                        
                        frame = bytes(buf[soi : eoi + 2])
                        del buf[: eoi + 2]

                        # Update latest frame and notify waiting subscribers
                        self._latest_frame = frame
                        for q in list(self._subscribers):
                            if q.full():
                                try:
                                    q.get_nowait()
                                except Exception:
                                    pass
                            try:
                                q.put_nowait(frame)
                            except Exception:
                                pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Capture loop error: {e}, restarting in 2 seconds...")
                    await asyncio.sleep(2)
            finally:
                if self._proc:
                    try:
                        self._proc.kill()
                        await self._proc.wait()
                    except Exception:
                        pass
                    self._proc = None

    async def subscribe(self) -> AsyncGenerator[bytes, None]:
        """
        Yields frames as they arrive.
        Drops frames automatically if consumer is slow to maintain zero latency.
        """
        q = asyncio.Queue(maxsize=1)
        if self._latest_frame:
            q.put_nowait(self._latest_frame)
        self._subscribers.add(q)
        try:
            while self._running:
                frame = await q.get()
                if frame:
                    yield frame
        finally:
            self._subscribers.discard(q)
