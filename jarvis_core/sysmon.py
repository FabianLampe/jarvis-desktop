"""System-Monitor: CPU/RAM via psutil, VRAM via nvidia-smi (nur home-Profil).

Auf dem dev-PC (gpu_monitor: false) bleibt VRAM None → Statusleiste zeigt „VRAM —".
Schlägt nvidia-smi einmal mit „nicht gefunden" fehl, wird es nicht erneut versucht.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any, Optional

import psutil

log = logging.getLogger("jarvis.sysmon")

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class SystemMonitor:
    def __init__(self, gpu_monitor: bool):
        self._gpu = gpu_monitor
        psutil.cpu_percent(interval=None)  # Erstaufruf liefert sonst 0.0

    def status(self) -> dict[str, Any]:
        ram = psutil.virtual_memory()
        out: dict[str, Any] = {
            "cpu": round(psutil.cpu_percent(interval=None)),
            "ram": round(ram.percent),
            "ram_used_gb": round(ram.used / 2**30, 1),
            "ram_total_gb": round(ram.total / 2**30, 1),
            "vram": None,
            "vram_used_mb": None,
            "vram_total_mb": None,
        }
        if self._gpu and (v := self._vram()) is not None:
            used, total = v
            out["vram_used_mb"] = used
            out["vram_total_mb"] = total
            out["vram"] = round(used / total * 100) if total else None
        return out

    def _vram(self) -> Optional[tuple[int, int]]:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
                creationflags=_CREATE_NO_WINDOW,
            )
            if r.returncode != 0:
                return None
            used, total = (int(x.strip()) for x in r.stdout.splitlines()[0].split(","))
            return used, total
        except FileNotFoundError:
            log.info("nvidia-smi nicht gefunden — VRAM-Anzeige deaktiviert")
            self._gpu = False
            return None
        except (subprocess.TimeoutExpired, ValueError, IndexError) as exc:
            log.debug("nvidia-smi-Lesefehler: %s", exc)
            return None
