"""Kamera beobachten: Jarvis meldet sich, wenn sich im Bild etwas bewegt (z. B. jemand kommt ins Zimmer).

Reine Bewegungserkennung in einem eigenen Prozess (camera_watch_worker.py): kein Gesicht, keine Person, kein
gespeichertes Bild. Weil die Kamera dabei dauerhaft läuft (grünes Licht!), fragt der Start IMMER nach. Gemeldet wird
höchstens einmal je 60 Sekunden, über denselben Weg wie die Wächter-Meldungen (ctx.shared["notify"], von der
Oberfläche gesetzt; sonst eine Systemmitteilung). Beim Beenden von Jarvis wird der Prozess gestoppt — er endet
außerdem von selbst nach Ablauf der Zeit oder wenn sein Elternprozess verschwindet."""
from __future__ import annotations

import atexit
import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .registry import CONFIRM, Assessment, InvalidArgs, Tool, ToolContext, ToolError, ToolResult
from .vision_tools import CAMERA_HELP

log = logging.getLogger("jarvis.camerawatch")

WORKER = Path(__file__).with_name("camera_watch_worker.py")
MAX_MINUTES = 60
MIN_NOTICE_GAP_S = 60.0
START_WAIT_S = 8.0
SENSITIVITIES = ("low", "medium", "high")


def _spawn(argv: list[str]):
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)


class CameraWatch:
    def __init__(self, notify: Callable[[str], None], *, spawn=None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], str] = lambda: time.strftime("%H:%M")):
        self._notify, self._spawn, self._clock, self._wall = notify, spawn or _spawn, clock, wall
        self._proc = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._started_at = 0.0
        self._until = 0.0
        self._sensitivity = "medium"
        self._last_notice = -1e9
        self._events = 0
        self._error = ""
        self._ready = threading.Event()
        self._registered = False

    # ------------------------------------------------------------ Steuerung

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, minutes: int, sensitivity: str = "medium") -> str:
        with self._lock:
            if self.running:
                raise ToolError("Die Kamerabeobachtung läuft bereits; zuerst camera_watch_stop.")
            self._error, self._events, self._last_notice = "", 0, -1e9
            self._ready.clear()
            argv = [sys.executable, "-I", str(WORKER), str(int(minutes) * 60), sensitivity]
            try:
                self._proc = self._spawn(argv)
            except OSError as exc:
                raise ToolError(f"Die Kamerabeobachtung ließ sich nicht starten: {exc}") from exc
            self._started_at = self._clock()
            self._until = self._started_at + minutes * 60
            self._sensitivity = sensitivity
            self._thread = threading.Thread(target=self._read, args=(self._proc,), name="jarvis-camera-watch", daemon=True)
            self._thread.start()
            if not self._registered:
                atexit.register(self.stop)
                self._registered = True
        self._ready.wait(START_WAIT_S)
        if self._error:
            self.stop()
            raise ToolError(CAMERA_HELP if self._error == "no_access" else f"Kamera: {self._error}")
        return f"Ich beobachte das Kamerabild für {minutes} Minuten und melde mich bei Bewegung."

    def stop(self) -> bool:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            self._proc = None
            return False
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001 — hängt der Prozess, wird er hart beendet
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self._proc = None
        return True

    def status(self) -> dict:
        running = self.running
        left = max(0, int((self._until - self._clock()) / 60 + 0.99)) if running else 0
        return {"running": running, "minutes_left": left, "sensitivity": self._sensitivity,
                "motion_events": self._events, "error": self._error}

    # ------------------------------------------------------------- Lesen

    def _read(self, proc) -> None:
        try:
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                kind = ev.get("event") if isinstance(ev, dict) else None
                if kind == "started":
                    self._ready.set()
                elif kind == "motion":
                    self._on_motion()
                elif kind == "error":
                    self._error = str(ev.get("error", "unbekannt"))[:120]
                    self._ready.set()
                elif kind == "end":
                    self._on_end(str(ev.get("reason", "")))
        except Exception:  # noqa: BLE001
            log.debug("Kamerawächter: Lesefehler", exc_info=True)
        finally:
            self._ready.set()

    def _on_motion(self) -> None:
        self._events += 1
        now = self._clock()
        if now - self._last_notice < MIN_NOTICE_GAP_S:
            return
        self._last_notice = now
        self._safe_notify(f"Bewegung im Kamerabild, Sir ({self._wall()} Uhr).")

    def _on_end(self, reason: str) -> None:
        if reason == "time":
            self._safe_notify("Die Kamerabeobachtung ist wie vereinbart zu Ende, Sir.")

    def _safe_notify(self, text: str) -> None:
        try:
            self._notify(text)
        except Exception:  # noqa: BLE001
            log.exception("Kamerawächter: Meldung fehlgeschlagen")


# ------------------------------------------------------------- Werkzeuge

def get_watch(ctx: ToolContext) -> CameraWatch:
    def notify(text: str) -> None:
        fn = (ctx.shared or {}).get("notify")
        if callable(fn):
            fn(text)
        else:
            ctx.platform.notify("Jarvis", text)
    return ctx.service("camera_watch", lambda: CameraWatch(notify))


def _args(a: dict) -> tuple[int, str]:
    try:
        minutes = int(a.get("minutes", 15))
    except (TypeError, ValueError):
        raise InvalidArgs("minutes muss eine ganze Zahl sein.") from None
    if not 1 <= minutes <= MAX_MINUTES:
        raise InvalidArgs(f"Ich beobachte höchstens {MAX_MINUTES} Minuten am Stück (1 bis {MAX_MINUTES}).")
    sensitivity = str(a.get("sensitivity") or "medium").lower()
    if sensitivity not in SENSITIVITIES:
        raise InvalidArgs("sensitivity muss low, medium oder high sein.")
    return minutes, sensitivity


def camera_watch_start(ctx: ToolContext, a: dict) -> ToolResult:
    minutes, sensitivity = _args(a)
    return ToolResult(True, get_watch(ctx).start(minutes, sensitivity))


def _assess_start(ctx: ToolContext, a: dict) -> Assessment:
    minutes, sensitivity = _args(a)
    return Assessment(CONFIRM, f"KAMERA {minutes} MINUTEN LANG EINSCHALTEN und bei Bewegung melden "
                               f"(Empfindlichkeit {sensitivity}; kein Bild wird gespeichert)")


def camera_watch_stop(ctx: ToolContext, a: dict) -> ToolResult:
    stopped = get_watch(ctx).stop()
    return ToolResult(True, "Die Kamerabeobachtung ist beendet." if stopped else "Es lief keine Kamerabeobachtung.")


def camera_watch_status(ctx: ToolContext, a: dict) -> ToolResult:
    st = get_watch(ctx).status()
    if st["running"]:
        text = (f"Die Kamerabeobachtung läuft noch etwa {st['minutes_left']} Minuten "
                f"({st['motion_events']} Bewegung(en) bisher).")
    else:
        text = "Es läuft keine Kamerabeobachtung." + (f" Letzter Fehler: {st['error']}." if st["error"] else "")
    return ToolResult(True, text, st)


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("camera_watch_start", "vision",
             "Beobachtet die Kamera bis zu 60 Minuten und meldet bei Bewegung im Bild (z. B. jemand betritt das "
             "Zimmer). Nur Bewegungserkennung — keine Gesichter, keine Bilder gespeichert. BEIDE Angaben sind optional "
             "(Standard 15 Minuten, medium): NICHT nachfragen, direkt aufrufen — die Rückfrage an Sir stellt das System.",
             {"minutes": {"type": "integer"}, "sensitivity": {**S, "enum": list(SENSITIVITIES)}}, (),
             camera_watch_start, _assess_start, announce=lambda a: "Ich schalte die Kamerabeobachtung ein, Sir."),
        Tool("camera_watch_stop", "vision", "Beendet die Kamerabeobachtung.", {}, (), camera_watch_stop),
        Tool("camera_watch_status", "vision", "Zeigt, ob die Kamerabeobachtung läuft und wie lange noch.", {}, (),
             camera_watch_status),
    ]
