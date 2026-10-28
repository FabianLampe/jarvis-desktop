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


_AFFIRM = _re.compile(r"^(?:ja|jawohl|jep|okay|ok|gerne|gern|genau|los|bitte|na klar|klar|sehr gerne|"
                      r"(?:ja[, ]*)?(?:bitte[, ]*)?(?:mach|tu|erledige|hol|führ)\w*\s*(?:das|es|sie|ihn|dies)?"
                      r"(?:\s*(?:bitte|mal|gerne|aus|ein|raus|durch))?)[\s,.!]*(?:bitte|gerne|mal)?[\s.!]*$", _re.I)


class Bridge:
    def __init__(self, cfg: AppConfig,
                 chat: ChatService | None = None,
                 sysmon: SystemMonitor | None = None,
                 hub: VoiceHub | None = None,
                 layout_path: Path | None = None):
        self._cfg = cfg
        self._client = OllamaClient(cfg.ollama_host)
        self._platform = current_platform()
        self._rt: Optional[Runtime] = None
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
        self._last_tps: Optional[float] = None
        self._online: Optional[bool] = None
        self._seq = 0
        self._workers: list[threading.Thread] = []
        self._stop = threading.Event()
        self._browser_ctl: Optional[BrowserController] = None
        self._browser_agent: Optional[BrowserAgent] = None
        self._briefing: Optional[BriefingService] = None
        self._reminders: Optional[ReminderService] = None
        self._telegram = None
        self._muted = False
        self._tasks: Optional[TaskManager] = None

    # ------------------------------------------------ Werkzeug-Laufzeit

    def _runtime(self) -> Runtime:
        if self._rt is None:
            self._rt = build_runtime(self._cfg, self._client, interactive=True)
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
        if self._hub.connected:
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
                        on_muted=self._daemon_muted)
        threading.Thread(target=self._status_loop, name="jarvis-status", daemon=True).start()
        threading.Thread(target=self._warmup, name="jarvis-warmup", daemon=True).start()
        threading.Thread(target=self._task_monitor, name="jarvis-taskmon", daemon=True).start()
        if self._cfg.tools.enabled:
            for fn in (self._start_reminders, self._start_telegram):
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
                self._client.preload(q, self._cfg.keep_alive)
                log.info("Schnellspur-Modell vorgewärmt: %s", q)
        except Exception as exc:
            log.debug("Warmup übersprungen: %s", exc)

    def _on_closing(self):
        self._stop.set()
        self._write_layout()
        if self._browser_ctl is not None:
            self._browser_ctl.shutdown()
        if self._reminders is not None:
            self._reminders.shutdown()
        if self._telegram is not None:
            self._telegram.stop()
        if self._rt is not None:
            self._rt.agent.cancel()

    def _ensure_browser_agent(self) -> BrowserAgent:
        if self._browser_agent is None:
            b = self._cfg.browser
            self._browser_ctl = BrowserController(
                profile_dir=b.profile_dir, channel=b.channel, headless=b.headless)
            self._browser_agent = BrowserAgent(self._cfg, self._browser_ctl, self._client)
        return self._browser_agent

    # ------------------------------------------------------- JS → Python

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
        if self._tasks is not None:
            self._tasks.cancel()
        self._hub.stop_speaking()
        self._chat.reset()
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
        return {"ok": self._runtime().confirms.resolve(str(action_id), bool(approve))}

    def cancel(self) -> dict[str, Any]:
        """Stopp-Knopf: alle laufenden Aufgaben und die Sprachausgabe beenden."""
        if self._tasks is not None:
            self._tasks.cancel()
        if self._rt is not None:
            self._rt.agent.cancel()
        self._hub.stop_speaking()
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
        if tools_on and fast_path(text):
            self._run_tool_stream(sid, text, speak_back, voice_origin)
            return
        if self._cfg.browser.enabled and browser_intent.detect_all(text):
            self._run_browser_stream(sid, text, speak_back, voice_origin)
            return
        if tools_on and self._runtime().agent.wants_tools(text):
            self._run_tool_stream(sid, text, speak_back, voice_origin)
            return

        # Sprach-Anfrage (voice_origin): NICHT vorab ins Panel streamen — der
        # Daemon zeigt die Antwort synchron zur Stimme (Captions) und treibt die
        # Orb-Zustände selbst. Getippte Anfrage: Tokens live ins Panel wie bisher.
        buf: list[str] = []
        last_flush = 0.0

        def flush(force: bool = False):
            nonlocal last_flush
            now = time.monotonic()
            if buf and (force or now - last_flush >= 0.03):
                self._js("jarvis.onToken", sid, "".join(buf))
                buf.clear()
                last_flush = now

        try:
            for ev in self._chat.send(text, force_7b, model_override=self._quick_model()):
                kind = ev.get("type")
                if kind == "token":
                    if not voice_origin:
                        buf.append(ev["content"])
                        flush()
                elif kind == "state":
                    if not voice_origin:
                        flush(force=True)
                        self._js("jarvis.onState", ev["value"], None)
                elif kind == "route":
                    self._last_model = ev["model"]
                    self._heavy_active = ev["lane"] == "heavy"
                    if not voice_origin:
                        self._js("jarvis.onMeta", sid, ev)
                    self._push_status()
                else:  # swap / done / error
                    if not voice_origin:
                        flush(force=True)
                    if kind == "done":
                        self._last_tps = ev.get("tps")
                        self._heavy_active = False
                        if speak_back and ev.get("text"):
                            self._hub.say(ev["text"])
                    elif kind == "error":
                        self._heavy_active = False
                        if speak_back and ev.get("fatal"):
                            self._hub.say(ev.get("message", ""))
                    if not voice_origin:
                        self._js("jarvis.onMeta", sid, ev)
                    if kind in ("done", "error"):
                        self._push_status()
        except Exception:
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
            if voice_origin:
                self._hub.say("Im Browser ging etwas schief, Sir.")
            else:
                self._js("jarvis.onToken", sid, "Im Browser ging etwas schief, Sir.")
                self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})
        finally:
            if not voice_origin:
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

    def _run_tool_stream(self, sid: str, text: str, speak_back: bool, voice_origin: bool) -> None:
        """Werkzeug-Auftrag als eigene, parallele Aufgabe: Aktionen werden angezeigt (bzw. angesagt),
        Bestätigungen erscheinen im Panel, der Abschluss stammt aus den tatsächlichen Ergebnissen."""
        tm = self._task_manager()
        task = tm.begin(text, voice=voice_origin)
        parallel = len(tm.active()) > 1
        final = ""
        self._js("jarvis.onState", "think", None)
        try:
            for ev in task.agent.run(text, history=list(self._chat.history[-6:])):
                kind = ev.get("type")
                if kind == "action":
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
                        self._hub.say("Das braucht Ihre Bestätigung im Panel, Sir: " + ev["summary"][:200])
                elif kind == "result":
                    tm.update(task, current=f"{ev['tool']}: {'fertig' if ev['ok'] else 'Fehler'}", status="running")
                    if not voice_origin:
                        self._js("jarvis.onAction", sid, f"{ev['tool']}: {ev['text'][:160]}".replace("\n", " "), bool(ev["ok"]))
                elif kind == "done":
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
            if voice_origin:
                self._hub.say(final)
            else:
                self._js("jarvis.onToken", sid, final)
                self._js("jarvis.onMeta", sid, {"type": "done", "tps": None})
        finally:
            if task.active:
                tm.finish(task, False, final or "Beendet")
            self._chat.remember_exchange(text, final)
            if not voice_origin and not tm.active():
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
        self._js("jarvis.onState", state, amplitude)

    def _daemon_clients(self, connected: bool) -> None:
        self._js("jarvis.onDaemon", connected)

    def _daemon_utterance(self, text: str) -> None:
        """Erkannter Sprachbefehl: im Panel zeigen, beantworten, Antwort vorlesen.
        voice_origin=True ⇒ die Antwort erscheint im Panel synchron zur Stimme."""
        log.info("Sprachbefehl: %s", text)
        self._js("jarvis.onUserMessage", text)
        self.send_message(text, force_7b=False, speak_back=True, voice_origin=True)

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
