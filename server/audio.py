"""
ShrineProjector Audio Subsystem
Ultra-Low Latency System Audio Broadcast Engine
Captures system audio via BlackHole 2ch loopback driver as native 44.1kHz 16-bit stereo PCM.
Capture utama lewat PortAudio (sounddevice): ring buffer CoreAudio, gak ngedrop paket.
ffmpeg avfoundation cuma cadangan — dia cuma nyimpen 1 buffer audio, jadi ngedrop ~3-4%
paket kalau Mac sibuk (terukur 18 Nov: 0,96x realtime vs PortAudio 1,002x).
Streams directly to Web Audio API clients over WebSocket for sub-40ms latency.
Zero-CPU Idle: Automatically terminates FFmpeg process when 0 viewers are listening.
"""

import os
import shutil
import asyncio
import logging
import subprocess
from typing import Set, Optional, AsyncGenerator

logger = logging.getLogger("shrine.audio")


class AudioEngine:
    def __init__(self, sample_rate: int = 44100, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._subscribers: Set[asyncio.Queue] = set()
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._sd_stream = None  # PortAudio RawInputStream (jalur utama)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._switch_audio_cmd = shutil.which("SwitchAudioSource") or "/opt/homebrew/bin/SwitchAudioSource"

    def ensure_multi_output(self):
        """Ensure audio output is routed to Multi-Output Device so BlackHole captures sound"""
        if self._switch_audio_cmd and os.path.exists(self._switch_audio_cmd):
            try:
                curr = subprocess.check_output([self._switch_audio_cmd, "-c"], text=True).strip()
                if curr != "Multi-Output Device":
                    all_devs = subprocess.check_output([self._switch_audio_cmd, "-a"], text=True)
                    if "Multi-Output Device" in all_devs:
                        subprocess.run([self._switch_audio_cmd, "-s", "Multi-Output Device"], check=True)
                        logger.info("Switched output device to Multi-Output Device for audio capture")
            except Exception as e:
                logger.warning(f"Failed to verify/switch audio output device: {e}")

    def _broadcast(self, chunk: bytes):
        """Kirim 1 chunk PCM ke semua subscriber; buang yang paling lama kalau antrian penuh"""
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    def _start_portaudio(self) -> bool:
        """Capture BlackHole lewat PortAudio. Callback jalan di thread audio CoreAudio."""
        try:
            import sounddevice as sd
        except Exception as e:
            logger.warning(f"[AUDIO] sounddevice gak ada ({e}), pakai ffmpeg")
            return False
        try:
            dev = next(
                i for i, d in enumerate(sd.query_devices())
                if "BlackHole 2ch" in d["name"] and d["max_input_channels"] >= self.channels
            )
        except StopIteration:
            logger.warning("[AUDIO] Device BlackHole 2ch gak ketemu di PortAudio, pakai ffmpeg")
            return False

        loop = self._loop

        def _cb(indata, frames, time_info, status):
            if status.input_overflow:
                logger.debug("[AUDIO] PortAudio input overflow")
            loop.call_soon_threadsafe(self._broadcast, bytes(indata))

        try:
            # 1764 frame = 40ms @44.1kHz = 7056 byte, sama persis dengan format chunk lama
            self._sd_stream = sd.RawInputStream(
                device=dev, samplerate=self.sample_rate, channels=self.channels,
                dtype="int16", blocksize=1764, latency="high", callback=_cb,
            )
            self._sd_stream.start()
            logger.info(f"[AUDIO] PortAudio capture aktif di BlackHole 2ch ({self.sample_rate}Hz stereo)")
            return True
        except Exception as e:
            logger.warning(f"[AUDIO] PortAudio gagal start ({e}), pakai ffmpeg")
            self._sd_stream = None
            return False

    async def _start_capture(self):
        """Mulai capture: PortAudio dulu, ffmpeg avfoundation kalau PortAudio gagal"""
        if self._process is not None or self._sd_stream is not None:
            return

        self.ensure_multi_output()
        self._loop = asyncio.get_running_loop()
        if self._start_portaudio():
            return
        logger.info(f"[AUDIO] Spawning FFmpeg raw PCM audio capture on BlackHole 2ch ({self.sample_rate}Hz stereo)...")

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "avfoundation",
            "-i", ":BlackHole 2ch",
            "-f", "s16le",
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            "-flush_packets", "1",
            "pipe:1"
        ]

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            self._reader_task = asyncio.create_task(self._reader_loop())
            logger.info(f"[AUDIO] FFmpeg raw PCM capture active (PID: {self._process.pid})")
        except Exception as e:
            logger.error(f"[AUDIO] Failed to start FFmpeg audio process: {e}")
            self._process = None

    async def _stop_capture(self):
        """Stops the FFmpeg capture process to return to 0% CPU idle without deadlocks"""
        logger.info("[AUDIO] 0 listeners active. Stopping FFmpeg audio capture...")
        if self._sd_stream is not None:
            stream, self._sd_stream = self._sd_stream, None
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        proc = self._process
        self._process = None

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            self._reader_task = None

        if proc:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except Exception:
                pass
        logger.info("[AUDIO] Audio capture idle (0% CPU / 0% RAM)")

    async def _reader_loop(self):
        """
        Reads raw PCM chunks from FFmpeg pipe and broadcasts to queues.
        7056 bytes = 1764 stereo frames = exactly 40.0ms of native 44.1kHz audio.
        readexactly guarantees fixed-size frames with no partial fragment jitter.
        """
        chunk_size = 7056
        try:
            while self._process and self._process.stdout:
                try:
                    chunk = await self._process.stdout.readexactly(chunk_size)
                except asyncio.IncompleteReadError as e:
                    if e.partial:
                        chunk = e.partial
                    else:
                        break
                if not chunk:
                    break
                self._broadcast(chunk)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[AUDIO] Reader loop error: {e}")

    async def subscribe(self) -> AsyncGenerator[bytes, None]:
        """Async generator yielding 40ms raw PCM chunks for WebSocket clients"""
        q = asyncio.Queue(maxsize=50)
        async with self._lock:
            self._subscribers.add(q)
            if len(self._subscribers) == 1:
                await self._start_capture()

        try:
            while True:
                chunk = await q.get()
                yield chunk
        finally:
            async with self._lock:
                self._subscribers.discard(q)
                if len(self._subscribers) == 0:
                    await self._stop_capture()


audio_engine = AudioEngine()
