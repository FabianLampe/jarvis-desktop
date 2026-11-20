"""Bridge zwischen UI (JS) und jarvis_core — pywebview js_api.

JS → Python:  send_message(text, force_7b) · get_config() · get_status() ·
              toggle_panel() · resize_orb(richtung) · push_to_talk() ·
              reset_chat() · save_layout() · quit_app()

Python → JS (über evaluate_js, alle Argumente JSON-serialisiert):
  jarvis.onToken(stream_id, text)     gebündelte Antwort-Häppchen
  jarvis.onState(state, amplitude)    idle | listen | think | speak
  jarvis.onStatus({model, tps, cpu, ram, vram, heavy, online, daemon})
  jarvis.onMeta(stream_id, ev)        route / swap / done / error
  jarvis.onDaemon(connected)          Sprach-Daemon verbunden?
  jarvis.onUserMessage(text)          erkannter Sprachbefehl (als Nutzerzeile zeigen)
"""

from __future__ import annotations

import json
import logging
import re as _re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from jarvis_core import browser_intent
from jarvis_core.lifecycle import lifecycle_command
from jarvis_core.agent import BrowserAgent
from jarvis_core.memory.reminders import ReminderService
from jarvis_core.platform import current as current_platform
from jarvis_core.runtime import Runtime, build_runtime, spawn_agent
from jarvis_core.tasks import TaskManager
from jarvis_core.tool_intent import fast_path
from jarvis_core.briefing.aggregator import BriefingService, build_script
from jarvis_core.chat import ChatService
from jarvis_core.config import AppConfig
from jarvis_core.ollama_client import OllamaClient
from jarvis_core.sysmon import SystemMonitor
from jarvis_core.tools.browser import BrowserController

from .ws_server import VoiceHub

log = logging.getLogger("jarvis.bridge")

PANEL_W = 560          # Chat-Panel-Breite (px)
PANEL_MIN_H = 780      # Mindesthöhe des Fensters bei offenem Panel
GAP = 16               # Abstand Orb ↔ Panel
ORB_MIN, ORB_MAX = 130, 520
ZONE_FACTOR = 1.5      # Orb-Zone (Canvas) relativ zur Orb-Größe (Platz fürs Glühen)

STATUS_INTERVAL = 2.0      # Sekunden zwischen Status-Pushes
OFFLINE_PING_EVERY = 5     # offline: nur jeder 5. Tick pingt (≈ alle 10 s, lt. Spec)


def _zone(orb_px: int) -> int:
    return int(orb_px * ZONE_FACTOR)


# Antwort auf eine gesprochene Bestätigungsfrage („Soll ich das machen, Sir? Ja oder nein.“)
_CONFIRM_YES = _re.compile(r"^(?:ja|jawohl|jep|jo|genau|richtig|korrekt|passt|okay|ok|einverstanden|"
                           r"bestätig\w*|mach(?:\s+(?:das|es))?|tu\s+(?:das|es)|ausführen|los)\b", _re.I)
_CONFIRM_NO = _re.compile(r"^(?:nein|nee|nö|ne|nicht|stopp?|halt|abbrechen|abbruch|lieber nicht|falsch|"
                          r"ablehnen|auf keinen fall)\b", _re.I)


def voice_decision(text: str) -> Optional[bool]:
    """True/False für ein gesprochenes Ja/Nein, None für alles andere („Jarvis“ davor ist erlaubt)."""
    t = _re.sub(r"^\W*(?:hey\s+)?jarvis\W*", "", text.strip(), flags=_re.I)
    if _CONFIRM_NO.match(t):                          # „nein, nicht so“ ist nie ein Ja
        return False
    return True if _CONFIRM_YES.match(t) else None


_AFFIRM = _re.compile(r"^(?:ja|jawohl|jep|okay|ok|gerne|gern|genau|los|bitte|na klar|klar|sehr gerne|"
                      r"(?:ja[, ]*)?(?:bitte[, ]*)?(?:mach|tu|erledige|hol|führ)\w*\s*(?:das|es|sie|ihn|dies)?"
                      r"(?:\s*(?:bitte|mal|gerne|aus|ein|raus|durch))?)[\s,.!]*(?:bitte|gerne|mal)?[\s.!]*$", _re.I)


class _SentenceSpeaker:
    """Gibt eine gestreamte Antwort satzweise an die Sprachausgabe weiter, sobald ein Satz fertig ist —
    statt erst nach der ganzen Antwort zu sprechen (lange Geschichten: keine Minute Stille)."""

    _END = _re.compile(r"[.!?…](?=\s)|\n\n")   # Satzende erst mit folgendem Leerzeichen (z. B. „Dr.“ im Stream)

    def __init__(self, say, on_first=None, min_chars: int = 15):
        self.say, self.on_first, self.min_chars = say, on_first, min_chars
        self.buf, self.spoken = "", False

    def feed(self, token: str) -> None:
        self.buf += token
        if self.buf.count("```") % 2:                 # Codeblock offen: erst komplett abwarten
            return
        cut = -1
        for m in self._END.finditer(self.buf):
            # nur dort schneiden, wo kein Codeblock offen ist
            if m.end() >= self.min_chars and self.buf[:m.end()].count("```") % 2 == 0:
                cut = m.end()
        if cut > 0:
            self._emit(self.buf[:cut])
            self.buf = self.buf[cut:]

    def finish(self) -> None:
        if self.buf.strip():
            self._emit(self.buf)
        self.buf = ""

    def _emit(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if not self.spoken and self.on_first:
            self.on_first()
        self.spoken = True
        self.say(text)


class Bridge:
    def __init__(self, cfg: AppConfig,
                 chat: ChatService | None = None,
                 sysmon: SystemMonitor | None = None,
                 hub: VoiceHub | None = None,
                 layout_path: Path | None = None,
                 startup_mode: str = "boot"):
        self._cfg = cfg
        self._client = OllamaClient(cfg.ollama_host)
        self._platform = current_platform()
        self._rt: Optional[Runtime] = None
        self._runtime_lock = threading.RLock()
        self._chat = chat or ChatService(cfg, client=self._client, facts=self._facts)
        self._sysmon = sysmon or SystemMonitor(cfg.profile.gpu_monitor, self._platform)
        self._hub = hub or VoiceHub(cfg.ws_host, cfg.ws_port)
        self._layout_path = layout_path or (Path(__file__).parent / "layout.json")

        self._window = None            # wird in on_started gesetzt
        self._panel_open = False
        self._side = "right"
        self._orb_px = int(cfg.orb.get("size_px", 170))
        self._heavy_active = False
        self._last_model: Optional[str] = None
        self._voice_confirm: Optional[str] = None   # Bestätigung, die per Stimme beantwortet werden darf
        self._last_tps: Optional[float] = None
        self._online: Optional[bool] = None
        self._seq = 0
        self._chat_generation = 0
        self._filler_epoch = 0
        self._chat_generation_lock = threading.RLock()
        self._workers: list[threading.Thread] = []
        self._stop = threading.Event()
        self._browser_ctl: Optional[BrowserController] = None
        self._browser_agent: Optional[BrowserAgent] = None
        self._briefing: Optional[BriefingService] = None
        self._reminders: Optional[ReminderService] = None
        self._telegram = None
        self._watcher = None
        self._muted = False
        self._tasks: Optional[TaskManager] = None
        from jarvis_core.fillers import FillerSource
        self._fillers = FillerSource(weather=self._weather_for_filler)
        self._last_said = 0.0
        _say = self._hub.say

        def say_and_note(text: str, **kwargs) -> bool:      # merkt sich, wann zuletzt gesprochen wurde (Stille messen)
            import time as _t
            self._last_said = _t.monotonic()
            return _say(text, **kwargs)
        setattr(self._hub, "say", say_and_note)   # Instanz-Hülle (VoiceHub und Test-Hubs)
        self._startup_mode = startup_mode
        self.restart_requested = False
        from .experience import Experience
        self._experience = Experience(self)

    # ------------------------------------------------ Werkzeug-Laufzeit

    def _runtime(self) -> Runtime:
        with self._runtime_lock:
            if self._rt is None:
                self._rt = build_runtime(self._cfg, self._client, interactive=True)
                # Meldungen von Hintergrundwerkzeugen (Kamerabeobachtung) laufen denselben Weg wie die Wächter-Meldungen.
                self._rt.ctx.shared["notify"] = self._on_watcher_notice
            return self._rt

    def _task_manager(self) -> TaskManager:
        if self._tasks is None:
            rt = self._runtime()
            self._tasks = TaskManager(lambda: spawn_agent(rt), on_change=self._push_tasks)
        return self._tasks

    def _quick_model(self) -> Optional[str]:
        """Läuft gerade eine Aufgabe, beantwortet das kleine Schnellmodell weitere Fragen — sie müssten
        sonst hinter dem Werkzeug-Modell warten (Ollama bedient ein Modell nacheinander)."""
        q = self._cfg.profile.quick
        if q and self._tasks is not None and self._tasks.active():
            return q
        return None

    FILLER_SILENCE_S = 4.0          # so lange Stille (seit dem letzten Gesprochenen), bevor ein Füllsatz kommt
    FILLER_GAP_S = 12.0             # Mindestabstand zwischen zwei Füllsätzen
    FILLER_MAX = 3

    def _start_filler(self, request: str, answered: threading.Event) -> None:
        if not (self._cfg.voice.get("filler_enabled", True) and self._hub.connected):
            return
        generation = (self._chat_generation, self._filler_epoch)
        threading.Thread(target=self._filler_loop, args=(request, answered, generation),
                         name="jarvis-filler", daemon=True).start()

    def _filler_loop(self, request: str, answered: threading.Event, generation=None) -> None:
        """Füllt echte Stille mit geprüften Inhalten (Fakten-Liste, echtes Wetter) — nie mit
        Modell-Text, der Fakten erfinden oder die Antwort vorwegnehmen könnte."""
        import time as _t
        generation = generation or (self._chat_generation, self._filler_epoch)
        said, last_filler, started = 0, 0.0, _t.monotonic()
        while said < self.FILLER_MAX and not answered.wait(0.25):
            if self._stop.is_set() or generation != (self._chat_generation, self._filler_epoch):
                return
            now = _t.monotonic()
            quiet_since = max(self._last_said, started)        # Stille erst ab dieser Anfrage zählen
            if now - quiet_since < self.FILLER_SILENCE_S or now - last_filler < self.FILLER_GAP_S:
                continue
            line = self._fillers.next(request)
            if answered.is_set() or generation != (self._chat_generation, self._filler_epoch):
                return
            self._hub.say(line)
            said, last_filler = said + 1, _t.monotonic()

    def _weather_for_filler(self) -> Optional[str]:
        from jarvis_core.tools.weather_tools import weather
        rt = self._runtime()
        return weather(rt.ctx, {"when": "heute"}).text

    def _push_tasks(self) -> None:
        if self._tasks is not None:
            self._js("jarvis.onTasks", self._tasks.snapshot())

    def cancel_task(self, task_id: int) -> dict[str, Any]:
        """✕ in der Aufgabenliste: genau diese Aufgabe abbrechen, die anderen laufen weiter."""
        return {"cancelled": self._task_manager().cancel(int(task_id))}

    def _task_monitor(self) -> None:
        """Meldet bei langen Aufgaben (per Sprache) alle ~30 s, woran Jarvis noch arbeitet."""
        import time as _t
        while not self._stop.wait(5.0):
            if self._tasks is None:
                continue
            now = _t.monotonic()
            for t in self._tasks.active():
                if t.voice and t.status == "running" and now - t.started > 25 and now - t.last_announce > 30:
                    t.last_announce = now
                    if self._hub.connected:
                        self._hub.say(f"Ich arbeite noch an Aufgabe {t.id}, Sir: {t.current}.")

    def _facts(self, text: str) -> list[str]:
        try:
            return self._runtime().facts(text)
        except Exception:  # noqa: BLE001
            return []

    def _start_reminders(self) -> None:
        """Erinnerungsdienst starten und verpasste Termine melden (nur solange Jarvis läuft)."""
        rt = self._runtime()
        svc: ReminderService = rt.ctx.service("reminders", lambda: ReminderService(
            rt.ctx.data_dir, self._cfg.reminders.timezone, self._cfg.reminders.misfire_grace_s))
        svc.on_due = self._on_reminder          # ein Dienst, eine Zustellung (kein Doppel-Feuern)
        self._reminders = svc
        missed = svc.start()
        if missed:
            log.info("%d verpasste Erinnerung(en) beim Start", len(missed))
        svc.deliver_pending()

    def _on_reminder(self, d: dict) -> None:
        text = ("Verpasste Erinnerung, Sir: " if d.get("missed") else "Erinnerung, Sir: ") + d["text"]
        self._platform.notify("Jarvis – Erinnerung", d["text"])
        self._js("jarvis.onNotice", text)
        if self._hub.connected and not self._experience.sleeping:
            self._hub.say(text)

    def _auto_learn(self, text: str) -> None:
        try:
            for note in self._runtime().learn(text):
                log.info("Auto-Learn: %s", note)
                self._js("jarvis.onNotice", note)
        except Exception:  # noqa: BLE001 — Merken darf nie den Auftrag stören
            log.debug("Auto-Learn fehlgeschlagen", exc_info=True)

    def _start_watcher(self) -> None:
        """Wächter (neues Gerät, Termin in Kürze, Platte, Akku) — meldet wie eine Erinnerung."""
        if not self._cfg.proactive.enabled:
            return
        from jarvis_core.proactive import Watcher
        self._watcher = Watcher(self._runtime().ctx, self._on_watcher_notice)
        self._watcher.start()
        log.info("Wächter aktiv (alle %d min)", self._cfg.proactive.interval_min)

    def _on_watcher_notice(self, text: str) -> None:
        if self._experience.running:
            return                                   # Startsequenz nicht unterbrechen
        self._platform.notify("Jarvis", text)
        self._js("jarvis.onNotice", text)
        if self._hub.connected and not self._experience.sleeping:
            self._hub.say(text)

    def _start_telegram(self) -> None:
        from jarvis_core.integrations.services import Telegram, TelegramBot
        tg = Telegram(self._cfg.integrations.get("telegram"))
        st = tg.status()
        if not tg.enabled:
            return
        if st.state != "ready":
            log.warning("Telegram nicht gestartet: %s", st.line("Telegram"))
            return
        remote = build_runtime(self._cfg, self._client, interactive=False)   # keine Bestätigung möglich

        def handle(text: str) -> str:
            if remote.agent.wants_tools(text):
                final = "Erledigt."
                for ev in remote.agent.run(text):
                    if ev["type"] == "done":
                        final = ev["text"]
                return final
            parts = [e.get("token", "") for e in self._chat.client.chat_stream(
                self._cfg.profile.general, [{"role": "user", "content": text}],
                num_ctx=self._cfg.profile.num_ctx)]
            return "".join(parts) or "…"

        self._telegram = TelegramBot(tg, handle)
        self._telegram.start()
        log.info("Telegram-Bot aktiv (nur eigener Chat, ohne Bestätigungs-Aktionen)")

    # ----------------------------------------------------- Lebenszyklus

    def on_started(self, window) -> None:
        """webview.start-Callback: Fenster ist da, Hintergrunddienste starten."""
        self._window = window
        window.events.closing += self._on_closing
        self._hub.start(on_state=self._daemon_state,
                        on_clients_changed=self._daemon_clients,
                        on_utterance=self._daemon_utterance,
                        on_caption=self._daemon_caption,
                        on_briefing=self._daemon_briefing,
                        on_muted=self._daemon_muted,
                        on_speech_finished=self._experience.speech_finished)
        threading.Thread(target=self._status_loop, name="jarvis-status", daemon=True).start()
        # The UI-ready handshake starts the visible startup checks/model warmup.
        threading.Thread(target=self._task_monitor, name="jarvis-taskmon", daemon=True).start()
        if self._cfg.tools.enabled:
            for fn in (self._start_reminders, self._start_telegram, self._start_watcher):
                threading.Thread(target=self._safe, args=(fn,), name=f"jarvis-{fn.__name__}", daemon=True).start()

    def _safe(self, fn) -> None:
        try:
            fn()
        except Exception:
            log.exception("Start von %s fehlgeschlagen", fn.__name__)

    def _warmup(self) -> None:
        """Lädt das Allrounder-Modell vorab in den VRAM — sonst stockt der erste
        echte Befehl, während der Sprach-Daemon das VRAM mit XTTS teilt."""
        try:
            for _ in self._chat.client.chat_stream(
                self._cfg.profile.general, [{"role": "user", "content": "Hallo"}],
                num_ctx=self._cfg.profile.num_ctx, keep_alive=self._cfg.keep_alive):
                pass
            log.info("Allrounder-Modell vorgewärmt: %s", self._cfg.profile.general)
            q = self._cfg.profile.quick
            if q:                                    # Schnellspur bereithalten, damit Zwischenfragen sofort drankommen
                self._client.preload(q, self._cfg.keep_alive, self._cfg.profile.num_ctx)
                log.info("Schnellspur-Modell vorgewärmt: %s", q)
        except Exception as exc:
            log.debug("Warmup übersprungen: %s", exc)

    def _on_closing(self):
        self._stop.set()
        self._experience.close()
        if self._tasks is not None:
            self._tasks.cancel()
        self._hub.stop_speaking()
        self._write_layout()
        if self._browser_ctl is not None:
            self._browser_ctl.shutdown()
        if self._reminders is not None:
            self._reminders.shutdown()
        if self._telegram is not None:
            self._telegram.stop()
        if self._watcher is not None:
            self._watcher.stop()
        if self._rt is not None:
            cam = self._rt.ctx.shared.get("camera_watch")
            if cam is not None:
                cam.stop()                          # Kamera nie laufen lassen, wenn Jarvis endet
            self._rt.agent.cancel()

    def _ensure_browser_agent(self) -> BrowserAgent:
        if self._browser_agent is None:
            b = self._cfg.browser
            self._browser_ctl = BrowserController(
                profile_dir=b.profile_dir, channel=b.channel, headless=b.headless)
            self._browser_agent = BrowserAgent(self._cfg, self._browser_ctl, self._client)
        return self._browser_agent

    # ------------------------------------------------------- JS → Python

    def ui_ready(self) -> dict[str, Any]:
        return self._experience.ready()

    def request_experience(self, mode: str) -> dict[str, Any]:
        return self._experience.start(mode)

    IMAGE_MAX_BYTES = 15_000_000
    _IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"), (b"BM", "bmp"))

    def attach_image(self, data_url: str, name: str = "") -> dict[str, Any]:
        """Bild aus dem Chat (Einfügen, Hineinziehen, Auswahl) im Arbeitsordner unter „Bilder“ ablegen.
        Gespeichert wird nur, was nach den ersten Bytes tatsächlich ein Bild ist."""
        import base64
        import binascii
        head, _, payload = str(data_url or "").partition(",")
        if not head.startswith("data:image/") or ";base64" not in head:
            return {"ok": False, "error": "Das ist kein Bild."}
        if len(payload) > self.IMAGE_MAX_BYTES * 4 // 3 + 16:
            return {"ok": False, "error": "Das Bild ist größer als 15 MB."}
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            return {"ok": False, "error": "Das Bild ließ sich nicht lesen."}
        ext = next((e for magic, e in self._IMAGE_MAGIC if raw.startswith(magic)), None)
        if ext is None and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            ext = "webp"
        if ext is None and raw[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypavif"):
            ext = "heic" if b"heic" in raw[4:12] or b"heix" in raw[4:12] or b"mif1" in raw[4:12] else "avif"
        if ext is None:
            return {"ok": False, "error": "Unbekanntes Bildformat."}
        ws = self._runtime().ctx.workspace
        folder = ws / "Bilder"
        folder.mkdir(parents=True, exist_ok=True)
        stem = time.strftime("eingang-%Y%m%d-%H%M%S")
        target = folder / f"{stem}.{ext}"
        n = 1
        while target.exists():
            n += 1
            target = folder / f"{stem}-{n}.{ext}"
        target.write_bytes(raw)
        log.info("Bild angehängt: %s (%d KB)", target, len(raw) // 1024)
        return {"ok": True, "path": str(target), "rel": str(target.relative_to(ws))}

    def choose_music(self) -> dict[str, Any]:
        return self._experience.select_music()

    def hide_panel(self) -> None:
        """Standby: nur der (schlafende) Orb bleibt sichtbar."""
        if self._window is not None and self._panel_open:
            self.toggle_panel()
        self._js("jarvis.onPanel", {"open": self._panel_open, "side": self._side})

    def show_panel(self) -> None:
        if self._window is not None and not self._panel_open:
            self.toggle_panel()
        self._js("jarvis.onPanel", {"open": self._panel_open, "side": self._side})

    def get_config(self) -> dict[str, Any]:
        p = self._cfg.profile
        return {
            "orb": self._cfg.orb,
            "orb_px": self._orb_px,
            "zone": _zone(self._orb_px),
            "profile": p.name,
            "models": {"general": p.general, "coder": p.coder, "heavy": p.heavy},
            "heavy_available": p.heavy is not None,
            "window_bg": self._cfg.window_bg,
        }

    def get_status(self) -> dict[str, Any]:
        return self._status_payload()

    def send_message(self, text: str, force_7b: bool = False,
                     speak_back: bool = False, voice_origin: bool = False) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"stream_id": None}
        self._seq += 1
        sid = f"s{self._seq}"
        t = threading.Thread(
            target=self._run_stream,
            args=(sid, text, bool(force_7b), bool(speak_back), bool(voice_origin)),
            name=f"jarvis-chat-{sid}", daemon=True)
        self._workers.append(t)
        t.start()
        return {"stream_id": sid}

    def reset_chat(self) -> dict[str, Any]:
        return self.new_chat()

    def new_chat(self) -> dict[str, Any]:
        """Neuer Chat: laufende Aktion abbrechen, Verlauf und Kontextanzeige zurücksetzen."""
        with self._chat_generation_lock:
            self._chat_generation += 1
            self._filler_epoch += 1
            if self._tasks is not None:
                self._tasks.cancel()
            self._hub.stop_speaking()
            self._chat.reset()
            if self._rt is not None:            # leerer Verlauf ⇒ Fremdinhalt wirkt nicht mehr nach
                self._rt.ctx.services.pop("conversation_taint", None)
        self._last_tps = None
        self._push_status()
        return {"ok": True, "context": self._chat.context_info()}

    def set_mute(self, muted: bool) -> dict[str, Any]:
        """Mikrofon stumm: der Daemon schließt den Audiostream, Jarvis hört nichts mehr."""
        self._muted = bool(muted)
        self._hub.set_muted(self._muted)
        self._push_status()
        return {"muted": self._muted, "daemon": self._hub.connected}

    def _context_safe(self):
        try:
            return self._chat.context_info()
        except Exception:  # noqa: BLE001 — Anzeige darf den Status nie sprengen
            return None

    def get_context(self) -> dict[str, Any]:
        return self._chat.context_info()

    def confirm_action(self, action_id: str, approve: bool) -> dict[str, Any]:
        """Zustimmung/Ablehnung für genau eine angezeigte Aktion."""
        if self._voice_confirm == str(action_id):
            self._voice_confirm = None                # im Panel entschieden ⇒ kein späteres „Ja“ mehr
        return {"ok": self._runtime().confirms.resolve(str(action_id), bool(approve))}

    def cancel(self, cancel_experience: bool = True) -> dict[str, Any]:
        """Stopp-Knopf: alle laufenden Aufgaben und die Sprachausgabe beenden."""
        with self._chat_generation_lock:
            self._chat_generation += 1      # Stopp bricht auch laufende Antworten ab
            self._filler_epoch += 1
        if self._tasks is not None:
            self._tasks.cancel()
        if self._rt is not None:
            self._rt.agent.cancel()
        cancel_chat = getattr(self._chat, "cancel", None)
        if cancel_chat:
            cancel_chat()
        self._hub.stop_speaking()
        if cancel_experience:
            self._experience.cancel()
        self._js("jarvis.onCancelled")
        return {"ok": True}

    def push_to_talk(self) -> dict[str, Any]:
        return {"sent": self._hub.push_to_talk()}

    def toggle_panel(self) -> dict[str, Any]:
        """Panel auf-/zuklappen; Fenster wird passend vergrößert/verschoben."""
        if self._window is None:
            return {"open": self._panel_open, "side": self._side}
        if self._panel_open:
            self._apply_geometry(opened=False)
            self._panel_open = False
        else:
            self._side = self._pick_side()
            self._apply_geometry(opened=True)
            self._panel_open = True
        self._write_layout()
        return {"open": self._panel_open, "side": self._side}

    def resize_orb(self, direction: int) -> dict[str, Any]:
        """Mausrad über dem Orb: Größe in 12-px-Schritten, Orb-Zentrum bleibt fix."""
        new_px = max(ORB_MIN, min(ORB_MAX, self._orb_px + (12 if direction > 0 else -12)))
        if new_px != self._orb_px and self._window is not None:
            center = self._orb_center()  # noch mit alter Größe bestimmen
            self._orb_px = new_px
            self._apply_geometry(opened=self._panel_open, center=center)
            self._write_layout()
        return {"orb_px": self._orb_px, "zone": _zone(self._orb_px)}

    def save_layout(self) -> dict[str, Any]:
        self._write_layout()
        return {"ok": True}

    def quit_app(self) -> None:
        self._stop.set()
        self._write_layout()
        if self._window is not None:
            threading.Thread(target=self._window.destroy, daemon=True).start()

    # -------------------------------------------------- Chat-Streaming

    def _run_stream(self, sid: str, text: str, force_7b: bool,
                    speak_back: bool = False, voice_origin: bool = False) -> None:
        command = lifecycle_command(text)
        if command:
            result = self.request_experience(command)
            if voice_origin:
                self._hub.say(result["message"])     # auch per Stimme bestätigen („Ja, Sir?“, „Standby, Sir.“)
            else:
                self._js("jarvis.onToken", sid, result["message"])
                self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})
            return
        if self._experience.sleeping:
            self._reply_plain(sid, "Ich bin im Standby, Sir. Sagen Sie ‚Jarvis, wake up‘.", False)
            return
        if self._experience.running:
            self._reply_plain(sid, "Die Startsequenz läuft noch, Sir. Sie können sie jederzeit abbrechen.", False)
            return
        self._auto_learn(text)                         # „Ich heiße …“, „Ich mag …“ → Fakt merken (nur Nutzertext)
        generation = self._chat_generation
        # Reihenfolge: Werkzeug-Schnellpfad (App öffnen, „merk dir“) → Browser-Schnellpfad →
        # Werkzeug-Agent (mehrstufig, LLM) → normaler Chat.
        import re as _re
        if _re.match(r"^(?:bitte\s+)?(?:starte\s+)?(?:einen?\s+)?neu(?:er|en|es)\s+(?:chat|gespr[äa]ch)\b", text.strip(), _re.I):
            self.new_chat()
            self._js("jarvis.onNewChat")
            msg = "Neuer Chat, Sir. Der Verlauf ist leer."
            if voice_origin:
                self._hub.say(msg)
            else:
                self._js("jarvis.onToken", sid, msg)
                self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})
            return
        # „Ja, mach das“ / „bitte“ nach einem Angebot: die vorherige Frage ausführen statt ins Leere zu laufen
        if _AFFIRM.match(text.strip()) and self._offer_pending():
            prev = self._previous_user_request()
            if prev:
                log.info("Zustimmung %r → führe aus: %r", text.strip(), prev)
                text = prev
                if not (self._cfg.tools.enabled and (fast_path(text) or self._runtime().agent.wants_tools(text))
                        or browser_intent.detect_all(text)):
                    self._reply_plain(sid, "Dafür habe ich leider kein Werkzeug, Sir.", voice_origin)
                    return
        low = text.strip().lower().rstrip("?!. ")
        if _re.search(r"\b(was machst du (gerade|jetzt|eigentlich)|woran arbeitest du|laufende aufgaben|aufgabenstatus|"
                      r"wie weit bist du|bist du fertig)\b", low):
            self._reply_plain(sid, self._task_manager().describe(), voice_origin)
            return
        m = _re.match(r"^(?:bitte\s+)?(?:brich|breche)\s+(alles|aufgabe\s*(\d+)|das|die aufgabe)\s*ab$|^alles abbrechen$", low)
        if m:
            tid = int(m.group(2)) if m.group(2) else None
            n = self._task_manager().cancel(tid)
            self._reply_plain(sid, f"{n} Aufgabe{'n' if n != 1 else ''} abgebrochen, Sir." if n else
                              "Es läuft gerade nichts, was ich abbrechen könnte, Sir.", voice_origin)
            return
        tools_on = self._cfg.tools.enabled
        if generation != self._chat_generation:
            return
        if tools_on and fast_path(text):
            self._run_tool_stream(sid, text, speak_back, voice_origin, generation)
            return
        if self._cfg.browser.enabled and browser_intent.detect_all(text):
            self._run_browser_stream(sid, text, speak_back, voice_origin)
            return
        if tools_on and self._runtime().agent.wants_tools(text, self._quick_model()):
            self._run_tool_stream(sid, text, speak_back, voice_origin, generation)
            return

        # Sprach-Anfrage (voice_origin): NICHT vorab ins Panel streamen — der
        # Daemon zeigt die Antwort synchron zur Stimme (Captions) und treibt die
        # Orb-Zustände selbst. Getippte Anfrage: Tokens live ins Panel wie bisher.
        buf: list[str] = []
        last_flush = 0.0
        answered = threading.Event()                  # erste gesprochene Antwort ⇒ kein Füllsatz mehr
        def say_current(line):
            if generation == self._chat_generation:
                self._hub.say(line)
        speaker = _SentenceSpeaker(say_current, on_first=answered.set) if speak_back else None
        if speak_back:
            self._start_filler(text, answered)

        def flush(force: bool = False):
            nonlocal last_flush
            now = time.monotonic()
            if buf and (force or now - last_flush >= 0.03):
                self._js("jarvis.onToken", sid, "".join(buf))
                buf.clear()
                last_flush = now

        try:
            for ev in self._chat.send(text, force_7b, model_override=self._quick_model()):
                if generation != self._chat_generation:
                    answered.set()
                    return
                kind = ev.get("type")
                if kind == "token":
                    if speaker is not None:
                        speaker.feed(ev["content"])
                    if not voice_origin:
                        buf.append(ev["content"])
                        flush()
                elif kind == "state":
                    if not voice_origin:
                        flush(force=True)
                        self._js("jarvis.onState", ev["value"], None)
                elif kind == "route":
                    self._last_model = ev["model"]
                    self._heavy_active = ev["lane"] in ("heavy", "reasoning")
                    if not voice_origin:
                        self._js("jarvis.onMeta", sid, ev)
                    self._push_status()
                elif kind == "reasoning":
                    if not voice_origin:
                        self._js("jarvis.onMeta", sid, ev)
                else:  # swap / done / error
                    if not voice_origin:
                        flush(force=True)
                    if kind == "done":
                        self._last_tps = ev.get("tps")
                        self._heavy_active = False
                        if speaker is not None:
                            speaker.finish()              # Rest der Antwort (letzter Satz)
                        answered.set()
                    elif kind == "error":
                        self._heavy_active = False
                        answered.set()
                        if speak_back and ev.get("fatal"):
                            self._hub.say(ev.get("message", ""))
                    if not voice_origin:
                        self._js("jarvis.onMeta", sid, ev)
                    if kind in ("done", "error"):
                        self._push_status()
        except Exception:
            answered.set()
            if generation != self._chat_generation:
                return
            log.exception("Stream %s abgestürzt", sid)
            if voice_origin:
                self._hub.say("Ein interner Fehler, Sir. Die Details stehen im Protokoll.")
            else:
                flush(force=True)
                self._js("jarvis.onMeta", sid,
                         {"type": "error", "fatal": True,
                          "message": "Ein interner Fehler, Sir. Details im Protokoll."})
                self._js("jarvis.onState", "idle", None)

    def _run_browser_stream(self, sid: str, text: str,
                            speak_back: bool, voice_origin: bool) -> None:
        """Browser-Befehl über den BrowserAgent. Aktions-Ansagen + Abschluss
        werden bei Sprache gesprochen (Daemon zeigt sie synchron als Caption),
        bei getippter Eingabe als Text ins Panel geschrieben."""
        generation = self._chat_generation
        try:
            agent = self._ensure_browser_agent()
        except Exception:
            log.exception("BrowserAgent nicht initialisierbar")
            msg = "Ich erreiche den Browser nicht, Sir."
            if voice_origin:
                self._hub.say(msg)
            else:
                self._js("jarvis.onToken", sid, msg)
                self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})
                self._js("jarvis.onState", "idle", None)
            return
        try:
            for ev in agent.run(text):
                if generation != self._chat_generation:
                    return
                kind = ev.get("type")
                spoken = ev.get("text", "")
                if kind == "action":
                    if voice_origin:
                        self._hub.say(spoken)
                    else:
                        self._js("jarvis.onToken", sid, spoken + "\n")
                elif kind in ("done", "error"):
                    if voice_origin:
                        if speak_back and spoken:
                            self._hub.say(spoken)
                    else:
                        self._js("jarvis.onToken", sid, spoken)
                        self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})
        except Exception:
            log.exception("Browser-Stream %s abgestürzt", sid)
            if generation != self._chat_generation:
                return
            if voice_origin:
                self._hub.say("Im Browser ging etwas schief, Sir.")
            else:
                self._js("jarvis.onToken", sid, "Im Browser ging etwas schief, Sir.")
                self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})
        finally:
            if generation == self._chat_generation and not voice_origin:
                self._js("jarvis.onState", "idle", None)

    def _offer_pending(self) -> bool:
        """„Ja“ gilt nur, wenn Jarvis zuletzt etwas ANGEBOTEN hat — nicht nach einer erledigten Aktion,
        nicht während Aufgaben laufen oder auf Bestätigung warten (dort ist „ja“ mehrdeutig)."""
        if self._tasks is not None and self._tasks.active():
            return False
        h = self._chat.history
        if not h or h[-1]["role"] != "assistant":
            return False
        last = h[-1]["content"].strip().lower()
        return last.endswith("?") or bool(_re.search(
            r"\b(soll ich|kann ich|darf ich|möchten sie|wünschen sie|wollen sie|ich kann (es|das|sie|ihnen)|"
            r"falls sie (möchten|wünschen)|wenn sie (möchten|wünschen|wollen))\b", last))

    def _previous_user_request(self) -> str:
        """Letzte Nutzerfrage, auf die das Angebot folgte (Verlauf endet auf [… Nutzer, Jarvis])."""
        h = self._chat.history
        for i in range(len(h) - 1, -1, -1):
            if h[i]["role"] == "user":
                return h[i]["content"]
        return ""

    def _reply_plain(self, sid: str, msg: str, voice_origin: bool) -> None:
        """Kurze, deterministische Antwort (kein Modell)."""
        if voice_origin:
            self._hub.say(msg)
        else:
            self._js("jarvis.onToken", sid, msg)
            self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})

    def _run_tool_stream(self, sid: str, text: str, speak_back: bool, voice_origin: bool,
                         expected_generation: int | None = None) -> None:
        """Werkzeug-Auftrag als eigene, parallele Aufgabe: Aktionen werden angezeigt (bzw. angesagt),
        Bestätigungen erscheinen im Panel, der Abschluss stammt aus den tatsächlichen Ergebnissen."""
        with self._chat_generation_lock:
            generation = self._chat_generation if expected_generation is None else expected_generation
            if generation != self._chat_generation:
                return
            tm = self._task_manager()
            task = tm.begin(text, voice=voice_origin)
        parallel = len(tm.active()) > 1
        final = ""
        asked: Optional[str] = None                  # per Stimme gestellte, noch offene Rückfrage
        answered = threading.Event()
        if voice_origin and speak_back:
            self._start_filler(text, answered)
        self._js("jarvis.onState", "think", None)
        try:
            for ev in task.agent.run(text, history=list(self._chat.history[-6:])):
                if generation != self._chat_generation:
                    return
                kind = ev.get("type")
                if kind == "reasoning":
                    if ev.get("model"):                 # Statusleiste zeigt das Modell, das wirklich arbeitet
                        self._last_model = ev["model"]
                        self._push_status()
                    if ev.get("active"):
                        tm.update(task, current="denkt nach und prüft den Auftrag", status="running")
                        if not voice_origin:
                            self._js("jarvis.onMeta", sid, ev)
                elif kind == "action":
                    tm.update(task, current=_re.sub(r",?\s*Sir\.?$", "", ev["text"]), status="running")
                    pre = f"Aufgabe {task.id}: " if len(tm.active()) > 1 else ""   # Präfix, sobald parallel
                    if voice_origin:
                        self._hub.say(pre + ev["text"])
                    else:
                        self._js("jarvis.onAction", sid, pre + ev["text"], None)
                elif kind == "confirm":
                    tm.update(task, current="wartet auf Ihre Bestätigung", status="confirm")
                    self._js("jarvis.onConfirm", ev)
                    if voice_origin:
                        # Das Gesprächsfenster des Daemons hört danach zu: „Ja“/„Nein“ entscheidet.
                        self._voice_confirm = asked = ev["id"]
                        answered.set()                    # keine Füllsätze, während Sir antworten soll
                        self._hub.say(ev["summary"][:200].rstrip(". ") + ". Soll ich das machen, Sir? Ja oder nein.")
                elif kind == "result":
                    if asked is not None and self._voice_confirm == asked:
                        self._voice_confirm = None       # entschieden oder verfallen: kein spätes „Ja“ mehr
                    asked = None
                    tm.update(task, current=f"{ev['tool']}: {'fertig' if ev['ok'] else 'Fehler'}", status="running")
                    if not voice_origin:
                        self._js("jarvis.onAction", sid, f"{ev['tool']}: {ev['text'][:160]}".replace("\n", " "), bool(ev["ok"]))
                elif kind == "done":
                    answered.set()
                    final = ev["text"]
                    tm.finish(task, bool(ev.get("ok")), final)
                    if voice_origin:
                        if speak_back:
                            self._hub.say(("Aufgabe %d fertig. " % task.id if parallel and task.elapsed() > 5 else "") + final)
                    else:
                        self._js("jarvis.onToken", sid, final)
                        self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})
        except Exception:
            log.exception("Werkzeug-Stream %s abgestürzt", sid)
            final = "Bei der Ausführung ist ein interner Fehler aufgetreten, Sir. Details im Protokoll."
            tm.finish(task, False, final)
            if generation != self._chat_generation:
                return
            if voice_origin:
                self._hub.say(final)
            else:
                self._js("jarvis.onToken", sid, final)
                self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})
        finally:
            answered.set()
            if task.active:
                tm.finish(task, False, final or "Beendet")
            with self._chat_generation_lock:
                if generation == self._chat_generation:
                    self._chat.remember_exchange(text, final)
            if generation == self._chat_generation and not voice_origin and not tm.active():
                self._js("jarvis.onState", "idle", None)

    # ----------------------------------------------------- Status-Loop

    def _status_loop(self) -> None:
        tick = 0
        while not self._stop.wait(STATUS_INTERVAL):
            tick += 1
            if self._online is not False or tick % OFFLINE_PING_EVERY == 0:
                online = self._client.ping()
                if online != self._online:
                    self._online = online
                    log.info("Mainframe %s", "online" if online else "offline")
            self._push_status()

    def _status_payload(self) -> dict[str, Any]:
        p = self._cfg.profile
        heavy = "AKTIV" if self._heavy_active else (
            "BEREIT" if p.heavy else "NICHT VERFÜGBAR")
        return {
            **self._sysmon.status(),
            "model": self._last_model or p.general,
            "tps": self._last_tps,
            "heavy": heavy,
            "heavy_available": p.heavy is not None,
            "online": self._online,
            "daemon": self._hub.connected,
            "muted": self._muted,
            "tasks": self._tasks.snapshot() if self._tasks is not None else [],
            "context": self._context_safe(),
        }

    def _push_status(self) -> None:
        self._js("jarvis.onStatus", self._status_payload())

    # ------------------------------------------------ Daemon-Callbacks

    def _daemon_muted(self, muted: bool) -> None:
        """Bestätigung des Daemons (z. B. nach Neustart)."""
        self._muted = bool(muted)
        self._push_status()

    def _daemon_state(self, state: str, amplitude: Optional[float]) -> None:
        self._experience.voice_state(state)
        if self._experience.sleeping:
            return
        self._js("jarvis.onState", state, amplitude)

    def _daemon_clients(self, connected: bool) -> None:
        self._js("jarvis.onDaemon", connected)
        if connected:
            self._experience.voice_connected()
        else:
            self._experience.voice_disconnected()

    def _daemon_utterance(self, text: str) -> None:
        """Erkannter Sprachbefehl: im Panel zeigen, beantworten, Antwort vorlesen.
        voice_origin=True ⇒ die Antwort erscheint im Panel synchron zur Stimme."""
        log.info("Sprachbefehl: %s", text)
        self._js("jarvis.onUserMessage", text)
        if self._answer_voice_confirm(text):
            return
        self.send_message(text, force_7b=False, speak_back=True, voice_origin=True)

    def _answer_voice_confirm(self, text: str) -> bool:
        """Offene Bestätigung + gesprochenes Ja/Nein ⇒ entscheiden statt neuen Auftrag starten."""
        action_id = self._voice_confirm
        decision = voice_decision(text) if action_id else None
        if decision is None:
            return False
        self._voice_confirm = None
        if not self._runtime().confirms.resolve(action_id, decision):
            self._hub.say("Diese Rückfrage ist schon abgelaufen, Sir. Sagen Sie den Auftrag bitte noch einmal.")
            return True
        log.info("Bestätigung per Stimme: %s", "ja" if decision else "nein")
        self._js("jarvis.onConfirmResolved", action_id, decision)
        return True

    def _daemon_caption(self, text: str, duration: Optional[float] = None) -> None:
        """Eine gerade gesprochene Sentence → synchron ins Panel (getippt über die Sprechdauer)."""
        self._js("jarvis.onCaption", text, duration)

    def _daemon_briefing(self) -> None:
        """Doppelklatsch/Sprachbefehl vom Daemon → Tagesbriefing."""
        self.request_briefing()

    # ----------------------------------------------------- Tagesbriefing

    def _ensure_briefing(self) -> BriefingService:
        if self._briefing is None:
            self._briefing = BriefingService(self._cfg, self._sysmon)
        return self._briefing

    def request_briefing(self) -> dict[str, Any]:
        if self._experience.sleeping or self._experience.running:
            return {"ok": False, "message": "Jarvis ist im Standby oder startet gerade."}
        t = threading.Thread(target=self._run_briefing, name="jarvis-briefing", daemon=True)
        self._workers.append(t)
        t.start()
        return {"ok": True}

    def _run_briefing(self) -> None:
        self._js("jarvis.onState", "think", None)
        try:
            data = self._ensure_briefing().collect()
        except Exception:
            log.exception("Briefing-Sammeln fehlgeschlagen")
            data = None
        if not data:
            msg = "Das Tagesbriefing erreiche ich gerade nicht, Sir."
            if self._hub.connected:
                self._hub.say(msg)
            else:
                self._js("jarvis.onToken", "s-brief", msg)
            self._js("jarvis.onState", "idle", None)
            return
        self._js("jarvis.onBriefing", data)
        for line in build_script(data):
            if self._hub.connected:
                self._hub.say(line)
            else:
                self._js("jarvis.onToken", "s-brief", line + " ")
        self._js("jarvis.onState", "idle", None)

    # -------------------------------------------------- Fenster-Mathe

    def _pick_side(self) -> str:
        """Panel rechts vom Orb, außer der Platz reicht nicht — dann links."""
        try:
            import webview
            screen = webview.screens[0]
            win = self._window
            assert win is not None
            right_edge = win.x + win.width + GAP + PANEL_W
            return "left" if right_edge > screen.width else "right"
        except Exception:
            return "right"

    def _orb_center(self) -> tuple[int, int]:
        w = self._window
        assert w is not None, "Fenster-Mathe nur nach on_started"
        zone = _zone(self._orb_px)
        cy = w.y + w.height // 2
        if self._panel_open and self._side == "left":
            return w.x + w.width - zone // 2, cy
        return w.x + zone // 2, cy

    def _apply_geometry(self, opened: bool,
                        center: tuple[int, int] | None = None) -> None:
        """Fenstergröße/-position so setzen, dass das Orb-Zentrum stehen bleibt."""
        cx, cy = center or self._orb_center()
        zone = _zone(self._orb_px)
        if opened:
            w = zone + GAP + PANEL_W
            h = max(zone, PANEL_MIN_H)
        else:
            w = h = zone
        if opened and self._side == "left":
            x = cx + zone // 2 - w   # Orb sitzt am rechten Fensterrand
        else:
            x = cx - zone // 2       # Orb sitzt links (bzw. Fenster = Orb-Zone)
        y = cy - h // 2
        win = self._window
        assert win is not None, "Fenster-Mathe nur nach on_started"
        win.resize(w, h)
        win.move(max(0, x), max(0, y))

    # ------------------------------------------------------ Persistenz

    def load_layout(self) -> dict[str, Any]:
        """Beim Start: gespeicherte Orb-Größe/-Position (Orb-Zentrum) lesen."""
        try:
            data = json.loads(self._layout_path.read_text(encoding="utf-8"))
            self._orb_px = max(ORB_MIN, min(ORB_MAX, int(data.get("orb_px", self._orb_px))))
            return {"cx": int(data["cx"]), "cy": int(data["cy"]), "orb_px": self._orb_px}
        except Exception:
            return {"cx": None, "cy": None, "orb_px": self._orb_px}

    def _write_layout(self) -> None:
        if self._window is None:
            return
        try:
            cx, cy = self._orb_center()
            self._layout_path.write_text(json.dumps(
                {"cx": cx, "cy": cy, "orb_px": self._orb_px}), encoding="utf-8")
        except Exception as exc:
            log.debug("Layout nicht gespeichert: %s", exc)

    # ------------------------------------------------------- JS-Aufrufe

    def _js(self, fn: str, *args: Any) -> None:
        if self._window is None:
            return
        try:
            payload = ",".join(
                json.dumps(a, ensure_ascii=True, default=str) for a in args)
            self._window.evaluate_js(f"window.{fn} && window.{fn}({payload})")
        except Exception as exc:
            log.debug("evaluate_js %s fehlgeschlagen: %s", fn, exc)
