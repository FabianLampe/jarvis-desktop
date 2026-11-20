"""Gemeinsamer, sicherer Zugang zu osascript und festen Systembefehlen (macOS).

Grundregel: Skripttext ist IMMER fest im Code. Alles, was vom Modell, vom Nutzer oder aus fremden
Quellen stammt, wird ausschließlich als Argument übergeben (`on run argv` … `item 1 of argv`) —
nie in den Skripttext eingesetzt. Unterprozesse laufen immer als argv-Liste, nie über eine Shell.
Tests ersetzen `_spawn`; ohne Ersatz startet ein Test hier nie einen echten Prozess (conftest)."""
from __future__ import annotations

import logging
import subprocess
from typing import Optional

from .registry import ToolError

log = logging.getLogger("jarvis.macscript")

AUTOMATION_HELP = ("Zugriff auf {app} nicht erlaubt. Bitte unter Systemeinstellungen → Datenschutz & Sicherheit → "
                   "Automation das Programm, aus dem Jarvis gestartet wurde (meist Terminal), für {app} freigeben.")
ACCESSIBILITY_HELP = ("Bedienungshilfen-Zugriff fehlt. Bitte unter Systemeinstellungen → Datenschutz & Sicherheit → "
                      "Bedienungshilfen das Programm, aus dem Jarvis gestartet wurde (meist Terminal), einschalten "
                      "und Jarvis neu starten.")


def _spawn(argv: list[str], timeout: float, stdin_text: Optional[str] = None) -> tuple[int, str, str]:
    """Einziger Ort, an dem dieses Modul Prozesse startet (argv-Liste, keine Shell)."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False,
                           input=stdin_text, stdin=None if stdin_text is not None else subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{argv[0]} antwortet nicht (Zeitlimit {int(timeout)} s)") from exc
    except FileNotFoundError as exc:
        raise ToolError(f"Programm nicht gefunden: {argv[0]}") from exc
    except OSError as exc:
        raise ToolError(f"{argv[0]} ließ sich nicht starten: {exc}") from exc
    return p.returncode, p.stdout or "", p.stderr or ""


def explain_error(stderr: str, app: str = "der App") -> str:
    """Übersetzt typische AppleScript-Fehler in verständliche deutsche Sätze."""
    low = stderr.lower()
    if "front_mismatch" in low:
        return "Die App ist nicht in den Vordergrund gekommen; ich habe nichts getippt."
    if "-1743" in low or "not authorized to send apple events" in low or "nicht berechtigt, apple-events" in low:
        return AUTOMATION_HELP.format(app=app)
    if ("-1719" in low or "-25211" in low or "assistive" in low or "bedienungshilfen" in low
            or "nicht zugelassen" in low and "tastenanschläge" in low or "keystroke" in low and "1002" in low):
        return ACCESSIBILITY_HELP
    if "-600" in low or "isn’t running" in low or "isn't running" in low or "läuft nicht" in low:
        return f"{app} läuft nicht."
    if "-128" in low:
        return "Abgebrochen."
    tail = [l for l in stderr.strip().splitlines() if l.strip()]
    return (tail[-1] if tail else "unbekannter Fehler")[:240]


def osa(script: str, *args: str, app: str = "der App", timeout: float = 30.0) -> str:
    """Führt ein FESTES AppleScript aus; `args` kommen als `argv` an. Gibt stdout (ohne Zeilenende) zurück."""
    if not isinstance(script, str) or not script.strip():
        raise ToolError("Leeres Skript")
    rc, out, err = _spawn(["osascript", "-e", script, *[str(a) for a in args]], timeout)
    if rc != 0:
        raise ToolError(explain_error(err, app))
    return out.rstrip("\n")


def cmd(argv: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
    """Fester Systembefehl (argv-Liste)."""
    return _spawn(list(argv), timeout)


def is_running(process: str) -> bool:
    """Läuft ein Programm mit genau diesem Prozessnamen? (startet nichts)"""
    try:
        rc, _, _ = _spawn(["pgrep", "-x", process], 5)
    except ToolError:
        return False
    return rc == 0
