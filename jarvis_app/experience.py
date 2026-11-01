"""Wake/standby/startup orchestration, independent of the model's tool decisions.

Progress counts real completed checks, including unavailable optional services.
Cancellation invalidates the whole run, including delayed briefing results.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path

from jarvis_core.background_music import BackgroundMusic
from jarvis_core.briefing.aggregator import build_script
from jarvis_core.config import repo_root
from jarvis_core.ollama_client import OllamaClient

log = logging.getLogger("jarvis.experience")


class Experience:
    STEP_PAUSE = 0.32
    SHUTDOWN_SECONDS = 1.8
    SLEEP_SECONDS = 0.8
    WAKE_SECONDS = 1.6
    MUSIC_FALLBACK_S = 25.0     # Puffer über der geschätzten Sprechdauer des Briefings

    def __init__(self, host):
        self.host = host
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._run_id = 0
        self._ready_once = False
        self._pending_speech: list[str] = []
        self._briefing_speech_id: str | None = None
        self.state = {"mode": "idle", "phase": "idle", "steps": [], "progress": 0}
        cfg = host._cfg.experience
        self.music = BackgroundMusic(cfg.music_volume, cfg.music_duck_volume,
                                     host._cfg.voice.get("output_device"), self._music_changed)

    @property
    def sleeping(self) -> bool:
        return self.state["mode"] == "sleep"

    @property
    def running(self) -> bool:
        return self.state["phase"] in ("running", "shutdown")

    def _settings_path(self) -> Path:
        d = self.host._cfg.tools.data_dir
        return (Path(d).expanduser() if d else self.host._platform.data_dir()) / "experience.json"

    def music_path(self) -> Path | None:
        value = self.host._cfg.experience.music_file
        try:
            value = json.loads(self._settings_path().read_text(encoding="utf-8")).get("music_file") or value
        except (OSError, ValueError, AttributeError):
            pass
        if not value:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else repo_root() / path

    def select_music(self) -> dict:
        """Only an explicit native file picker can change this preference."""
        try:
            import webview
            files = self.host._window.create_file_dialog(
                webview.FileDialog.OPEN, allow_multiple=False,
                file_types=("Audio (*.mp3;*.wav;*.flac;*.ogg;*.aiff)",))
            if not files:
                return {"ok": False, "cancelled": True}
            path = Path(files[0]).expanduser().resolve()
            import soundfile as sf
            with sf.SoundFile(str(path)) as source:
                if source.frames == 0 or source.channels not in (1, 2):
                    raise ValueError("Die Audiodatei muss Mono oder Stereo sein und Audio enthalten.")
            target = self._settings_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps({"music_file": str(path)}, ensure_ascii=False), encoding="utf-8")
            temp.replace(target)
            self._music_changed(self.music.snapshot())
            return {"ok": True, "title": path.name}
        except Exception as exc:
            log.warning("Musikdatei nicht ausgewählt: %s", exc)
            return {"ok": False, "message": f"Audiodatei konnte nicht geladen werden: {exc}"}

    def snapshot(self) -> dict:
        with self._lock:
            state = json.loads(json.dumps(self.state))
        path = self.music_path()
        state["music"] = {**self.music.snapshot(), "configured": bool(path),
                          "file": path.name if path else ""}
        return state

    def _publish(self) -> None:
        self.host._js("jarvis.onExperience", self.snapshot())

    def _music_changed(self, data: dict) -> None:
        path = self.music_path()
        self.host._js("jarvis.onMusic", {**data, "configured": bool(path), "file": path.name if path else ""})

    def ready(self) -> dict:
        with self._lock:
            first = not self._ready_once
            self._ready_once = True
        if first and self.host._cfg.experience.startup_animation:
            self.start("boot")
        elif first:
            threading.Thread(target=self.host._warmup, daemon=True).start()
        return self.snapshot()

    def start(self, mode: str) -> dict:
        if mode not in ("boot", "wake", "reboot", "sleep", "music_stop"):
            return {"ok": False, "message": "Unbekannter Startbefehl."}
        if mode == "music_stop":
            self.music.stop()
            return {"ok": True, "message": "Musik gestoppt, Sir."}
        if mode == "wake" and not self.sleeping:
            # „Hey Jarvis“ im Wachzustand ist kein Weckbefehl — keine Animation, keine Systemprüfung
            return {"ok": True, "noop": True, "message": "Ja, Sir?"}
        if mode == "sleep":
            if self.sleeping:
                return {"ok": True, "message": "Jarvis ist bereits im Standby."}
            self.cancel()
            self.host.cancel()
            with self._lock:
                self._cancel = cancel = threading.Event()
                self._run_id += 1
                self.state = {"mode": "sleep", "phase": "sleeping", "steps": [], "progress": 0,
                              "run_id": self._run_id}
            self.host._hub.set_standby(True)
            self._publish()
            threading.Thread(target=self._settle_sleep, args=(cancel,), daemon=True).start()
            return {"ok": True, "message": "Standby, Sir. ‚Hey Jarvis‘ weckt mich wieder."}
        with self._lock:
            if self.running:
                return {"ok": False, "message": "Die Startsequenz läuft bereits, Sir."}
            self._cancel.set()
            self._cancel = cancel = threading.Event()
            self._run_id += 1
            self._pending_speech = []
            self._briefing_speech_id = None
            self.state = {"mode": mode, "phase": "shutdown" if mode == "reboot" else "running",
                          "steps": [], "progress": 0, "run_id": self._run_id}
        if mode == "wake":
            self.host.hide_panel()
        else:
            self.host.show_panel()
        self.host.cancel(cancel_experience=False)
        self.host._hub.set_standby(False)
        self.host._hub.stop_speaking()
        self.music.stop()
        self.music.duck(False)
        self._publish()
        target = {"reboot": self._reboot, "wake": self._wake}.get(mode, self._run)
        threading.Thread(target=target, args=(mode, cancel), name="jarvis-experience", daemon=True).start()
        return {"ok": True, "message": {"reboot": "Jarvis startet neu, Sir.",
                                        "wake": "Ich bin wieder da, Sir."}.get(mode, "Ich bin gleich bei Ihnen, Sir.")}

    def _settle_sleep(self, cancel):
        if cancel.wait(self.SLEEP_SECONDS):
            return
        with self._lock:
            if cancel is not self._cancel or cancel.is_set():
                return
            self.host.hide_panel()
            self._update(cancel, phase="standby")

    def _wake(self, mode, cancel):
        # Resume the existing session without service checks, music or a briefing.
        if cancel.wait(self.WAKE_SECONDS):
            return
        with self._lock:
            if cancel is not self._cancel or cancel.is_set():
                return
            self._update(cancel, phase="awake")
            self.host.show_panel()

    def cancel(self) -> None:
        with self._lock:
            self._cancel.set()
            self._pending_speech = []
            self._briefing_speech_id = None
            if self.running:
                self.state["phase"] = "cancelled"
        self.music.stop()
        self._publish()

    def close(self) -> None:
        with self._lock:
            self._cancel.set()
            self._pending_speech = []
            self._briefing_speech_id = None
            self.music.stop()

    def voice_connected(self) -> None:
        with self._lock:
            if self.sleeping or self.running or self._cancel.is_set():
                return
            speech, self._pending_speech = self._pending_speech, []
            self._speak(speech, self._cancel)

    def voice_disconnected(self) -> None:
        # The report remains in the panel; an absent voice must not leave the song running.
        with self._lock:
            self._briefing_speech_id = None
            if not self.running:
                self.music.stop()

    def voice_state(self, state: str) -> None:
        # Keep the soundtrack quiet across pauses between briefing sentences.
        with self._lock:
            self.music.duck(bool(self._briefing_speech_id) or state in ("speak", "listen", "think"))

    def speech_finished(self, completion_id: str) -> None:
        with self._lock:
            if completion_id != self._briefing_speech_id:
                return
            self._briefing_speech_id = None
            self.music.stop()

    def _speak(self, lines: list[str], cancel: threading.Event) -> None:
        with self._lock:
            if not lines or cancel.is_set() or cancel is not self._cancel or self.sleeping:
                return
            completion_id = "startup-" + uuid.uuid4().hex
            self._briefing_speech_id = completion_id
            self.music.duck(True)
            # Register the ID before any sentence is synthesized, so failures also finish it.
            # Die Kennung hängt am LETZTEN Satz: der Daemon meldet sie, wenn das ganze Briefing gesprochen ist.
            sent = all(self.host._hub.say(line, completion_id=completion_id if i == len(lines) - 1 else None)
                       for i, line in enumerate(lines))
            if not sent:
                self._briefing_speech_id = None
                self.music.stop()
                return
            # Notstopp: meldet die Sprachausgabe das Ende nie (alter Daemon, Absturz), endet der Song trotzdem.
            words = sum(len(line.split()) for line in lines)
            timer = threading.Timer(words / 2.0 + self.MUSIC_FALLBACK_S, self.speech_finished, args=(completion_id,))
            timer.daemon = True
            timer.start()

    def _update(self, cancel, **changes) -> bool:
        with self._lock:
            if cancel.is_set() or cancel is not self._cancel:
                return False
            self.state.update(changes)
            self._publish()
            return True

    def _call(self, fn, cancel, timeout=20):
        """A stalled optional source never holds the UI or a cancelled run hostage."""
        result = queue.Queue(maxsize=1)
        def work():
            try:
                result.put((True, fn()))
            except Exception as exc:
                result.put((False, exc))
        threading.Thread(target=work, name="jarvis-startup-check", daemon=True).start()
        end = time.monotonic() + timeout
        while not cancel.is_set():
            try:
                ok, value = result.get(timeout=0.1)
                if ok:
                    return value
                raise value
            except queue.Empty:
                if time.monotonic() >= end:
                    raise TimeoutError("Zeitlimit erreicht — Dienst antwortet noch nicht.")
        return None

    def _check_memory(self):
        if not self.host._cfg.tools.enabled:
            return "skip", "Werkzeuge deaktiviert"
        from jarvis_core.tools.memory_tools import get_memory
        get_memory(self.host._runtime().ctx).search("", 1)
        return "ok", "Lokaler Speicher erreichbar"

    def _check_model(self):
        c = OllamaClient(self.host._cfg.ollama_host, connect_timeout=2, read_timeout=25)
        if not c.ping():
            return "warn", "Ollama nicht erreichbar"
        if not c.preload(self.host._cfg.profile.general, self.host._cfg.keep_alive):
            return "warn", "Modell konnte nicht geladen werden"
        quick = self.host._cfg.profile.quick
        if quick and quick != self.host._cfg.profile.general:
            if not c.preload(quick, self.host._cfg.keep_alive):
                return "warn", "Hauptmodell bereit · Schnellspur nicht verfügbar"
            return "ok", f"{self.host._cfg.profile.general} · Schnellspur bereit"
        return "ok", self.host._cfg.profile.general

    def _check_voice(self):
        if self.host._hub.connected:
            return "ok", "Sprach-Daemon verbunden"
        return "warn", "Noch nicht verbunden · Bericht im Panel"

    def _run(self, mode: str, cancel: threading.Event):
        data = None
        with_briefing = self.host._cfg.experience.startup_briefing
        plan = [("core", "Oberfläche", lambda: ("ok", "Overlay verbunden")),
                ("memory", "Gedächtnis", self._check_memory),
                ("model", "Intelligenz", self._check_model),
                ("voice", "Stimme", self._check_voice)]
        if mode == "boot":
            path = self.music_path()
            plan.insert(1, ("music", "Soundtrack", lambda: self._start_music(path, cancel)))
        if with_briefing:
            plan.append(("briefing", "Tagesbriefing", lambda: self.host._ensure_briefing().collect()))
        steps = [{"id": key, "label": label, "state": "pending", "detail": ""} for key, label, _ in plan]
        self._update(cancel, steps=steps)
        for i, (key, _, fn) in enumerate(plan):
            if cancel.is_set():
                return
            steps[i] = {**steps[i], "state": "loading", "detail": "Wird geprüft …"}
            if not self._update(cancel, steps=list(steps)):
                return
            try:
                result = self._call(fn, cancel, timeout=55 if key == "model" else 30)
                if cancel.is_set():
                    return
                if key == "briefing":
                    data = result
                    available = bool(data and data.get("weather"))
                    status, detail = ("ok", "Aktuelles Wetter und Tagesübersicht geladen") if available else (
                        "warn", "Wetter nicht verfügbar · übrige Quellen geladen")
                elif result is None:                 # abgebrochen, während die Prüfung lief
                    return
                else:
                    status, detail = result
            except Exception as exc:
                log.warning("Startprüfung %s: %s", key, exc)
                status, detail = "warn", str(exc)[:180]
            steps[i] = {**steps[i], "state": status, "detail": detail}
            if not self._update(cancel, steps=list(steps), progress=round(100 * (i + 1) / len(plan))):
                return
            if cancel.wait(self.STEP_PAUSE):
                return
        welcome = ("Ich bin wach, Sir. Schön, Sie wiederzusehen." if mode == "wake" else
                   "Wieder für Sie da, Sir." if self.host._startup_mode == "reboot" else "Willkommen zurück, Sir.")
        lines = [welcome]
        if data:
            lines.extend(build_script(data))
        elif with_briefing:
            lines.append("Das aktuelle Tagesbriefing ist gerade nicht erreichbar, Sir.")
        with self._lock:
            if cancel.is_set() or cancel is not self._cancel:
                return
            if not self._update(cancel, phase="ready", warnings=sum(s["state"] == "warn" for s in steps)):
                return
            if data:
                self.host._js("jarvis.onBriefing", data)
            self.host._js("jarvis.onNotice", "\n".join(lines))
            if self.host._hub.connected:
                self._speak(lines, cancel)
            else:
                self._pending_speech = lines
                self.music.stop()

    def _start_music(self, path, cancel):
        if not path:
            return "skip", "Audiodatei über ‚Musik wählen‘ hinzufügen"
        if cancel.is_set():
            return "skip", "Abgebrochen"
        ok, message = self.music.start(path)
        if cancel.is_set():
            self.music.stop()
        return ("ok" if ok else "warn"), message

    def _reboot(self, mode, cancel):
        self.host.cancel(cancel_experience=False)
        self.music.stop()
        self._update(cancel, steps=[{"id": "restart", "label": "Jarvis wird beendet",
                                    "state": "loading", "detail": "Die App startet anschließend neu. Ihr Mac bleibt an."}])
        if cancel.wait(self.SHUTDOWN_SECONDS):
            return
        if not self.host._window:
            self._update(cancel, phase="cancelled")
            return
        self.host.restart_requested = True
        self.host.quit_app()
