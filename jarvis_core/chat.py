"""ChatService — verbindet Router, Persona und Ollama zu einem Ereignis-Strom.

UI-unabhängig: send() ist ein Generator von JSON-serialisierbaren Ereignissen.
Die pywebview-Bridge (und später der Sprach-Daemon) konsumieren denselben Strom.

Ereignisse:
  {"type":"route", "lane","model","reason","heavy_requested","heavy_unavailable"}
  {"type":"state", "value":"think"|"speak"|"idle"}
  {"type":"swap",  "phase":"unload_pair"|"restore_pair"}   (nur Schwerlast-Spur)
  {"type":"token", "content": "..."}
  {"type":"done",  "tps": float|None, "text": "..."}
  {"type":"error", "message": "...", "fatal": bool}
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from .config import AppConfig
from .ollama_client import ModelMissing, OllamaClient, OllamaUnavailable
from .persona import messages_with_persona
from .router import route

log = logging.getLogger("jarvis.chat")

MSG_UNAVAILABLE = "Verbindung zum Mainframe unterbrochen, Sir. Ich versuche es weiter."
MSG_MODEL_MISSING = ("Das Modell „{model}“ fehlt im Hangar, Sir. "
                     "`ollama pull {model}`, wenn ich bitten darf.")
MSG_STREAM_ABORT = " — Übertragung abgerissen, Sir; der Rest der Antwort ging verloren."


class ChatService:
    """Hält den Gesprächsverlauf einer Sitzung und orchestriert die Schwerlast-Spur."""

    def __init__(self, cfg: AppConfig, client: OllamaClient | None = None,
                 facts=None):
        self.cfg = cfg
        self.client = client or OllamaClient(cfg.ollama_host)
        self.history: list[dict[str, str]] = []
        self.facts = facts          # Callable[[str], list[str]] | None — Gedächtnis-Fakten

    # ------------------------------------------------------------------ Chat

    def send(self, text: str, force_7b: bool = False,
             model_override: str | None = None) -> Iterator[dict[str, Any]]:
        """model_override: Schnellspur — kleineres Modell, solange Aufgaben das große belegen."""
        from dataclasses import replace
        profile = self.cfg.profile
        decision = route(text, force_7b, profile)
        if model_override and decision.lane != "heavy":
            decision = replace(decision, model=model_override, reason="Schnellspur (Aufgabe läuft)")
        yield {
            "type": "route",
            "lane": decision.lane,
            "model": decision.model,
            "reason": decision.reason,
            "heavy_requested": decision.heavy_requested,
            "heavy_unavailable": decision.heavy_unavailable,
        }
        yield {"type": "state", "value": "think"}

        self.history.append({"role": "user", "content": text})
        self._trim()
        facts = None
        if self.facts is not None:
            try:
                facts = self.facts(text)
            except Exception:  # noqa: BLE001 — Gedächtnis darf den Chat nie blockieren
                log.debug("Fakten nicht lesbar", exc_info=True)
        messages = messages_with_persona(self.history, facts)
        heavy = decision.lane == "heavy"

        if heavy:
            # ADR 0002: 3B-Paar entladen → 7B laden → antworten → 3B zurückladen
            yield {"type": "swap", "phase": "unload_pair"}
            self.client.unload(profile.general)
            if profile.coder != profile.general:
                self.client.unload(profile.coder)

        parts: list[str] = []
        tps = None
        try:
            for ev in self.client.chat_stream(
                decision.model, messages,
                num_ctx=profile.num_ctx, keep_alive=0 if heavy else self.cfg.keep_alive,
            ):
                if token := ev.get("token"):
                    if not parts:
                        yield {"type": "state", "value": "speak"}
                    parts.append(token)
                    yield {"type": "token", "content": token}
                if ev.get("done"):
                    tps = ev.get("tps")
        except OllamaUnavailable as exc:
            log.warning("Ollama nicht erreichbar: %s", exc)
            if parts:  # Teilantwort bleibt stehen, Hinweis ans Nachrichtenende
                self._remember_assistant(parts)
                yield {"type": "error", "message": MSG_STREAM_ABORT, "fatal": False}
            else:
                self.history.pop()  # Frage blieb unbeantwortet
                yield {"type": "error", "message": MSG_UNAVAILABLE, "fatal": True}
            yield {"type": "state", "value": "idle"}
            return
        except ModelMissing as exc:
            self.history.pop()
            yield {"type": "error",
                   "message": MSG_MODEL_MISSING.format(model=exc.model), "fatal": True}
            yield {"type": "state", "value": "idle"}
            return
        finally:
            if heavy:
                # 3B-Paar zurückladen, damit das nächste Routing latenzfrei ist
                self.client.preload(profile.general, self.cfg.keep_alive)
                if profile.coder != profile.general:
                    self.client.preload(profile.coder, self.cfg.keep_alive)

        if heavy:
            yield {"type": "swap", "phase": "restore_pair"}

        self._remember_assistant(parts)
        yield {"type": "done", "tps": tps, "text": "".join(parts)}
        yield {"type": "state", "value": "idle"}

    # --------------------------------------------------------- Kontextfenster

    @staticmethod
    def est_tokens(text: str) -> int:
        return int(len(text) / 3.3) + 4                     # grobe Schätzung für Deutsch

    def _trim(self) -> int:
        """Älteste Nachrichten verwerfen, bevor der Verlauf das Kontextfenster sprengt
        (Ollama würde sonst still vorne abschneiden). Gibt die Zahl entfernter Nachrichten zurück."""
        from .persona import SYSTEM_PROMPT
        budget = int(self.cfg.profile.num_ctx * 0.7) - self.est_tokens(SYSTEM_PROMPT)
        removed = 0
        while len(self.history) > 2 and sum(self.est_tokens(m["content"]) for m in self.history) > budget:
            self.history.pop(0); removed += 1
        return removed

    def context_info(self) -> dict[str, Any]:
        """Belegung des Kontextfensters: gemessen (Ollama-Zähler) oder geschätzt."""
        from .persona import SYSTEM_PROMPT
        total = self.cfg.profile.num_ctx
        usage = getattr(self.client, "last_usage", None)
        if usage and self.history:
            used, source = int(usage["prompt"]) + int(usage["completion"]), "gemessen"
        else:
            used = self.est_tokens(SYSTEM_PROMPT) + sum(self.est_tokens(m["content"]) for m in self.history)
            source = "geschätzt"
        return {"used": used, "total": total, "pct": min(100, round(used / total * 100)),
                "messages": len(self.history), "source": source, "model": self.cfg.profile.general}

    def remember_exchange(self, user: str, assistant: str) -> None:
        """Werkzeug-Aufträge in den Verlauf aufnehmen, damit Folgefragen den Bezug haben."""
        self.history.append({"role": "user", "content": user})
        if assistant:
            self.history.append({"role": "assistant", "content": assistant})

    def reset(self) -> None:
        self.history.clear()
        if hasattr(self.client, "last_usage"):
            self.client.last_usage = None

    # ---------------------------------------------------------------- intern

    def _remember_assistant(self, parts: list[str]) -> None:
        if parts:
            self.history.append({"role": "assistant", "content": "".join(parts)})
