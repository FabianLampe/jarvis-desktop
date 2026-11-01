"""Datenanalyse, Diagramme und Dokumentausgabe in begrenzten Kindprozessen."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .extension_utils import B, S, output_assess, result, run
from .paths import resolve
from .registry import Tool, ToolError

WORKER = Path(__file__).with_name("artifact_worker.py")


def worker(ctx, request):
    with tempfile.TemporaryDirectory(prefix="jarvis-artifact-") as tmp:
        inp = Path(tmp) / "request.json"
        text = json.dumps(request, ensure_ascii=False)
        if len(text) > 250000:
            raise ToolError("Auftrag zu groß (maximal 250000 Zeichen)")
        inp.write_text(text, encoding="utf-8")
        res = run(ctx, [sys.executable, "-I", str(WORKER), str(inp)], timeout=60)
        if not res.ok or res.truncated:
            raise ToolError(res.render())
        try:
            data = json.loads(res.stdout)
        except ValueError:
            raise ToolError("Artefaktprozess lieferte kein vollständiges Ergebnis") from None
        if not data.pop("ok", False):
            raise ToolError(data.get("error", "Artefakterstellung fehlgeschlagen"))
        return data


def source(ctx, a):
    p = resolve(ctx, str(a["path"]), must_exist=True)
    if not p.is_file() or p.suffix.lower() not in (".csv", ".tsv", ".xlsx"):
        raise ToolError("Unterstützt: CSV, TSV, XLSX")
    if p.stat().st_size > 25_000_000:
        raise ToolError("Eingabedatei größer als 25 MB")
    return str(p)


def analyze_table(ctx, a):
    return result(worker(ctx, {"action": "analyze", "source": source(ctx, a), "sheet": a.get("sheet", 0),
                               "columns": a.get("columns")}))


def emit(ctx, a, request, suffixes):
    p = resolve(ctx, str(a["output"]))
    if p.suffix.lower() not in suffixes:
        raise ToolError("Erlaubte Ausgabeformate: " + ", ".join(suffixes))
    if p.exists() and not a.get("overwrite"):
        raise ToolError("Ausgabedatei existiert; anderen Namen wählen oder overwrite explizit setzen")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".jarvis-", suffix=p.suffix, dir=p.parent)
    os.close(fd)
    try:
        data = worker(ctx, {**request, "output": tmp})
        if ctx.cancel.is_set():
            raise ToolError("Abgebrochen; Ausgabe nicht übernommen")
        if not Path(tmp).stat().st_size:
            raise ToolError("Leere Ausgabe, Datei nicht übernommen")
        # Ohne overwrite auch bei einem zwischenzeitlich erzeugten Ziel nicht ersetzen.
        if a.get("overwrite"):
            os.replace(tmp, p)
        else:
            os.link(tmp, p)
            os.unlink(tmp)
        return result({**data, "path": str(p), "bytes": p.stat().st_size})
    finally:
        Path(tmp).unlink(missing_ok=True)


def plot_table(ctx, a):
    return emit(ctx, a, {"action": "plot", "source": source(ctx, a), "sheet": a.get("sheet", 0),
                        "kind": a.get("kind", "line"), "x": a.get("x"), "y": a.get("y"),
                        "title": a.get("title", "Auswertung")}, (".png",))


def create_document(ctx, a):
    return emit(ctx, a, {"action": "document", "title": a.get("title", "Bericht"),
                        "paragraphs": a.get("paragraphs") or [], "rows": a.get("rows") or []},
                (".pdf", ".docx", ".xlsx"))


def tools():
    return [
        Tool("analyze_table", "analysis", "CSV/TSV/XLSX: Spalten, Fehlwerte, Zahlenstatistik, bis 100000 Zeilen.",
             {"path": S, "sheet": S, "columns": {"type": "array", "items": S}}, ("path",), analyze_table, untrusted=True),
        Tool("plot_table", "analysis", "Diagramm aus CSV/XLSX als PNG erstellen (line, bar, hist).",
             {"path": S, "output": S, "x": S, "y": S, "kind": {**S, "enum": ["line", "bar", "hist"]},
              "sheet": S, "title": S, "overwrite": B}, ("path", "output", "y"), plot_table, output_assess, untrusted=True),
        Tool("create_document", "documents_create", "PDF, Word oder Excel mit Titel, Absätzen und Tabelle erstellen; Format aus output-Endung.",
             {"output": S, "title": S, "paragraphs": {"type": "array", "items": S},
              "rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}}},
              "overwrite": B}, ("output",), create_document, output_assess),
    ]
