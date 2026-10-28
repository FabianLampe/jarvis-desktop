"""BrowserAgent — Tool-Loop, der Browser-Befehle ausführt und ansagt.

Ereignis-Generator (wie ChatService.send), konsumiert von Bridge/Daemon:
  {"type":"action", "text": <Ansage>}   vor jeder Browser-Aktion
  {"type":"done",   "text": <Abschluss-Antwort>}
  {"type":"error",  "text": <Meldung>, "fatal": bool}

Zwei Wege: Schnellpfad (browser_intent.detect) oder Ollama-Tool-Calling.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from . import browser_intent
from .config import AppConfig
from .persona import SYSTEM_PROMPT
from .tools.schemas import TOOLS, TOOL_NAMES

log = logging.getLogger("jarvis.agent")

MSG_DISABLED = "Die Browser-Steuerung ist abgeschaltet, Sir."
MSG_FAIL = "Das konnte ich im Browser nicht erledigen, Sir."
MSG_UNREACHABLE = ("Ich erreiche den Browser nicht, Sir — läuft Node und ist "
                   "Chrome installiert?")


def _narrate(tool: str, args: dict) -> str:
    """Knappe, gesprochene Ansage je Werkzeug (deterministisch, schnell)."""
    if tool == "open_url":
        host = args.get("url", "").split("//")[-1].split("/")[0].replace("www.", "")
        return f"Ich öffne {host}, Sir."
    if tool == "search":
        wo = "auf YouTube" if args.get("engine") == "youtube" else "im Netz"
        return f"Ich suche {wo} nach {args.get('query')}, Sir."
    if tool == "play_youtube":
        return f"Ich spiele {args.get('query')} auf YouTube ab, Sir."
    if tool == "search_open":
        return f"Ich suche die Seite zu {args.get('query')}, Sir."
    if tool == "navigate":
        return {"back": "Ich gehe zurück, Sir.", "forward": "Ich gehe vor, Sir.",
                "reload": "Ich lade die Seite neu, Sir."}.get(
                    str(args.get("action")), "Sehr wohl, Sir.")
    if tool == "read_page":
        return "Einen Moment, Sir — ich sehe mir die Seite an."
    if tool == "click":
        return "Ich klicke das an, Sir."
    if tool == "type_text":
        return "Ich gebe das ein, Sir."
    if tool == "scroll":
        return "Ich scrolle, Sir."
    return "Sehr wohl, Sir."


class BrowserAgent:
    def __init__(self, cfg: AppConfig, browser, client):
        self.cfg = cfg
        self.browser = browser
        self.client = client

    def run(self, text: str) -> Iterator[dict[str, Any]]:
        if not self.cfg.browser.enabled:
            yield {"type": "done", "text": MSG_DISABLED}
            return
        try:
            self.browser.ensure_started()
        except Exception as exc:  # noqa: BLE001
            log.warning("Browser-Start fehlgeschlagen: %s", exc)
            yield {"type": "error", "fatal": True, "text": MSG_UNREACHABLE}
            return

        actions = browser_intent.detect_all(text)
        if actions:
            yield from self._run_sequence(actions)
            return
        yield from self._run_llm_loop(text)

    # ---------------------------------------------------- Aktions-Sequenz

    def _run_sequence(self, actions: list[dict]) -> Iterator[dict]:
        """Führt eine oder mehrere Aktionen nacheinander aus (z. B. öffnen + lesen).
        Jede Aktion wird angesagt; eine Lese-Aktion liefert die Abschluss-Antwort."""
        last_narration = "Erledigt, Sir."
        read_result = None
        for a in actions:
            tool, args = a["tool"], a["args"]
            last_narration = _narrate(tool, args)
            yield {"type": "action", "text": last_narration}
            try:
                result = self._invoke(tool, args)
            except Exception as exc:  # noqa: BLE001
                log.warning("Werkzeug %s fehlgeschlagen: %s", tool, exc)
                yield {"type": "done", "text": MSG_FAIL}
                return
            if tool == "read_page":
                read_result = result
        if read_result is not None:
            yield {"type": "done", "text": self._summarize(read_result)}
        else:
            yield {"type": "done", "text": last_narration}

    def _invoke(self, tool: str, args: dict) -> str:
        if tool not in TOOL_NAMES:
            raise ValueError(f"Unbekanntes Werkzeug: {tool}")
        fn = getattr(self.browser, tool)
        return fn(**args)

    def _summarize(self, content: str) -> str:
        content = (content or "").strip()
        if not content:
            return "Auf der Seite finde ich keinen lesbaren Text, Sir."
        if self.client is not None:
            msgs = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "Fasse den folgenden Seiteninhalt in zwei "
                     "Sätzen auf Deutsch zusammen:\n\n" + content[:4000]}]
            try:
                m = self.client.chat_once(self.cfg.profile.general, msgs,
                                          num_ctx=self.cfg.profile.num_ctx)
                if m.get("content"):
                    return m["content"].strip()
            except Exception as exc:  # noqa: BLE001
                log.debug("Zusammenfassung fehlgeschlagen: %s", exc)
        return content[:400]

    # ---------------------------------------------------- LLM-Tool-Loop

    def _run_llm_loop(self, text: str) -> Iterator[dict]:
        if self.client is None:
            yield {"type": "done",
                   "text": "Das habe ich als Browser-Befehl nicht verstanden, Sir."}
            return
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT +
             "\nDu kannst den Browser über die bereitgestellten Werkzeuge steuern. "
             "Nutze sie für Web-Aufgaben und antworte sonst normal."},
            {"role": "user", "content": text},
        ]
        for _ in range(max(1, self.cfg.browser.max_steps)):
            msg = self.client.chat_once(self.cfg.profile.general, messages,
                                        tools=TOOLS, num_ctx=self.cfg.profile.num_ctx)
            calls = msg.get("tool_calls") or []
            if not calls:
                yield {"type": "done",
                       "text": (msg.get("content") or "Erledigt, Sir.").strip()}
                return
            messages.append({"role": "assistant", "content": msg.get("content", ""),
                             "tool_calls": calls})
            for call in calls:
                fn = call["function"]["name"]
                raw = call["function"].get("arguments", {})
                args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
                yield {"type": "action", "text": _narrate(fn, args)}
                try:
                    result = self._invoke(fn, args)
                except Exception as exc:  # noqa: BLE001
                    result = f"Fehler: {exc}"
                messages.append({"role": "tool", "name": fn, "content": result[:2000]})
        yield {"type": "done", "text": "Ich habe getan, was möglich war, Sir."}
