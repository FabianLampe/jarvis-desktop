"""Ollama-HTTP-Client: Streaming-Chat, Laden/Entladen (Swap-Logik der Schwerlast-Spur).

Spricht ausschließlich die lokale Ollama-API (http://127.0.0.1:11434) — keine Cloud
(ADR 0002). Die Swap-Reihenfolge für die Schwerlast-Spur orchestriert chat.ChatService;
hier liegen die Bausteine: chat_stream / unload / preload / ping.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator, Optional

import requests

log = logging.getLogger("jarvis.ollama")


class OllamaError(Exception):
    """Basisklasse für Ollama-Fehler."""


class OllamaUnavailable(OllamaError):
    """Ollama-Server nicht erreichbar (Verbindung zum Mainframe unterbrochen)."""


class ModelMissing(OllamaError):
    """Angefragtes Modell ist nicht installiert (404)."""

    def __init__(self, model: str):
        self.model = model
        super().__init__(f"Modell fehlt: {model}")


class OllamaClient:
    def __init__(self, host: str = "http://127.0.0.1:11434",
                 connect_timeout: float = 5.0, read_timeout: float = 600.0):
        self.host = host.rstrip("/")
        self._timeout = (connect_timeout, read_timeout)
        self._session = requests.Session()

    # ---------------------------------------------------------------- intern

    def _post(self, path: str, payload: dict[str, Any], *, stream: bool = False,
              model_for_404: Optional[str] = None) -> requests.Response:
        try:
            r = self._session.post(self.host + path, json=payload,
                                   stream=stream, timeout=self._timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if r.status_code == 404 and model_for_404:
            raise ModelMissing(model_for_404)
        if not r.ok:
            raise OllamaError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r

    # ------------------------------------------------------------------ API

    def ping(self) -> bool:
        try:
            r = self._session.get(self.host + "/api/version", timeout=(2.0, 5.0))
            return r.ok
        except requests.RequestException:
            return False

    def chat_stream(self, model: str, messages: list[dict[str, str]], *,
                    num_ctx: int = 2048, keep_alive: str | int = "30m",
                    ) -> Iterator[dict[str, Any]]:
        """Streamt eine Chat-Antwort.

        Liefert {"token": str} pro Häppchen, abschließend
        {"done": True, "tps": float|None, "eval_count": int|None}.
        """
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"num_ctx": num_ctx},
            "keep_alive": keep_alive,
        }
        r = self._post("/api/chat", payload, stream=True, model_for_404=model)
        try:
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if err := data.get("error"):
                    # Ollama meldet fehlende Modelle teils als Fehlerobjekt im Stream
                    if "not found" in err.lower():
                        raise ModelMissing(model)
                    raise OllamaError(err)
                if content := (data.get("message") or {}).get("content"):
                    yield {"token": content}
                if data.get("done"):
                    tps = None
                    count, dur = data.get("eval_count"), data.get("eval_duration")
                    if count and dur:
                        tps = count / (dur / 1e9)
                    yield {"done": True, "tps": tps, "eval_count": count}
                    return
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as exc:
            raise OllamaUnavailable(f"Stream abgebrochen: {exc}") from exc
        finally:
            r.close()

    def chat_once(self, model: str, messages: list[dict], *,
                  tools: list[dict] | None = None, num_ctx: int = 2048,
                  keep_alive: str | int = "30m") -> dict:
        """Einmalige (nicht-streamende) Chat-Antwort. Gibt die message zurück
        (enthält ggf. tool_calls). Für den Tool-Loop des Browser-Agenten."""
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False,
            "options": {"num_ctx": num_ctx}, "keep_alive": keep_alive,
        }
        if tools:
            payload["tools"] = tools
        r = self._post("/api/chat", payload, stream=False, model_for_404=model)
        data = r.json()
        if err := data.get("error"):
            if "not found" in err.lower():
                raise ModelMissing(model)
            raise OllamaError(err)
        return data.get("message", {}) or {}

    def unload(self, model: str) -> bool:
        """Wirft ein Modell aus dem VRAM (keep_alive=0). Best effort."""
        try:
            self._post("/api/chat", {"model": model, "messages": [], "keep_alive": 0},
                       model_for_404=model)
            return True
        except OllamaError as exc:
            log.warning("Entladen von %s fehlgeschlagen: %s", model, exc)
            return False

    def preload(self, model: str, keep_alive: str | int = "30m") -> bool:
        """Lädt ein Modell in den VRAM, ohne eine Antwort zu erzeugen. Best effort."""
        try:
            self._post("/api/chat", {"model": model, "messages": [],
                                     "keep_alive": keep_alive}, model_for_404=model)
            return True
        except OllamaError as exc:
            log.warning("Vorladen von %s fehlgeschlagen: %s", model, exc)
            return False
