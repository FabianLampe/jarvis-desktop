"""Ollama-HTTP-Client: Streaming-Chat, Laden/Entladen (Swap-Logik der Schwerlast-Spur).

Spricht ausschließlich die lokale Ollama-API (http://127.0.0.1:11434) — keine Cloud
(ADR 0002). Die Swap-Reihenfolge für die Schwerlast-Spur orchestriert chat.ChatService;
hier liegen die Bausteine: chat_stream / unload / preload / ping.
"""

from __future__ import annotations

import json
import logging
import time
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


class GenerationLimit(OllamaError):
    """Generierungsbudget erschöpft; eine Teilantwort ist kein fertiges Ergebnis."""


class GenerationCancelled(OllamaError):
    """Auftrag wurde abgebrochen; keine weiteren Werkzeuge ausführen."""


class OllamaClient:
    def __init__(self, host: str = "http://127.0.0.1:11434",
                 connect_timeout: float = 5.0, read_timeout: float = 600.0):
        self.host = host.rstrip("/")
        self._timeout = (connect_timeout, read_timeout)
        self._session = requests.Session()
        self.last_usage: dict | None = None     # {"prompt": int, "completion": int} der letzten Antwort
        self._thinking_controls: dict[str, dict] = {}

    # ---------------------------------------------------------------- intern

    def _post(self, path: str, payload: dict[str, Any], *, stream: bool = False,
              model_for_404: Optional[str] = None,
              max_time_s: float | None = None) -> requests.Response:
        timeout = self._timeout if max_time_s is None else (
            min(self._timeout[0], max_time_s), min(self._timeout[1], max_time_s))
        try:
            r = self._session.post(self.host + path, json=payload,
                                   stream=stream, timeout=timeout)
        except requests.Timeout as exc:
            if max_time_s is not None:
                raise GenerationLimit("Zeitlimit der Modellgenerierung erreicht") from exc
            raise OllamaUnavailable(str(exc)) from exc
        except requests.ConnectionError as exc:
            raise OllamaUnavailable(str(exc)) from exc
        if r.status_code == 404 and model_for_404:
            raise ModelMissing(model_for_404)
        if not r.ok:
            raise OllamaError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r

    def thinking_control(self, model: str, enabled: bool) -> bool | str | None:
        """Nur unterstützte think-Werte senden; ältere Ollama-Versionen bleiben nutzbar.

        Fehlgeschlagene Abfragen werden nicht gecacht (Ollama kann später wieder online sein).
        Ohne Metadaten bleibt der Modellstandard aktiv; es wird keine Fähigkeit erfunden.
        """
        if model not in self._thinking_controls:
            try:
                with self._session.post(self.host + "/api/show", json={"model": model},
                                        timeout=(2, 5)) as r:
                    if not r.ok:
                        return None
                    self._thinking_controls[model] = r.json()
            except (requests.RequestException, ValueError):
                return None
        info = self._thinking_controls[model]
        controls = info.get("thinking") or {}
        values = controls.get("values", [])
        if enabled:
            if True in values:
                return True
            names = [v for v in values if isinstance(v, str)]
            if names:
                default = controls.get("default")
                return "medium" if "medium" in names else default if default in names else names[0]
            if not values and "thinking" in info.get("capabilities", []):
                return True
        elif False in values:
            return False
        return None

    # ------------------------------------------------------------------ API

    def ping(self) -> bool:
        try:
            r = self._session.get(self.host + "/api/version", timeout=(2.0, 5.0))
            return r.ok
        except requests.RequestException:
            return False

    def chat_stream(self, model: str, messages: list[dict[str, str]], *,
                    num_ctx: int = 2048, keep_alive: str | int = "30m",
                    think: bool | str | None = None, num_predict: int | None = None,
                    max_time_s: float | None = None,
                    cancel=None,
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
        if think is not None:
            payload["think"] = think
        if num_predict is not None:
            payload["options"]["num_predict"] = num_predict
        started = time.monotonic()
        r = self._post("/api/chat", payload, stream=True, model_for_404=model, max_time_s=max_time_s)
        try:
            for line in r.iter_lines(chunk_size=1):
                if cancel is not None and cancel.is_set():
                    raise GenerationCancelled("Abgebrochen, Sir.")
                if max_time_s is not None and time.monotonic() - started >= max_time_s:
                    raise GenerationLimit("Zeitlimit der Modellgenerierung erreicht")
                if not line:
                    continue
                data = json.loads(line)
                if err := data.get("error"):
                    # Ollama meldet fehlende Modelle teils als Fehlerobjekt im Stream
                    if "not found" in err.lower():
                        raise ModelMissing(model)
                    raise OllamaError(err)
                message = data.get("message") or {}
                # Den Reasoning-Text weder vorlesen noch als Antwort-Token ausgeben.
                if message.get("thinking"):
                    yield {"thinking": True}
                if content := message.get("content"):
                    yield {"token": content}
                if data.get("done"):
                    tps = None
                    count, dur = data.get("eval_count"), data.get("eval_duration")
                    if count and dur:
                        tps = count / (dur / 1e9)
                    self.last_usage = {"prompt": data.get("prompt_eval_count") or 0, "completion": count or 0}
                    if data.get("done_reason") == "length":
                        raise GenerationLimit("Tokenlimit erreicht; die Antwort ist unvollständig")
                    yield {"done": True, "tps": tps, "eval_count": count}
                    return
            raise OllamaUnavailable("Stream ohne Abschluss abgebrochen")
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as exc:
            if max_time_s is not None and time.monotonic() - started >= max_time_s:
                raise GenerationLimit("Zeitlimit der Modellgenerierung erreicht") from exc
            raise OllamaUnavailable(f"Stream abgebrochen: {exc}") from exc
        finally:
            r.close()

    def chat_once(self, model: str, messages: list[dict], *,
                  tools: list[dict] | None = None, num_ctx: int = 2048,
                  keep_alive: str | int = "30m", num_predict: int | None = None,
                  temperature: float | None = None, think: bool | str | None = None,
                  max_time_s: float | None = None, cancel=None) -> dict:
        """Einmalige (nicht-streamende) Chat-Antwort. Gibt die message zurück
        (enthält ggf. tool_calls). Für den Tool-Loop des Browser-Agenten."""
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False,
            "options": {"num_ctx": num_ctx}, "keep_alive": keep_alive,
        }
        if num_predict is not None:
            payload["options"]["num_predict"] = num_predict
        if temperature is not None:
            payload["options"]["temperature"] = temperature
        if tools:
            payload["tools"] = tools
        if think is not None:
            payload["think"] = think
        started = time.monotonic()
        if max_time_s is not None or cancel is not None:
            # Auch Tool-Generierung streamen, damit Budget/Abbruch während des Denkens
            # geprüft werden können. Den vollständigen Dialog intern zusammensetzen.
            payload["stream"] = True
            message: dict[str, Any] = {"role": "assistant", "content": ""}
            complete = False
            with self._post("/api/chat", payload, stream=True, model_for_404=model,
                            max_time_s=max_time_s) as r:
                try:
                    for line in r.iter_lines(chunk_size=1):
                        if cancel is not None and cancel.is_set():
                            raise GenerationCancelled("Abgebrochen, Sir.")
                        if max_time_s is not None and time.monotonic() - started >= max_time_s:
                            raise GenerationLimit("Zeitlimit der Modellgenerierung erreicht")
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise OllamaError(data["error"])
                        chunk = data.get("message") or {}
                        for key in ("content", "thinking"):
                            if chunk.get(key):
                                message[key] = message.get(key, "") + chunk[key]
                        if chunk.get("tool_calls"):
                            message.setdefault("tool_calls", []).extend(chunk["tool_calls"])
                        if data.get("done"):
                            complete = True
                            break
                except (requests.ConnectionError, requests.Timeout,
                        requests.exceptions.ChunkedEncodingError) as exc:
                    if max_time_s is not None and time.monotonic() - started >= max_time_s:
                        raise GenerationLimit("Zeitlimit der Modellgenerierung erreicht") from exc
                    raise OllamaUnavailable(f"Stream abgebrochen: {exc}") from exc
            if not complete:
                raise OllamaUnavailable("Stream ohne Abschluss abgebrochen")
            data["message"] = message
        else:
            with self._post("/api/chat", payload, stream=False, model_for_404=model) as r:
                data = r.json()
        if max_time_s is not None and time.monotonic() - started >= max_time_s:
            raise GenerationLimit("Zeitlimit der Modellgenerierung erreicht")
        if err := data.get("error"):
            if "not found" in err.lower():
                raise ModelMissing(model)
            raise OllamaError(err)
        self.last_usage = {"prompt": data.get("prompt_eval_count") or 0, "completion": data.get("eval_count") or 0}
        if data.get("done_reason") == "length":
            raise GenerationLimit("Tokenlimit erreicht; keine unvollständigen Werkzeugaufrufe ausgeführt")
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
