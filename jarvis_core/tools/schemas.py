"""Werkzeug-Definitionen im Ollama-Tools-Format (für qwen2.5:3b Tool-Calling).

Die Namen entsprechen den hohen Methoden von tools.browser.BrowserController
(NICHT den rohen chrome-devtools-mcp-Tools)."""
from __future__ import annotations


def _fn(name, desc, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


TOOLS = [
    _fn("browser_snapshot", "Zeigt Browser-Elemente mit uid vor click/type_text; nach Navigation neu abrufen.", {}, []),
    _fn("open_url", "Öffnet eine Webadresse in einem neuen Tab.",
        {"url": {"type": "string", "description": "Vollständige URL inkl. https://"}}, ["url"]),
    _fn("search", "Sucht im Web (Google oder YouTube).",
        {"query": {"type": "string", "description": "Suchbegriff"},
         "engine": {"type": "string", "enum": ["google", "youtube"], "description": "Suchmaschine"}},
        ["query"]),
    _fn("play_youtube", "Sucht ein Video auf YouTube und spielt das erste Ergebnis ab.",
        {"query": {"type": "string", "description": "Was abgespielt werden soll"}}, ["query"]),
    _fn("search_open", "Googelt und öffnet die erste Trefferseite (für unbekannte Webseiten).",
        {"query": {"type": "string", "description": "Wonach gesucht wird"},
         "engine": {"type": "string", "enum": ["google"]}}, ["query"]),
    _fn("navigate", "Browser-Verlauf: zurück, vor oder neu laden.",
        {"action": {"type": "string", "enum": ["back", "forward", "reload"]}}, ["action"]),
    _fn("read_page", "Liest den sichtbaren Textinhalt der aktuellen Seite (zum Zusammenfassen/Vorlesen).",
        {}, []),
    _fn("click", "Klickt ein Element aus dem letzten Snapshot anhand seiner uid.",
        {"uid": {"type": "string", "description": "uid des Elements aus take_snapshot"}}, ["uid"]),
    _fn("type_text", "Tippt Text in ein Eingabefeld (uid aus dem Snapshot).",
        {"uid": {"type": "string"}, "text": {"type": "string"}}, ["uid", "text"]),
    _fn("press_enter", "Drückt die Eingabetaste.", {}, []),
    _fn("scroll", "Scrollt die Seite hoch oder runter.",
        {"direction": {"type": "string", "enum": ["up", "down"]}}, ["direction"]),
    _fn("list_tabs", "Listet die offenen Tabs.", {}, []),
    _fn("close_tab", "Schließt den aktuellen Tab.", {}, []),
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
