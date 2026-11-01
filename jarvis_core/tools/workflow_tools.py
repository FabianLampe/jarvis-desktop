"""Gespeicherte Werkzeugrezepte; Ausführung bleibt im normalen ToolAgent."""
from __future__ import annotations

import json
import os
import re
import tempfile

from .extension_utils import S, confirm, result
from .registry import Tool, ToolError


def path(ctx, name):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(name)):
        raise ToolError("Workflow-Name: 1–64 Buchstaben/Ziffern/_/-")
    folder = ctx.data_dir / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{name}.json"


def validate(steps, limit=6):
    if not isinstance(steps, list) or not 1 <= len(steps) <= limit:
        raise ToolError(f"Ein Workflow braucht 1–{limit} Schritte")
    for step in steps:
        if (not isinstance(step, dict) or not isinstance(step.get("tool"), str)
                or not isinstance(step.get("args"), dict) or step["tool"].startswith("workflow_")):
            raise ToolError("Jeder Schritt braucht tool und args; verschachtelte Workflows sind nicht erlaubt")
    if len(json.dumps(steps)) > 30000:
        raise ToolError("Workflow zu groß")


def load_workflow(ctx, name):
    p = path(ctx, name)
    if not p.is_file():
        raise ToolError("Workflow nicht gefunden")
    if p.stat().st_size > 40000:
        raise ToolError("Workflow zu groß")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ToolError("Ungültiges Workflow-Format")
    validate(data.get("steps"), max(1, ctx.cfg.tools.max_steps))
    return data


def workflow_save(ctx, a):
    validate(a.get("steps"), max(1, ctx.cfg.tools.max_steps))
    # Auch direkte Aufrufer können nur registrierte Werkzeugnamen speichern.
    from . import build_registry
    reg = build_registry()
    for step in a["steps"]:
        t = reg.get(step["tool"])
        if t is None:
            raise ToolError(f"Unbekanntes Werkzeug: {step['tool']}")
        if error := reg.argument_error(t.name, step["args"]):
            raise ToolError(error)
    p = path(ctx, a["name"])
    data = {"name": a["name"], "description": str(a.get("description", "")), "steps": a["steps"]}
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return result({"saved": a["name"], "steps": len(a["steps"])})


def workflow_list(ctx, a):
    folder = path(ctx, "placeholder").parent
    return result({"workflows": [p.stem for p in sorted(folder.glob("*.json"))][:100]})


def workflow_get(ctx, a):
    return result(load_workflow(ctx, a["name"]))


def workflow_run(ctx, a):
    # Kein verschachteltes reg.execute: jede Aktion benötigt assess/confirm/cancel.
    raise ToolError("Workflow-Ausführung erfolgt über den ToolAgent")


def tool_catalog(ctx, a):
    from . import build_registry
    reg = build_registry()
    group = a.get("group")
    if not group:
        return result({"groups": {g: reg.names({g}) for g in sorted(reg.groups())}})
    return result({"tools": reg.specs({str(group)})})


def tools():
    return [
        Tool("tool_catalog", "workflows", "Werkzeuggruppen anzeigen; mit group die Schemas zum Planen eines Workflows abrufen.",
             {"group": S}, (), tool_catalog),
        Tool("workflow_save", "workflows", "Werkzeugfolge dauerhaft speichern; jeder Schritt hat tool und args.",
             {"name": S, "description": S, "steps": {"type": "array", "items": {"type": "object",
              "properties": {"tool": S, "args": {"type": "object"}}, "required": ["tool", "args"]}}},
             ("name", "steps"), workflow_save, confirm("WORKFLOW SPEICHERN/ERSETZEN")),
        Tool("workflow_list", "workflows", "Gespeicherte Workflows auflisten.", {}, (), workflow_list),
        Tool("workflow_get", "workflows", "Schritte eines Workflows anzeigen.", {"name": S}, ("name",), workflow_get, untrusted=True),
        Tool("workflow_run", "workflows", "Gespeicherten Workflow über normale Einzelprüfungen ausführen; stoppt bei Fehler/Abbruch.",
             {"name": S}, ("name",), workflow_run),
    ]
