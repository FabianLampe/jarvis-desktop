"""WebSocket-Schnittstelle für den Sprach-Daemon.

Protokoll (JSON pro Nachricht):
  Daemon → App:  {"type": "state", "value": "listening"|"thinking"|"speaking"|"idle",
                  "amplitude": 0.0–1.0 (optional, nur beim Sprechen)}
                 {"type": "utterance", "text": "erkannter deutscher Satz"}
  App → Daemon:  {"type": "push_to_talk"}
                 {"type": "speak", "text": "Antwort, die vorgelesen werden soll"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Callable, Optional

from websockets.asyncio.server import serve

log = logging.getLogger("jarvis.ws")

_STATE_ALIASES = {
    "listening": "listen", "listen": "listen",
    "thinking": "think", "think": "think",
    "speaking": "speak", "speak": "speak",
    "idle": "idle",
}


class VoiceHub:
    """Hält Verbindungen zum (künftigen) Sprach-Daemon und reicht Zustände durch."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._clients: set = set()
        self._on_state: Callable[[str, Optional[float]], None] = lambda *_: None
        self._on_clients: Callable[[bool], None] = lambda *_: None
        self._on_utterance: Callable[[str], None] = lambda *_: None
        self._on_caption: Callable[[str], None] = lambda *_: None
        self._on_briefing: Callable[[], None] = lambda: None

    # ------------------------------------------------------------------ API

    @property
    def connected(self) -> bool:
        return bool(self._clients)

    def start(self,
              on_state: Callable[[str, Optional[float]], None],
              on_clients_changed: Callable[[bool], None],
              on_utterance: Callable[[str], None] | None = None,
              on_caption: Callable[[str], None] | None = None,
              on_briefing: Callable[[], None] | None = None) -> None:
        """Startet den Server in einem Daemon-Thread. Portkonflikt ⇒ nur Warnung."""
        self._on_state = on_state
        self._on_clients = on_clients_changed
        if on_utterance is not None:
            self._on_utterance = on_utterance
        if on_caption is not None:
            self._on_caption = on_caption
        if on_briefing is not None:
            self._on_briefing = on_briefing
        threading.Thread(target=self._run, name="jarvis-ws", daemon=True).start()

    def push_to_talk(self) -> bool:
        """Sendet Push-to-talk an alle verbundenen Daemons."""
        return self._send({"type": "push_to_talk"})

    def say(self, text: str) -> bool:
        """Bittet den Daemon, eine Antwort vorzulesen."""
        return self._send({"type": "speak", "text": text})

    def _send(self, payload: dict) -> bool:
        if not (self._loop and self._clients):
            return False
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)
        return True

    # ---------------------------------------------------------------- intern

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        except OSError as exc:
            log.warning("WebSocket-Server (%s:%s) nicht gestartet: %s",
                        self.host, self.port, exc)
        except Exception:
            log.exception("WebSocket-Server abgestürzt")

    async def _serve(self) -> None:
        async with serve(self._handler, self.host, self.port):
            log.info("Sprach-Daemon-Schnittstelle bereit: ws://%s:%s", self.host, self.port)
            await asyncio.Future()  # für immer

    async def _handler(self, ws) -> None:
        self._clients.add(ws)
        self._on_clients(True)
        log.info("Sprach-Daemon verbunden (%s)", ws.remote_address)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.debug("Unlesbare WS-Nachricht: %r", raw[:120])
                    continue
                kind = msg.get("type")
                if kind == "state":
                    state = _STATE_ALIASES.get(str(msg.get("value", "")).lower())
                    if state:
                        amp = msg.get("amplitude")
                        self._on_state(state, float(amp) if amp is not None else None)
                elif kind == "utterance":
                    text = str(msg.get("text", "")).strip()
                    if text:
                        self._on_utterance(text)
                elif kind == "caption":
                    text = str(msg.get("text", "")).strip()
                    if text:
                        self._on_caption(text)
                elif kind == "briefing":
                    self._on_briefing()
        finally:
            self._clients.discard(ws)
            self._on_clients(self.connected)
            log.info("Sprach-Daemon getrennt")

    async def _broadcast(self, payload: dict) -> None:
        data = json.dumps(payload)
        for ws in list(self._clients):
            try:
                await ws.send(data)
            except Exception:
                self._clients.discard(ws)
