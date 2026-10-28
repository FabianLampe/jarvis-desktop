"""Windows-Adapter (bisheriges Verhalten). Auf dem Mac nur per Mock getestet."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from .base import PlatformAdapter, PlatformError

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200


class WindowsAdapter(PlatformAdapter):
    name = "windows"
    gui = "edgechromium"
    shell_label = "cmd"

    def _default_data_dir(self) -> Path:
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "Jarvis"

    def protected_paths(self) -> list[Path]:
        return [*super().protected_paths(), Path.home() / "AppData" / "Roaming" / "Microsoft" / "Credentials"]

    def popen_kwargs(self) -> dict:
        return {"creationflags": _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP}

    def terminate(self, proc: subprocess.Popen, force: bool = False) -> None:
        # taskkill /T beendet den ganzen Prozessbaum
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, creationflags=_CREATE_NO_WINDOW)

    def shell_argv(self, command: str) -> list[str]:
        return ["cmd.exe", "/c", command]

    def open_target(self, target: str) -> list[str]:
        # `start` ist ein cmd-Builtin; leerer Titel verhindert, dass Pfade als Titel gelten
        return ["cmd.exe", "/c", "start", "", target.strip()]

    def gpu_status(self) -> Optional[tuple[int, int]]:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3, creationflags=_CREATE_NO_WINDOW)
            if r.returncode != 0:
                return None
            used, total = (int(x.strip()) for x in r.stdout.splitlines()[0].split(","))
            return used, total
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            return None

    def screenshot(self, path: Path) -> None:
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise PlatformError("Pillow fehlt für Screenshots") from exc
        ImageGrab.grab().save(path)

    def ocr_image(self, path: Path, languages: tuple[str, ...] = ("de-DE", "en-US")) -> str:
        try:
            import pytesseract  # pyright: ignore[reportMissingImports] — optional unter Windows
            from PIL import Image
        except ImportError as exc:
            raise PlatformError("OCR unter Windows: pytesseract + Tesseract installieren") from exc
        return pytesseract.image_to_string(Image.open(path), lang="deu+eng")

    def clipboard_get(self) -> str:
        return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                              capture_output=True, text=True, timeout=10).stdout

    def clipboard_set(self, text: str) -> None:
        subprocess.run(["clip"], input=text, text=True, timeout=5, check=True)

    def chrome_candidates(self) -> list[str]:
        return [r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
