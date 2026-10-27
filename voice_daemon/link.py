"""Link: WebSocket-Client zur Desktop-App (ws://127.0.0.1:8765), mit Reconnect.

Sendet:    {"type":"state",...} · {"type":"utterance","text":...}
Empfängt:  {"type":"push_to_talk"} · {"type":"speak","text":...}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Optional

from websockets.asyncio.client import connect

log = logging.getLogger("daemon.link")


class Link:
    def __init__(self, url: str):
        self.url = url
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.on_push_to_talk: Callable[[], None] = lambda: None
        self.on_speak: Callable[[str], None] = lambda _t: None

    # ---------------------------------------------------------- Senden
    # (thread-sicher — aufrufbar aus Ears-/Worker-Threads)

    def send_state(self, value: str, amplitude: float | None = None) -> None:
        msg: dict = {"type": "state", "value": value}
        if amplitude is not None:
            msg["amplitude"] = round(float(amplitude), 3)
        self._send(msg)

    def send_utterance(self, text: str) -> None:
        self._send({"type": "utterance", "text": text})

    def send_caption(self, text: str) -> None:
        """Eine gerade gesprochene Sentence — die App zeigt sie synchron im Panel."""
        self._send({"type": "caption", "text": text})

    def send_briefing(self) -> None:
        """Löst in der App das Tagesbriefing aus (Doppelklatsch / Sprachbefehl)."""
        self._send({"type": "briefing"})

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def _send(self, msg: dict) -> None:
        if self._ws is None or self._loop is None:
            log.debug("Nicht verbunden — Nachricht verworfen: %s", msg.get("type"))
            return
        asyncio.run_coroutine_threadsafe(self._async_send(msg), self._loop)

    async def _async_send(self, msg: dict) -> None:
        try:
            if self._ws is not None:
                await self._ws.send(json.dumps(msg))
        except Exception as exc:
            log.debug("Senden fehlgeschlagen: %s", exc)

    # ------------------------------------------------------- Verbindung

    async def run(self) -> None:
        """Hält die Verbindung zur App; läuft für immer (Reconnect alle 3 s)."""
        self._loop = asyncio.get_running_loop()
        announced = False
        while True:
            try:
                async with connect(self.url) as ws:
                    self._ws = ws
                    log.info("Mit J.A.R.V.I.S.-App verbunden (%s)", self.url)
                    announced = False
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        kind = msg.get("type")
                        if kind == "push_to_talk":
                            self.on_push_to_talk()
                        elif kind == "speak":
                            text = str(msg.get("text", "")).strip()
                            if text:
                                self.on_speak(text)
            except (OSError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                if not announced:
                    log.info("App nicht erreichbar (%s) — versuche es weiter …", exc)
                    announced = True
            finally:
                self._ws = None
            await asyncio.sleep(3)
