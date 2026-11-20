"""Bewegungswächter für die Kamera (eigener Prozess, gestartet von camera_watch.py).

Aufruf: python -I camera_watch_worker.py <sekunden> <low|medium|high> [kamera-index]
Ausgabe: je Ereignis eine JSON-Zeile — {"event":"started"}, {"event":"motion"}, {"event":"error","error":…},
         {"event":"end","reason":"time|parent|signal"}.

Was dieser Prozess tut: Frame-Differenz gegen einen langsam nachgeführten Hintergrund. Das ist alles — keine
Gesichtserkennung, keine Identifizierung, KEIN Bild wird gespeichert oder weitergegeben; jedes Bild lebt nur
für den Vergleich im Arbeitsspeicher. Der Prozess beendet sich selbst nach Ablauf der Zeit oder wenn Jarvis
(der Elternprozess) nicht mehr läuft."""
import json
import os
import signal
import sys
import time

# Anteil geänderter Bildpunkte, ab dem eine Bewegung zählt.
SENSITIVITY = {"low": 0.08, "medium": 0.03, "high": 0.012}
SAMPLE_S = 0.4              # Abstand der Vergleichsbilder
WARMUP_FRAMES = 12          # Belichtung einpendeln lassen
CONFIRM_HITS = 2            # so viele Vergleiche hintereinander müssen auffallen (gegen Rauschen)
LIGHT_CHANGE = 0.6          # fast das ganze Bild ändert sich = Licht/Belichtung, keine Bewegung
COOLDOWN_S = 10.0           # Mindestabstand der Meldungen dieses Prozesses (Jarvis begrenzt zusätzlich auf 60 s)


class MotionDetector:
    """Bewegungserkennung über Frame-Differenz. `feed(gray)` bekommt Graustufenbilder (numpy) und
    liefert True, wenn eine Bewegung bestätigt ist."""

    def __init__(self, min_ratio: float):
        self.min_ratio = min_ratio
        self.background = None
        self.hits = 0
        self.frames = 0

    def feed(self, gray) -> bool:
        import cv2
        import numpy as np
        small = cv2.GaussianBlur(cv2.resize(gray, (160, 120)), (9, 9), 0)
        self.frames += 1
        if self.background is None or self.frames <= WARMUP_FRAMES:
            self.background = small.astype("float32")
            return False
        delta = cv2.absdiff(small, cv2.convertScaleAbs(self.background))
        changed = float((delta > 25).sum()) / delta.size
        if changed > LIGHT_CHANGE:                         # Belichtungssprung: neu einmessen statt melden
            self.background = small.astype("float32")
            self.hits = 0
            return False
        cv2.accumulateWeighted(small.astype(np.float32), self.background, 0.05)   # langsam mitwandern
        self.hits = self.hits + 1 if changed >= self.min_ratio else 0
        if self.hits >= CONFIRM_HITS:
            self.hits = 0
            return True
        return False


def emit(**kw) -> None:
    print(json.dumps(kw), flush=True)


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[2] not in SENSITIVITY:
        emit(event="error", error="Aufruf: <sekunden> <low|medium|high> [kamera]")
        return 2
    seconds = max(1.0, min(float(sys.argv[1]), 3600.0))
    index = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    parent = os.getppid()
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    try:
        import cv2
    except ImportError:
        emit(event="error", error="opencv fehlt (pip install -r requirements-tools.txt)")
        return 2
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION) if sys.platform == "darwin" else cv2.VideoCapture(index)
    if not cap.isOpened():
        emit(event="error", error="no_access")
        return 3
    det = MotionDetector(SENSITIVITY[sys.argv[2]])
    deadline, last_event, reason, started = time.monotonic() + seconds, 0.0, "time", False
    try:
        while time.monotonic() < deadline:
            if stop["flag"]:
                reason = "signal"
                break
            if os.getppid() != parent:                     # Jarvis ist weg: Kamera sofort frei geben
                reason = "parent"
                break
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(SAMPLE_S)
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            del frame                                      # nichts aufbewahren
            if not started and det.frames >= 1:
                started = True
                emit(event="started")
            if det.feed(gray) and time.monotonic() - last_event >= COOLDOWN_S:
                last_event = time.monotonic()
                emit(event="motion")
            time.sleep(SAMPLE_S)
        emit(event="end", reason=reason)
        return 0
    finally:
        cap.release()


if __name__ == "__main__":
    sys.exit(main())
