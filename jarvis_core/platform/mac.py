"""macOS-Adapter: open, osascript, screencapture, say, pbcopy, Apple Vision (OCR)."""

from __future__ import annotations

import platform as _platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .base import PlatformAdapter, PlatformError, _tool_version


class MacAdapter(PlatformAdapter):
    name = "macos"
    gui = "cocoa"
    shell_label = "zsh"

    def _default_data_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "Jarvis"

    def protected_paths(self) -> list[Path]:
        home = Path.home()
        return [*super().protected_paths(), home / "Library" / "Keychains",
                home / "Library" / "Mail", home / "Library" / "Messages"]

    def shell_argv(self, command: str) -> list[str]:
        return ["/bin/zsh", "-c", command]

    # ----------------------------------------------------------- Öffnen

    def open_target(self, target: str) -> list[str]:
        t = target.strip()
        if not t:
            raise PlatformError("Leeres Ziel")
        if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I) or Path(t).expanduser().exists():
            return ["open", t if "://" in t else str(Path(t).expanduser())]
        return ["open", "-a", t]       # App-Name, z. B. „Safari“

    def notify_argv(self, title: str, text: str) -> Optional[list[str]]:
        # Text kommt als Skript-Argument, nie in den Skript-Quelltext (keine Injektion)
        script = ('on run argv\n display notification (item 2 of argv) '
                  'with title (item 1 of argv)\nend run')
        return ["osascript", "-e", script, title[:120], text[:400]]

    def screenshot(self, path: Path) -> None:
        r = subprocess.run(["screencapture", "-x", str(path)],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not path.exists() or path.stat().st_size == 0:
            raise PlatformError(
                "Bildschirmaufnahme fehlgeschlagen — Berechtigung „Bildschirm- & "
                "Systemaudioaufnahme“ für das Terminal/die App erteilen "
                "(Systemeinstellungen → Datenschutz & Sicherheit).")

    def ocr_image(self, path: Path, languages: tuple[str, ...] = ("de-DE", "en-US")) -> str:
        try:
            import Quartz  # noqa: F401
            import Vision
            from Foundation import NSURL  # pyright: ignore[reportAttributeAccessIssue]
        except ImportError as exc:
            raise PlatformError("pyobjc-framework-Vision fehlt (pip install -r requirements.txt)") from exc
        url = NSURL.fileURLWithPath_(str(path))
        handler = Vision.VNImageRequestHandler.alloc()  # pyright: ignore[reportAttributeAccessIssue].initWithURL_options_(url, None)
        req = Vision.VNRecognizeTextRequest.alloc().init()  # pyright: ignore[reportAttributeAccessIssue]
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)  # pyright: ignore[reportAttributeAccessIssue]
        req.setRecognitionLanguages_(list(languages))
        req.setUsesLanguageCorrection_(True)
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            raise PlatformError(f"Apple-Vision-OCR fehlgeschlagen: {err}")
        lines = []
        for obs in req.results() or []:
            cands = obs.topCandidates_(1)
            if cands:
                lines.append(str(cands[0].string()))
        return "\n".join(lines)

    def speak_argv(self, text: str, voice: Optional[str] = None,
                   rate: Optional[int] = None) -> Optional[list[str]]:
        argv = ["say"]
        if voice:
            argv += ["-v", voice]
        if rate:
            argv += ["-r", str(rate)]
        return [*argv, "--", text]

    def clipboard_get(self) -> str:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout

    def clipboard_set(self, text: str) -> None:
        subprocess.run(["pbcopy"], input=text, text=True, timeout=5, check=True)

    def get_volume(self) -> Optional[int]:
        r = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                           capture_output=True, text=True, timeout=5)
        try:
            return int(r.stdout.strip())
        except ValueError:
            return None

    def set_volume(self, percent: int) -> None:
        pct = max(0, min(100, int(percent)))
        subprocess.run(["osascript", "-e", f"set volume output volume {pct}"],
                       capture_output=True, timeout=5, check=True)

    def chrome_candidates(self) -> list[str]:
        return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]

    def permission_hints(self) -> dict[str, str]:
        return {
            "mikrofon": "Sprach-Daemon: Terminal/iTerm (bzw. die startende App) braucht "
                        "Datenschutz & Sicherheit → Mikrofon.",
            "bedienungshilfen": "Nur nötig, wenn Jarvis per System Events Tasten/Fenster "
                                "steuern soll (derzeit nicht verwendet).",
            "automation": "Nur nötig für AppleScript-Steuerung anderer Apps; Lautstärke und "
                          "Benachrichtigungen kommen ohne aus.",
            "bildschirmaufnahme": "Nur für screen_ocr / Screenshots: Datenschutz & Sicherheit → "
                                  "Bildschirm- & Systemaudioaufnahme.",
        }

    def system_info(self) -> dict[str, Any]:
        info = super().system_info()
        info["macos"] = _platform.mac_ver()[0]
        info["chip"] = _sysctl("machdep.cpu.brand_string")
        info["gpu"] = "Apple Silicon (Unified Memory, kein dedizierter VRAM)" \
            if _platform.machine() == "arm64" else None
        info["homebrew"] = shutil.which("brew")
        return info


def _sysctl(key: str) -> Optional[str]:
    return _tool_version(["sysctl", "-n", key]) if shutil.which("sysctl") else None
