"""Werkzeuge für Gedächtnis, Dokumentenwissen und Erinnerungen."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..memory import when
from ..memory.knowledge import Knowledge
from ..memory.reminders import ReminderService
from ..memory.store import MemoryStore
from .paths import resolve
from .registry import (ALLOW, CONFIRM, Assessment, Tool, ToolContext, ToolError,
                       ToolResult, wrap_untrusted)


def get_memory(ctx: ToolContext) -> MemoryStore:
    return ctx.service("memory", lambda: MemoryStore(ctx.data_dir / "memory.db"))


def get_knowledge(ctx: ToolContext) -> Knowledge:
    k = ctx.cfg.knowledge
    return ctx.service("knowledge", lambda: Knowledge(
        ctx.data_dir, k.embedding_model, k.chunk_chars, k.chunk_overlap, k.top_k))


def get_reminders(ctx: ToolContext) -> ReminderService:
    def make():
        def notify(d):
            if not ctx.platform.notify("Jarvis – Erinnerung", d["text"]):
                raise RuntimeError("Benachrichtigung nicht zugestellt")   # bleibt offen, kommt beim nächsten Start
        svc = ReminderService(ctx.data_dir, ctx.cfg.reminders.timezone, ctx.cfg.reminders.misfire_grace_s,
                              on_due=notify)
        svc.start()
        return svc
    return ctx.service("reminders", make)


# ------------------------------------------------------------- Gedächtnis

def memory_save(ctx: ToolContext, a: dict) -> ToolResult:
    mid, new = get_memory(ctx).add(str(a.get("text", "")), str(a.get("kind") or "fact"))
    return ToolResult(True, f"Gemerkt (#{mid}): {a['text']}" if new else f"Das wusste ich bereits (#{mid}).")


def memory_search(ctx: ToolContext, a: dict) -> ToolResult:
    rows = get_memory(ctx).search(str(a.get("query", "")), int(a.get("limit") or 5))
    if not rows:
        return ToolResult(True, "Dazu ist nichts gespeichert.")
    return ToolResult(True, "\n".join(f"#{r['id']} [{r['kind']}, {r['updated'][:10]}] {r['text']}" for r in rows))


def memory_update(ctx: ToolContext, a: dict) -> ToolResult:
    mid = int(a["id"])
    if not get_memory(ctx).update(mid, str(a.get("text", ""))):
        raise ToolError(f"Eintrag #{mid} gibt es nicht")
    return ToolResult(True, f"Korrigiert (#{mid}): {a['text']}")


def memory_forget(ctx: ToolContext, a: dict) -> ToolResult:
    mid = int(a["id"])
    if not get_memory(ctx).delete(mid):
        raise ToolError(f"Eintrag #{mid} gibt es nicht")
    return ToolResult(True, f"Vergessen (#{mid})")


def _tainted_confirm(what):
    def assess(ctx: ToolContext, a: dict) -> Assessment:
        if ctx.tainted:
            return Assessment(CONFIRM, f"{what} (stammt aus gelesenem Fremdinhalt): {a.get('text') or a.get('query') or ''}")
        return Assessment(ALLOW)
    return assess


def _forget_assess(ctx: ToolContext, a: dict) -> Assessment:
    try:
        row = get_memory(ctx).get(int(a.get("id", 0)))
    except (TypeError, ValueError):
        return Assessment(ALLOW)
    return Assessment(CONFIRM, f"VERGESSEN #{a.get('id')}: {row['text'] if row else '(unbekannt)'}")


# -------------------------------------------------------------- Dokumente

def docs_index(ctx: ToolContext, a: dict) -> ToolResult:
    p = resolve(ctx, str(a.get("path", "")), must_exist=True)
    r = get_knowledge(ctx).index_path(p)
    msg = f"Indexiert: {r['files']} Datei(en), {r['chunks']} Passagen aus {p}"
    if r["failed"]:
        msg += "\nNicht lesbar: " + "; ".join(r["failed"][:5])
    return ToolResult(r["files"] > 0 or not r["failed"], msg, r)


def docs_search(ctx: ToolContext, a: dict) -> ToolResult:
    hits = get_knowledge(ctx).search(str(a.get("query", "")), int(a.get("limit") or 0) or None)
    if not hits:
        return ToolResult(True, "Keine Treffer — wurden Dokumente mit docs_index eingelesen?", {"sources": []})
    lines, sources = [], []
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h['file']} — {h['location']}\n{h['text'][:700]}")
        sources.append({"n": i, "title": f"{h['file']}, {h['location']}", "path": h["path"]})
    return ToolResult(True, wrap_untrusted("\n\n".join(lines)), {"sources": sources})


def docs_list(ctx: ToolContext, a: dict) -> ToolResult:
    st = get_knowledge(ctx).stats()
    return ToolResult(True, f"{len(st['files'])} Datei(en), {st['chunks']} Passagen:\n" + "\n".join(st["files"][:30]))


def docs_forget(ctx: ToolContext, a: dict) -> ToolResult:
    p = resolve(ctx, str(a.get("path", "")))
    get_knowledge(ctx).forget(p)
    return ToolResult(True, f"Aus der Dokumentensuche entfernt: {p} (die Datei selbst bleibt unverändert)")


# ----------------------------------------------------------- Erinnerungen

_NOTE = "Hinweis: Zustellung nur, solange Jarvis läuft und der Mac nicht schläft; sonst melde ich sie nach dem nächsten Start als verpasst."


def reminder_create(ctx: ToolContext, a: dict) -> ToolResult:
    svc = get_reminders(ctx)
    text = str(a.get("text", ""))
    when_s = str(a.get("when", ""))
    repeat = a.get("repeat") or when.detect_repeat(when_s) or when.detect_repeat(text)
    if repeat in ("none", "once", "null", ""):
        repeat = None
    if repeat and repeat not in when.REPEATS:
        raise ToolError(f"repeat muss eines von {', '.join(when.REPEATS)} sein")
    try:
        due = when.parse_when(when_s, tz=ctx.cfg.reminders.timezone)
        r = svc.create(text, due, repeat)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(True, f"Erinnerung gesetzt: {svc.format(r)} ({ctx.cfg.reminders.timezone}). {_NOTE}", {"id": r["id"]})


def reminder_list(ctx: ToolContext, a: dict) -> ToolResult:
    svc = get_reminders(ctx)
    rows = svc.list()
    return ToolResult(True, "\n".join(svc.format(r) for r in rows) if rows else "Keine aktiven Erinnerungen.")


def reminder_cancel(ctx: ToolContext, a: dict) -> ToolResult:
    if not get_reminders(ctx).cancel(int(a["id"])):
        raise ToolError(f"Erinnerung #{a['id']} gibt es nicht oder sie ist nicht mehr aktiv")
    return ToolResult(True, f"Erinnerung #{a['id']} gelöscht")


def tools() -> list[Tool]:
    S = {"type": "string"}
    I = {"type": "integer"}
    return [
        Tool("memory_save", "memory", "Merkt sich eine Information dauerhaft (Fakt oder Notiz).",
             {"text": S, "kind": {**S, "enum": ["fact", "note"]}}, ("text",), memory_save,
             _tainted_confirm("Merken"), announce=lambda a: "Ich merke mir das, Sir."),
        Tool("memory_search", "memory", "Sucht in gespeicherten Fakten und Notizen.",
             {"query": S, "limit": I}, ("query",), memory_search),
        Tool("memory_update", "memory", "Korrigiert einen gespeicherten Eintrag (id aus memory_search).",
             {"id": I, "text": S}, ("id", "text"), memory_update, _tainted_confirm("Korrigieren")),
        Tool("memory_forget", "memory", "Vergisst einen gespeicherten Eintrag (id aus memory_search).",
             {"id": I}, ("id",), memory_forget, _forget_assess),
        Tool("docs_index", "docs", "Liest eine Datei oder einen Ordner (PDF, Word, Excel, PowerPoint, Markdown …) in die lokale Dokumentensuche ein.",
             {"path": S}, ("path",), docs_index, announce=lambda a: "Ich lese die Dokumente ein, Sir."),
        Tool("docs_search", "docs", "Durchsucht die eingelesenen Dokumente; liefert Passagen mit Datei und Fundstelle.",
             {"query": S, "limit": I}, ("query",), docs_search, untrusted=True,
             announce=lambda a: "Ich durchsuche Ihre Dokumente, Sir."),
        Tool("docs_list", "docs", "Zeigt, welche Dokumente eingelesen sind.", {}, (), docs_list),
        Tool("docs_forget", "docs", "Entfernt ein Dokument aus der Suche (Datei bleibt).",
             {"path": S}, ("path",), docs_forget),
        Tool("reminder_create", "reminders",
             "Setzt eine Erinnerung. when = z. B. „morgen um 9 Uhr“, „in 10 Minuten“, „2026-11-02 08:15“. repeat optional.",
             {"text": S, "when": S, "repeat": {**S, "enum": ["daily", "weekdays", "weekly", "monthly"]}},
             ("text", "when"), reminder_create, _tainted_confirm("Erinnerung setzen"),
             announce=lambda a: "Ich setze die Erinnerung, Sir."),
        Tool("reminder_list", "reminders", "Listet aktive Erinnerungen.", {}, (), reminder_list),
        Tool("reminder_cancel", "reminders", "Löscht eine Erinnerung per id.", {"id": I}, ("id",), reminder_cancel),
    ]
