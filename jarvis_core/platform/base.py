"""Gemeinsame Schnittstelle für betriebssystemspezifische Funktionen.

Der Rest von jarvis_core kennt nur PlatformAdapter; macOS und Windows liegen in
getrennten Adaptern (mac.py, windows.py). Alle Programmaufrufe sind Argumentlisten.
"""

from __future__ import annotations

import os
import platform as _platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


class PlatformError(RuntimeError):
    """Funktion auf dieser Plattform nicht verfügbar oder fehlgeschlagen."""


class PlatformAdapter:
    name = "other"          # macos | windows | other
    gui = "qt"              # pywebview-Backend: cocoa | edgechromium | qt
    shell_label = "sh"

    # ------------------------------------------------------------ Pfade

    def data_dir(self) -> Path:
        override = os.environ.get("JARVIS_DATA_DIR")
        base = Path(override).expanduser() if override else self._default_data_dir()
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _default_data_dir(self) -> Path:
        return Path.home() / ".local" / "share" / "Jarvis"

    def default_workspace(self) -> Path:
        return Path.home() / "Jarvis"

    def protected_paths(self) -> list[Path]:
        """Pfade, die Jarvis nie lesen oder ändern darf (Schlüssel, Zugangsdaten)."""
        home = Path.home()
        return [home / ".ssh", home / ".gnupg", home / ".aws"]

    # ----------------------------------------------------- Prozesse

    def popen_kwargs(self) -> dict[str, Any]:
        """Eigene Prozessgruppe, damit Abbruch auch Kindprozesse trifft."""
        return {"start_new_session": True}

    def terminate(self, proc: subprocess.Popen, force: bool = False) -> None:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    def shell_argv(self, command: str) -> list[str]:
        return ["/bin/sh", "-c", command]

    # ------------------------------------------------ Programme/Systeme

    def open_target(self, target: str) -> list[str]:
        """argv zum Öffnen einer Datei, URL oder App (Name)."""
        raise PlatformError("Programmstart auf dieser Plattform nicht unterstützt")

    def notify_argv(self, title: str, text: str) -> Optional[list[str]]:
        return None

    def notify(self, title: str, text: str) -> bool:
        argv = self.notify_argv(title, text)
        if not argv:
            return False
        try:
            return subprocess.run(argv, capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def gpu_status(self) -> Optional[tuple[int, int]]:
        """(belegt_MB, gesamt_MB) dedizierter GPU-Speicher oder None."""
        return None

    def screenshot(self, path: Path) -> None:
        raise PlatformError("Screenshots auf dieser Plattform nicht unterstützt")

    def ocr_image(self, path: Path, languages: tuple[str, ...] = ("de-DE", "en-US")) -> str:
        raise PlatformError("Bildschirm-OCR auf dieser Plattform nicht eingerichtet")

    def speak_argv(self, text: str, voice: Optional[str] = None,
                   rate: Optional[int] = None) -> Optional[list[str]]:
        """argv für die eingebaute Systemstimme (Offline-Ausweichpfad der Sprachausgabe)."""
        return None

    def clipboard_get(self) -> str:
        raise PlatformError("Zwischenablage nicht unterstützt")

    def clipboard_set(self, text: str) -> None:
        raise PlatformError("Zwischenablage nicht unterstützt")

    def get_volume(self) -> Optional[int]:
        return None

    def set_volume(self, percent: int) -> None:
        raise PlatformError("Lautstärkesteuerung nicht unterstützt")

    def chrome_candidates(self) -> list[str]:
        return []

    def permission_hints(self) -> dict[str, str]:
        return {}

    # -------------------------------------------------- Systeminfo

    def system_info(self) -> dict[str, Any]:
        import psutil
        info: dict[str, Any] = {
            "os": self.name,
            "os_version": _platform.platform(),
            "arch": _platform.machine(),
            "cpu_cores": psutil.cpu_count(logical=True),
            "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
            "ram_free_gb": round(psutil.virtual_memory().available / 2**30, 1),
            "python": sys.version.split()[0],
            "node": _tool_version(["node", "--version"]),
            "ollama": _tool_version(["ollama", "--version"], strip="ollama version is "),
        }
        return info


def _tool_version(argv: list[str], strip: str = "") -> Optional[str]:
    if not shutil.which(argv[0]):
        return None
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or r.stderr).strip().splitlines()
    return out[0].replace(strip, "").strip() if out else None
