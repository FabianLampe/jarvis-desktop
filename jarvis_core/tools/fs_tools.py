"""Dateiwerkzeuge. Lesen überall (außer geschützte Pfade), Schreiben im Arbeitsordner
direkt, außerhalb und Löschen nur nach Bestätigung."""
from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path

from .paths import in_workspace, is_protected, resolve
from .registry import (ALLOW, CONFIRM, Assessment, Tool, ToolContext, ToolError,
                       ToolResult, wrap_untrusted)

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".Trash"}
MAX_READ_BYTES = 2_000_000


def _p(ctx, args, key="path", must_exist=False) -> Path:
    return resolve(ctx, str(args.get(key, "")), must_exist=must_exist)


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


# -------------------------------------------------------------- Lesen

def list_dir(ctx: ToolContext, a: dict) -> ToolResult:
    p = _p(ctx, a, must_exist=True) if a.get("path") else ctx.workspace
    if not p.is_dir():
        raise ToolError(f"Kein Ordner: {p}")
    limit = 100
    rows = []
    for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if e.name.startswith(".") and not a.get("hidden"):
            continue
        try:
            rows.append(f"{e.name}/" if e.is_dir() else f"{e.name}  ({_fmt_size(e.stat().st_size)})")
        except OSError:
            rows.append(f"{e.name}  (nicht lesbar)")
    more = f"\n… und {len(rows) - limit} weitere" if len(rows) > limit else ""
    return ToolResult(True, f"{p}:\n" + ("\n".join(rows[:limit]) or "(leer)") + more)


def read_file(ctx: ToolContext, a: dict) -> ToolResult:
    p = _p(ctx, a, must_exist=True)
    if not p.is_file():
        raise ToolError(f"Keine Datei: {p}")
    if p.stat().st_size > MAX_READ_BYTES and not a.get("offset"):
        raise ToolError(f"Datei zu groß ({_fmt_size(p.stat().st_size)}); mit offset/max_chars teilweise lesen")
    raw = p.read_bytes()[: MAX_READ_BYTES]
    if b"\x00" in raw[:4096]:
        raise ToolError(f"Binärdatei ({_fmt_size(p.stat().st_size)}): {p.name} — für PDF/Office docs_index nutzen")
    text = raw.decode("utf-8", errors="replace")
    off = max(0, int(a.get("offset") or 0))
    n = int(a.get("max_chars") or ctx.cfg.tools.max_output_chars)
    chunk = text[off: off + n]
    tail = f"\n[… weitere {len(text) - off - n} Zeichen ab offset={off + n}]" if len(text) > off + n else ""
    return ToolResult(True, f"{p}\n" + wrap_untrusted(chunk) + tail)


def find_files(ctx: ToolContext, a: dict) -> ToolResult:
    root = _p(ctx, a, "root", must_exist=True) if a.get("root") else ctx.workspace
    pattern = str(a.get("name") or "*")
    needle = str(a.get("contains") or "")
    hits: list[str] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not is_protected(ctx, Path(dirpath) / d)]
        for f in files + dirs:
            if is_protected(ctx, Path(dirpath) / f):
                continue
            if not fnmatch.fnmatch(f.lower(), pattern.lower()):
                continue
            fp = Path(dirpath) / f
            if needle:
                try:
                    if not fp.is_file() or fp.stat().st_size > 1_000_000 \
                            or needle.lower() not in fp.read_text(errors="ignore").lower():
                        continue
                except OSError:
                    continue
            hits.append(str(fp))
            if len(hits) >= 50:
                break
        if len(hits) >= 50:
            break
    return ToolResult(True, f"{len(hits)} Treffer in {root}:\n" + "\n".join(hits) if hits
                      else f"Keine Treffer in {root}", {"hits": hits})


# ----------------------------------------------------------- Schreiben

def write_file(ctx: ToolContext, a: dict) -> ToolResult:
    p = _p(ctx, a)
    mode = str(a.get("mode") or "create")
    content = str(a.get("content", ""))
    if mode not in ("create", "overwrite", "append"):
        raise ToolError("mode muss create, overwrite oder append sein")
    if p.is_dir():
        raise ToolError(f"{p} ist ein Ordner")
    if mode == "create" and p.exists():
        raise ToolError(f"{p} existiert bereits — mode=overwrite oder append wählen")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if mode == "append" else "w", encoding="utf-8") as fh:
        fh.write(content)
    return ToolResult(True, f"{'Angehängt an' if mode == 'append' else 'Geschrieben:'} {p} ({len(content)} Zeichen)")


def edit_file(ctx: ToolContext, a: dict) -> ToolResult:
    p = _p(ctx, a, must_exist=True)
    old, new = str(a.get("old", "")), str(a.get("new", ""))
    if not old:
        raise ToolError("old (zu ersetzender Text) fehlt")
    text = p.read_text(encoding="utf-8")
    n = text.count(old)
    if n == 0:
        raise ToolError("Der zu ersetzende Text kommt in der Datei nicht vor")
    if n > 1 and not a.get("all"):
        raise ToolError(f"Der Text kommt {n}× vor — all=true setzen oder eindeutiger wählen")
    p.write_text(text.replace(old, new), encoding="utf-8")
    return ToolResult(True, f"{n} Stelle(n) in {p} ersetzt")


def make_dir(ctx: ToolContext, a: dict) -> ToolResult:
    p = _p(ctx, a)
    if p.exists() and not p.is_dir():
        raise ToolError(f"{p} existiert als Datei")
    existed = p.exists()
    p.mkdir(parents=True, exist_ok=True)
    return ToolResult(True, f"Ordner existiert bereits: {p}" if existed else f"Ordner angelegt: {p}")


def copy_path(ctx: ToolContext, a: dict) -> ToolResult:
    src, dst = _p(ctx, a, "src", must_exist=True), _p(ctx, a, "dst")
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise ToolError(f"Ziel existiert bereits: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
    return ToolResult(True, f"Kopiert: {src} → {dst}")


def move_path(ctx: ToolContext, a: dict) -> ToolResult:
    src, dst = _p(ctx, a, "src", must_exist=True), _p(ctx, a, "dst")
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise ToolError(f"Ziel existiert bereits: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return ToolResult(True, f"Verschoben: {src} → {dst}")


def delete_path(ctx: ToolContext, a: dict) -> ToolResult:
    p = _p(ctx, a, must_exist=True)
    if p == ctx.workspace or p == Path.home() or p == Path(p.anchor):
        raise ToolError(f"{p} wird nie gelöscht")
    if p.is_dir() and not p.is_symlink():
        if any(p.iterdir()) and not a.get("recursive"):
            raise ToolError(f"{p} ist nicht leer — recursive=true setzen")
        shutil.rmtree(p)
    else:
        p.unlink()
    if p.exists():
        raise ToolError(f"Löschen von {p} hat nicht funktioniert")
    return ToolResult(True, f"Gelöscht: {p}")


# --------------------------------------------------------- Bewertung

def _write_assess(*keys: str, verb: str):
    def assess(ctx: ToolContext, a: dict) -> Assessment:
        try:
            paths = [resolve(ctx, str(a.get(k, ""))) for k in keys]
        except ToolError:
            return Assessment(ALLOW)      # Handler meldet den Fehler selbst
        outside = [p for p in paths if not in_workspace(ctx, p)]
        if outside:
            return Assessment(CONFIRM, f"{verb} außerhalb des Arbeitsordners: " + ", ".join(map(str, outside)))
        return Assessment(ALLOW)
    return assess


def _delete_assess(ctx: ToolContext, a: dict) -> Assessment:
    try:
        p = resolve(ctx, str(a.get("path", "")))
    except ToolError:
        return Assessment(ALLOW)
    extra = " samt Inhalt" if a.get("recursive") and p.is_dir() else ""
    return Assessment(CONFIRM, f"LÖSCHEN: {p}{extra}")


def tools() -> list[Tool]:
    P = {"type": "string"}
    return [
        Tool("list_dir", "files", "Listet den Inhalt eines Ordners (Standard: Arbeitsordner).",
             {"path": {**P, "description": "Ordnerpfad"}, "hidden": {"type": "boolean"}}, (), list_dir,
             announce=lambda a: f"Ich sehe in {a.get('path') or 'den Arbeitsordner'} nach, Sir."),
        Tool("read_file", "files", "Liest eine Textdatei.",
             {"path": P, "offset": {"type": "integer"}, "max_chars": {"type": "integer"}},
             ("path",), read_file, untrusted=True,
             announce=lambda a: f"Ich lese {Path(str(a.get('path', ''))).name}, Sir."),
        Tool("find_files", "files", "Sucht Dateien nach Namensmuster (z. B. *.pdf) und optional Text darin.",
             {"name": {**P, "description": "Muster, z. B. *.md"}, "root": P, "contains": P},
             (), find_files, untrusted=True,
             announce=lambda a: "Ich suche die Dateien, Sir."),
        Tool("write_file", "files", "Schreibt eine Textdatei (mode: create|overwrite|append). Relative Pfade gelten im Arbeitsordner.",
             {"path": P, "content": P, "mode": {**P, "enum": ["create", "overwrite", "append"]}},
             ("path", "content"), write_file, _write_assess("path", verb="Schreiben"),
             announce=lambda a: f"Ich schreibe {Path(str(a.get('path', ''))).name}, Sir."),
        Tool("edit_file", "files", "Ersetzt Text in einer Datei (old → new).",
             {"path": P, "old": P, "new": P, "all": {"type": "boolean"}},
             ("path", "old", "new"), edit_file, _write_assess("path", verb="Bearbeiten"),
             announce=lambda a: f"Ich bearbeite {Path(str(a.get('path', ''))).name}, Sir."),
        Tool("make_dir", "files", "Legt einen Ordner an (auch verschachtelt).",
             {"path": P}, ("path",), make_dir, _write_assess("path", verb="Anlegen"),
             announce=lambda a: f"Ich lege den Ordner {Path(str(a.get('path', ''))).name} an, Sir."),
        Tool("copy_path", "files", "Kopiert Datei oder Ordner.",
             {"src": P, "dst": P}, ("src", "dst"), copy_path, _write_assess("dst", verb="Kopieren"),
             announce=lambda a: "Ich kopiere, Sir."),
        Tool("move_path", "files", "Verschiebt/benennt Datei oder Ordner um.",
             {"src": P, "dst": P}, ("src", "dst"), move_path, _write_assess("src", "dst", verb="Verschieben"),
             announce=lambda a: "Ich verschiebe, Sir."),
        Tool("delete_path", "files", "Löscht Datei oder Ordner (erfordert Bestätigung des Nutzers).",
             {"path": P, "recursive": {"type": "boolean"}}, ("path",), delete_path, _delete_assess,
             announce=lambda a: "Ich bitte um Bestätigung zum Löschen, Sir."),
    ]
