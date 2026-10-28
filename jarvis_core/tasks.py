"""Parallele Aufgaben: Übersicht, aktueller Schritt, gezieltes Abbrechen.

Jeder Werkzeugauftrag läuft in einem eigenen Thread mit eigenem Agenten. Der TaskManager führt Buch
(wer macht gerade was) — die Oberfläche zeigt die Liste, und Jarvis kann auf „Was machst du gerade?“
wahrheitsgemäß antworten.  Hinweis: Ollama bedient Sprachmodell-Anfragen nacheinander; parallel
laufen die Werkzeuge (Befehle, Recherche, Dokumente …), nicht die Textgenerierung."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Task:
    id: int
    text: str
    agent: Any
    voice: bool = False
    status: str = "running"            # running | confirm | done | failed | cancelled
    current: str = "startet"
    result: str = ""
    started: float = field(default_factory=time.monotonic)
    finished: Optional[float] = None
    last_announce: float = field(default_factory=time.monotonic)

    @property
    def active(self) -> bool:
        return self.status in ("running", "confirm")

    def elapsed(self) -> float:
        return (self.finished or time.monotonic()) - self.started


class TaskManager:
    def __init__(self, spawn: Callable[[], Any], on_change: Callable[[], None] = lambda: None, keep: int = 20):
        self._spawn = spawn
        self._on_change = on_change
        self._tasks: dict[int, Task] = {}
        self._next = 1
        self._lock = threading.Lock()
        self._keep = keep

    def begin(self, text: str, voice: bool = False) -> Task:
        with self._lock:
            t = Task(self._next, text, self._spawn(), voice)
            self._tasks[t.id] = t
            self._next += 1
            done = [k for k, v in self._tasks.items() if not v.active]
            for k in done[:-self._keep]:
                self._tasks.pop(k, None)
        self._on_change()
        return t

    def update(self, task: Task, **kw) -> None:
        if task.status == "cancelled":       # abgebrochen bleibt abgebrochen, auch wenn noch Ereignisse eintrudeln
            kw.pop("status", None)
        for k, v in kw.items():
            setattr(task, k, v)
        self._on_change()

    def finish(self, task: Task, ok: bool, result: str) -> None:
        if task.status == "cancelled":
            result = task.result or result
        else:
            task.status = "done" if ok else "failed"
        task.result, task.finished = result, time.monotonic()
        self._on_change()

    def active(self) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        return [t for t in tasks if t.active]

    def get(self, task_id: int) -> Optional[Task]:
        return self._tasks.get(task_id)

    def cancel(self, task_id: Optional[int] = None) -> int:
        """Eine Aufgabe (oder alle) abbrechen. Gibt die Zahl der abgebrochenen zurück."""
        n = 0
        for t in self.active():
            if task_id is None or t.id == task_id:
                t.status, t.result = "cancelled", "Abgebrochen"
                t.agent.cancel()
                n += 1
        if n:
            self._on_change()
        return n

    def snapshot(self) -> list[dict[str, Any]]:
        return [{"id": t.id, "text": t.text[:60], "status": t.status, "current": t.current[:90],
                 "seconds": int(t.elapsed())} for t in self.active()]

    def describe(self) -> str:
        """Gesprochene/geschriebene Antwort auf „Was machst du gerade?“ — nur Tatsachen."""
        act = self.active()
        if not act:
            return "Gerade arbeite ich an nichts, Sir."
        if len(act) == 1:
            t = act[0]
            return f"Ich arbeite an Aufgabe {t.id}: „{t.text[:70]}“. Aktuell: {t.current}. Seit {int(t.elapsed())} Sekunden."
        lines = [f"Aufgabe {t.id}: „{t.text[:50]}“ — {t.current}" for t in act]
        return f"Ich arbeite gerade an {len(act)} Aufgaben gleichzeitig, Sir. " + " ".join(lines)
