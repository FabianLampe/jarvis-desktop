"""Dateien in einem Code-Editor öffnen (VS Code, Cursor, Xcode, PyCharm …).

Öffnen führt nichts aus; der Editor zeigt die Datei nur an. Deshalb braucht es auch nach fremdem
Inhalt (z. B. der Ausgabe des eben getesteten Skripts) keine Bestätigung — vorausgesetzt, es startet
wirklich nur ein Editor: Das Modell kann ausschließlich Einträge aus EDITORS wählen, nie „Terminal“ o. ä. Ohne Angabe gilt
tools.editor aus config.yaml (Standard: Visual Studio Code)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .paths import resolve
from .process import ProcessRunner
from .registry import Tool, ToolContext, ToolError, ToolResult

# Gesprochene/getippte Namen → App-Name (macOS) bzw. Kommandozeilenprogramm.
EDITORS = {
    "vs code": ("Visual Studio Code", "code"), "vscode": ("Visual Studio Code", "code"),
    "visual studio code": ("Visual Studio Code", "code"), "code": ("Visual Studio Code", "code"),
    "cursor": ("Cursor", "cursor"), "xcode": ("Xcode", None), "pycharm": ("PyCharm", "pycharm"),
    "pycharm ce": ("PyCharm CE", "pycharm"), "sublime": ("Sublime Text", "subl"),
    "sublime text": ("Sublime Text", "subl"), "zed": ("Zed", "zed"), "textedit": ("TextEdit", None),
}
APP_DIRS = (Path("/Applications"), Path.home() / "Applications", Path("/System/Applications"))


def find_app(name: str) -> str | None:
    if not name or "/" in name or ".." in name:
        return None
    for d in APP_DIRS:
        if (d / f"{name}.app").exists():
            return name
    return None


def _resolve_editor(ctx: ToolContext, wanted: str) -> tuple[str, str | None]:
    """Nur bekannte Editoren. Der Wunsch des Modells muss in EDITORS stehen; der Eintrag aus
    config.yaml (vom Nutzer gesetzt) darf auch ein anderer App-Name sein. So kann ein Werkzeugaufruf
    nie „Terminal“ oder eine andere ausführende App mit einer Datei starten."""
    if wanted.strip():
        entry = EDITORS.get(wanted.strip().lower())
        if entry is None:
            raise ToolError(f"Unbekannter Editor „{wanted}“. Möglich: VS Code, Cursor, Xcode, PyCharm, Sublime Text, Zed, TextEdit.")
        return entry
    configured = (ctx.cfg.tools.editor or "Visual Studio Code").strip()
    return EDITORS.get(configured.lower(), (configured, None))


def editor_argv(ctx: ToolContext, wanted: str, path: Path) -> tuple[list[str], str]:
    app, cli = _resolve_editor(ctx, wanted)
    if sys.platform == "darwin":
        found = find_app(app)
        if found:
            return ["open", "-a", found, str(path)], found
    if cli and shutil.which(cli):
        return [cli, str(path)], app
    raise ToolError(f"Den Editor „{app}“ habe ich nicht gefunden. Installiert? Anderen Editor über "
                    "tools.editor in config.yaml einstellen.")


def open_in_editor(ctx: ToolContext, a: dict) -> ToolResult:
    path = resolve(ctx, str(a.get("path", "")), must_exist=True)
    argv, name = editor_argv(ctx, str(a.get("editor") or ""), path)
    res = ProcessRunner(ctx.platform).run(argv, path.parent if path.is_file() else path, timeout_s=20, max_chars=800)
    if not res.ok:
        detail = (res.stderr or res.error or "").strip().splitlines()
        raise ToolError(f"{name} ließ sich nicht öffnen" + (f": {detail[0]}" if detail else ""))
    return ToolResult(True, f"In {name} geöffnet: {path}")


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [Tool("open_in_editor", "editor",
                 "Öffnet eine Datei oder einen Ordner in einem Code-Editor (Standard: VS Code). "
                 "editor optional, z. B. „VS Code“, „Cursor“, „Xcode“, „PyCharm“.",
                 {"path": S, "editor": S}, ("path",), open_in_editor,
                 announce=lambda a: f"Ich öffne {Path(str(a.get('path', ''))).name} im Editor, Sir.")]
