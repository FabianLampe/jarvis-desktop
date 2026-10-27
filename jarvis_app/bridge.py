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
import threading
import time
from pathlib import Path
from typing import Any, Optional

from jarvis_core import browser_intent
from jarvis_core.agent import BrowserAgent
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


class Bridge:
    def __init__(self, cfg: AppConfig,
                 chat: ChatService | None = None,
                 sysmon: SystemMonitor | None = None,
                 hub: VoiceHub | None = None,
                 layout_path: Path | None = None):
        self._cfg = cfg
        self._client = OllamaClient(cfg.ollama_host)
        self._chat = chat or ChatService(cfg, client=self._client)
        self._sysmon = sysmon or SystemMonitor(cfg.profile.gpu_monitor)
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

    # ----------------------------------------------------- Lebenszyklus

    def on_started(self, window) -> None:
        """webview.start-Callback: Fenster ist da, Hintergrunddienste starten."""
        self._window = window
        window.events.closing += self._on_closing
        self._hub.start(on_state=self._daemon_state,
                        on_clients_changed=self._daemon_clients,
                        on_utterance=self._daemon_utterance,
                        on_caption=self._daemon_caption,
                        on_briefing=self._daemon_briefing)
        threading.Thread(target=self._status_loop, name="jarvis-status", daemon=True).start()
        threading.Thread(target=self._warmup, name="jarvis-warmup", daemon=True).start()

    def _warmup(self) -> None:
        """Lädt das Allrounder-Modell vorab in den VRAM — sonst stockt der erste
        echte Befehl, während der Sprach-Daemon das VRAM mit XTTS teilt."""
        try:
            for _ in self._chat.client.chat_stream(
                self._cfg.profile.general, [{"role": "user", "content": "Hallo"}],
                num_ctx=self._cfg.profile.num_ctx, keep_alive=self._cfg.keep_alive):
                pass
            log.info("Allrounder-Modell vorgewärmt: %s", self._cfg.profile.general)
        except Exception as exc:
            log.debug("Warmup übersprungen: %s", exc)

    def _on_closing(self):
        self._stop.set()
        self._write_layout()
        if self._browser_ctl is not None:
            self._browser_ctl.shutdown()

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
        self._chat.reset()
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
        # Browser-Befehl? (Schnellpfad) → an den BrowserAgent statt ans Chat-Modell.
        if self._cfg.browser.enabled and browser_intent.detect_all(text):
            self._run_browser_stream(sid, text, speak_back, voice_origin)
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
            for ev in self._chat.send(text, force_7b):
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
        }

    def _push_status(self) -> None:
        self._js("jarvis.onStatus", self._status_payload())

    # ------------------------------------------------ Daemon-Callbacks

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

    def _daemon_caption(self, text: str) -> None:
        """Eine gerade gesprochene Sentence → synchron ins Panel."""
        self._js("jarvis.onCaption", text)

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
            right_edge = self._window.x + self._window.width + GAP + PANEL_W
            return "left" if right_edge > screen.width else "right"
        except Exception:
            return "right"

    def _orb_center(self) -> tuple[int, int]:
        w = self._window
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
        self._window.resize(w, h)
        self._window.move(max(0, x), max(0, y))

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
