"""
Remote targets: tombol di HUD buat nyalain/matiin sesi remote desktop ke mesin LAIN
(mis. PC Linux lewat Guacamole/noVNC), tanpa ShrineProjector ngurus protokolnya sendiri.

Tiap target didefinisikan di config lokal (`remote_targets`), contoh:

  "remote_targets": {
    "linuxbox": {
      "label": "Linux box",
      "start": ["/usr/bin/python3", "/path/to/session-holder.py"],
      "ready_check": "127.0.0.1:18080",
      "url": "https://my-mac.tailnet.ts.net:8767/"
    }
  }

`start` = perintah yang memegang sesi selama jalan (nyalain mesin, buka terowongan, dll) dan
beres-beres sendiri saat dapat SIGTERM. ShrineProjector cuma start/stop prosesnya dan ngecek
`ready_check` (host:port TCP) buat tahu kapan `url` siap dibuka.
"""

import logging
import os
import signal
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("shrine.remote")


class RemoteTargets:
    def __init__(self, config: Dict[str, Any], state_dir: Path):
        self.targets: Dict[str, Dict[str, Any]] = config.get("remote_targets", {}) or {}
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._procs: Dict[str, subprocess.Popen] = {}  # anak proses kita sendiri (biar bisa di-reap, gak jadi zombie)

    # pid disimpan di file supaya sesi yang jalan tetap kepegang kalau daemon restart
    def _pidfile(self, name: str) -> Path:
        return self.state_dir / f"remote-{name}.pid"

    def _pid(self, name: str) -> Optional[int]:
        proc = self._procs.get(name)
        if proc is not None:
            if proc.poll() is None:
                return proc.pid
            self._procs.pop(name, None)
            self._pidfile(name).unlink(missing_ok=True)
            return None
        try:
            pid = int(self._pidfile(name).read_text().strip())
            os.kill(pid, 0)
            return pid
        except (OSError, ValueError):
            return None

    @staticmethod
    def _port_open(spec: Optional[str]) -> bool:
        if not spec:
            return False
        host, _, port = spec.rpartition(":")
        try:
            with socket.create_connection((host or "127.0.0.1", int(port)), timeout=0.5):
                return True
        except (OSError, ValueError):
            return False

    def status(self, name: str) -> Dict[str, Any]:
        t = self.targets[name]
        running = self._pid(name) is not None
        return {
            "name": name,
            "label": t.get("label", name),
            "running": running,
            "ready": running and self._port_open(t.get("ready_check")),
            "url": t.get("url", ""),
        }

    def list(self):
        return [self.status(n) for n in self.targets]

    def start(self, name: str) -> Dict[str, Any]:
        t = self.targets[name]
        if self._pid(name) is None:
            argv = t.get("start")
            if not argv or not isinstance(argv, list):
                raise ValueError(f"remote target '{name}' has no start command")
            log = open(self.state_dir / f"remote-{name}.log", "ab")
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            log.close()
            self._procs[name] = proc
            self._pidfile(name).write_text(str(proc.pid))
            logger.info(f"Remote target '{name}' started (pid {proc.pid})")
        return self.status(name)

    def stop(self, name: str) -> Dict[str, Any]:
        pid = self._pid(name)
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGTERM)  # holder beres-beres sendiri saat SIGTERM
            except OSError:
                os.kill(pid, signal.SIGTERM)
            logger.info(f"Remote target '{name}' stop requested (pid {pid})")
        return self.status(name)
