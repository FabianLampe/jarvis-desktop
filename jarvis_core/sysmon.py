"""System-Monitor: CPU/RAM via psutil, VRAM über den Plattformadapter (nvidia-smi nur unter Windows).

Auf dem dev-PC (gpu_monitor: false) bleibt VRAM None → Statusleiste zeigt „VRAM —".
Schlägt nvidia-smi einmal mit „nicht gefunden" fehl, wird es nicht erneut versucht.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import psutil

from .platform import current as current_platform

log = logging.getLogger("jarvis.sysmon")


class SystemMonitor:
    def __init__(self, gpu_monitor: bool, platform=None):
        self._platform = platform or current_platform()
        self._misses = 0
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
        """Dedizierter GPU-Speicher über den Plattformadapter (Windows: nvidia-smi;
        macOS: Unified Memory ⇒ keine eigene VRAM-Anzeige)."""
        v = self._platform.gpu_status()
        if v is None:
            self._misses += 1
            if self._misses >= 3:
                log.info("Kein GPU-Speicher abfragbar — VRAM-Anzeige deaktiviert")
                self._gpu = False
        else:
            self._misses = 0
        return v
