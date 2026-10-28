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
    def __init__(self, url: str, port_tries: int = 10):
        self.url = url
        self._candidates = _candidate_urls(url, port_tries)   # App weicht bei belegtem Port aus
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.on_push_to_talk: Callable[[], None] = lambda: None
        self.on_speak: Callable[[str], None] = lambda _t: None
        self.on_stop: Callable[[], None] = lambda: None
        self.on_mute: Callable[[bool], None] = lambda _m: None

    def send_muted(self, value: bool) -> None:
        self._send({"type": "muted", "value": bool(value)})

    # ---------------------------------------------------------- Senden
    # (thread-sicher — aufrufbar aus Ears-/Worker-Threads)

    def send_state(self, value: str, amplitude: float | None = None) -> None:
        msg: dict = {"type": "state", "value": value}
        if amplitude is not None:
            msg["amplitude"] = round(float(amplitude), 3)
        self._send(msg)

    def send_utterance(self, text: str) -> None:
        self._send({"type": "utterance", "text": text})

    def send_caption(self, text: str, duration: float | None = None) -> None:
        """Eine gerade gesprochene Sentence — die App tippt sie passend zur Sprechdauer ins Panel."""
        msg: dict = {"type": "caption", "text": text}
        if duration is not None:
            msg["duration"] = round(float(duration), 2)
        self._send(msg)

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
        """Hält die Verbindung zur App; läuft für immer (Reconnect alle 3 s).
        Probiert den konfigurierten Port und die Ausweichports der App der Reihe nach."""
        self._loop = asyncio.get_running_loop()
        announced = False
        while True:
            for url in self._candidates:
                try:
                    announced = await self._session(url) or announced
                except (OSError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                    if not announced:
                        log.info("App unter %s nicht erreichbar (%s) — versuche es weiter …", url, exc)
                finally:
                    self._ws = None
            announced = True
            await asyncio.sleep(3)

    async def _session(self, url: str) -> bool:
        async with connect(url, open_timeout=3) as ws:
            # Fremde Dienste auf dem Port antworten nicht mit unserem Protokoll → kein Handshake-Erfolg
            self._ws = ws
            self.url = url
            log.info("Mit J.A.R.V.I.S.-App verbunden (%s)", url)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = msg.get("type")
                if kind == "push_to_talk":
                    self.on_push_to_talk()
                elif kind == "stop":
                    self.on_stop()
                elif kind == "mute":
                    self.on_mute(bool(msg.get("value")))
                elif kind == "speak":
                    text = str(msg.get("text", "")).strip()
                    if text:
                        self.on_speak(text)
        return False


def _candidate_urls(url: str, tries: int) -> list[str]:
    """ws://host:port → [port, port+1, …] (Gegenstück zu VoiceHub._serve)."""
    from urllib.parse import urlparse
    u = urlparse(url)
    base = u.port or 8765
    return [f"{u.scheme}://{u.hostname}:{p}" for p in range(base, base + tries)]
