"""Gemeinsame Bausteine für begrenzte, strukturierte Erweiterungswerkzeuge."""
from __future__ import annotations

import json
from pathlib import Path

from .paths import in_workspace, resolve
from .registry import ALLOW, CONFIRM, Assessment, ToolContext, ToolError, ToolResult
from .shell_tools import _runner

S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}


def result(data: dict, ok: bool = True) -> ToolResult:
    return ToolResult(ok, json.dumps(data, ensure_ascii=False, default=str, allow_nan=False), data, structured=True)


def confirm(label: str):
    def assess(ctx, args):
        return Assessment(CONFIRM, label + ": " + json.dumps(args, ensure_ascii=False))
    return assess


def output_assess(ctx: ToolContext, args: dict) -> Assessment:
    try:
        p = resolve(ctx, str(args.get("output", "")))
    except ToolError:
        return Assessment(ALLOW)  # Handler verweigert den ungültigen Pfad ebenfalls.
    if ctx.tainted or not in_workspace(ctx, p) or p.exists():
        return Assessment(CONFIRM, f"DATEI SCHREIBEN: {p} (überschreiben: {bool(args.get('overwrite'))})")
    return Assessment(ALLOW)


def run(ctx: ToolContext, argv: list[str], cwd: Path | None = None, timeout: float = 60):
    if ctx.cancel.is_set():
        raise ToolError("Abgebrochen")
    return _runner(ctx).run(argv, cwd or ctx.workspace, timeout_s=min(timeout, ctx.cfg.tools.max_timeout_s),
                            max_chars=20000, cancel=ctx.cancel)


def process_result(res) -> ToolResult:
    return result({"exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr,
                   "timed_out": res.timed_out, "cancelled": res.cancelled,
                   "truncated": res.truncated, "error": res.error}, res.ok)


def status_lines(cfg):
    """Nur lokale Voraussetzungen prüfen, keine Server starten oder Hosts kontaktieren."""
    import importlib.util
    import shutil
    lines = [f"Git: {'vorhanden' if shutil.which('git') else 'nicht installiert'}",
             f"SSH-Programm: {'vorhanden' if shutil.which('ssh') else 'nicht installiert'}"]
    missing = [m for m in ("pandas", "openpyxl", "matplotlib", "docx", "reportlab")
               if importlib.util.find_spec(m) is None]
    lines.append("Daten/Dokumente: " + ("Pakete vorhanden" if not missing else
                 "fehlende Pakete: " + ", ".join(missing) + "; pip install -r requirements-tools.txt"))
    for key, entries in (("ssh", "hosts"), ("mcp", "servers")):
        section = cfg.extensions.get(key) or {}
        aliases = list((section.get(entries) or {}).keys())
        lines.append(f"{key.upper()}: " + (
            "konfiguriert (Verbindung nicht geprüft): " + ", ".join(aliases)
            if section.get("enabled") and aliases else "deaktiviert/nicht eingerichtet"))
    lines.append("Mac-UI: " + ("aktiviert; Pakete/Bedienungshilfen beim Aufruf geprüft" if
                 (cfg.extensions.get("mac_ui") or {}).get("enabled") else "deaktiviert"))
    return lines
