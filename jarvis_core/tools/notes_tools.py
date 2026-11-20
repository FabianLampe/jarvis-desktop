"""Apple Notizen: Notiz anlegen und durchsuchen (App „Notizen“, nicht Jarvis' eigenes Gedächtnis).

AppleScripts sind fest im Code; Titel, Text und Suchbegriff kommen nur als argv an. Der Notiztext wird vor der
Übergabe zu HTML umgebaut (Zeichen maskiert) — es kann also kein Markup oder Skript eingeschleust werden.
Notizen sind persönlich (PRIVATE) und ihr Inhalt ist fremder Text: Titel und Anfang werden bereinigt und markiert."""
from __future__ import annotations

import html

from . import mac_script as ms
from .network_tools import clean
from .registry import ALLOW, CONFIRM, Assessment, Tool, ToolContext, ToolError, ToolResult, wrap_untrusted

FS, RS = "\x1f", "\x1e"          # Feld-/Datensatztrenner in der Skriptausgabe
MAX_TITLE, MAX_BODY, MAX_HITS = 200, 10000, 8

CREATE_SCRIPT = """on run argv
    tell application "Notes"
        make new note with properties {body:(item 1 of argv)}
    end tell
end run"""

SEARCH_SCRIPT = """on run argv
    set q to item 1 of argv
    set fs to (ASCII character 31)
    set rs to (ASCII character 30)
    set out to ""
    with timeout of 40 seconds
        tell application "Notes"
            set hits to (notes whose name contains q)
            if (count of hits) is 0 then set hits to (notes whose plaintext contains q)
            set n to count of hits
            if n > 8 then set n to 8
            repeat with i from 1 to n
                set theNote to item i of hits
                set snippet to ""
                try
                    set snippet to text 1 thru 200 of (plaintext of theNote)
                on error
                    try
                        set snippet to plaintext of theNote
                    end try
                end try
                set out to out & (name of theNote) & fs & snippet & rs
            end repeat
        end tell
    end timeout
    return out
end run"""


def note_html(title: str, body: str) -> str:
    """Notiz-HTML aus reinem Text: alles maskiert, Zeilenumbrüche werden <br>."""
    head = f"<div><h1>{html.escape(title.strip(), quote=True)}</h1></div>"
    lines = "".join(f"<div>{html.escape(l, quote=True) or '<br>'}</div>" for l in body.strip().splitlines())
    return head + lines


def notes_create(ctx: ToolContext, a: dict) -> ToolResult:
    title = str(a.get("title", "")).strip()
    body = str(a.get("body") or "")
    if not title or len(title) > MAX_TITLE:
        raise ToolError(f"Die Notiz braucht einen Titel (bis {MAX_TITLE} Zeichen).")
    if len(body) > MAX_BODY:
        raise ToolError(f"Der Notiztext ist zu lang (höchstens {MAX_BODY} Zeichen).")
    ms.osa(CREATE_SCRIPT, note_html(title, body), app="Notizen")
    return ToolResult(True, f"Notiz „{clean(title, 60)}“ angelegt (App Notizen).")


def notes_search(ctx: ToolContext, a: dict) -> ToolResult:
    query = str(a.get("query", "")).strip()
    if not query or len(query) > 200:
        raise ToolError("Bitte einen Suchbegriff angeben.")
    out = ms.osa(SEARCH_SCRIPT, query, app="Notizen", timeout=60)
    rows = [r for r in out.split(RS) if r.strip()]
    if not rows:
        return ToolResult(True, f"In den Apple Notizen finde ich nichts zu „{clean(query, 40)}“.")
    lines = []
    for r in rows[:MAX_HITS]:
        title, _, snippet = r.partition(FS)
        title = clean(title, 80)
        snippet = clean(snippet, 150)
        lines.append(f"- {title}" + (f": {snippet}" if snippet and snippet != title else ""))
    return ToolResult(True, f"{len(rows)} Treffer in den Apple Notizen:\n" + wrap_untrusted("\n".join(lines)),
                      {"count": len(rows)})


def _assess_create(ctx: ToolContext, a: dict) -> Assessment:
    if ctx.tainted:
        return Assessment(CONFIRM, f"NOTIZ ANLEGEN (nach Lesen fremder Inhalte): „{clean(str(a.get('title', '')), 60)}“")
    return Assessment(ALLOW)


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("notes_create", "notes", "Legt eine Notiz in der Apple-App „Notizen“ an (Titel + Text).",
             {"title": S, "body": S}, ("title",), notes_create, _assess_create,
             announce=lambda a: "Ich lege die Notiz an, Sir."),
        Tool("notes_search", "notes",
             "Sucht in den Apple Notizen nach Titel/Inhalt und zeigt Titel und Anfang (nicht Jarvis' eigenes Gedächtnis).",
             {"query": S}, ("query",), notes_search, untrusted=True),
    ]
