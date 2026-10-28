"""Optionale Module: Offline-Übersetzung (Argos Translate) und Gesichtserkennung.

Beide sind standardmäßig aus. Übersetzung ist implementiert, sobald `argostranslate`
und ein Sprachpaket installiert sind; Gesichtserkennung ist bewusst NICHT implementiert
(biometrische Daten, Einwilligung/Datenschutz — siehe README)."""
from __future__ import annotations

import importlib.util
from typing import Any

from ..tools.registry import Tool, ToolContext, ToolError, ToolResult
from .base import Status


def optional_status(settings: dict[str, Any], name: str) -> Status:
    if not settings.get("enabled"):
        return Status("disabled", "optionales Modul, abgeschaltet")
    if name == "face_recognition":
        return Status("error", "nicht implementiert (Datenschutz: biometrische Daten) — siehe README")
    if importlib.util.find_spec("argostranslate") is None:
        return Status("needs_setup", "`pip install argostranslate` und ein Sprachpaket installieren", ["argostranslate"])
    return Status("ready", "argostranslate vorhanden")


def translate(ctx: ToolContext, a: dict) -> ToolResult:
    st = optional_status(ctx.cfg.integrations.get("translation") or {}, "translation")
    if st.state != "ready":
        raise ToolError(f"Offline-Übersetzung nicht einsatzbereit: {st.line('translation')}")
    import argostranslate.translate as T  # pyright: ignore[reportMissingImports] — optionales Modul
    src, dst = str(a.get("from") or "de"), str(a.get("to") or "en")
    try:
        out = T.translate(str(a.get("text", "")), src, dst)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"Übersetzung {src}→{dst} nicht möglich (Sprachpaket installiert?): {exc}") from exc
    return ToolResult(True, out)


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [Tool("translate", "integrations", "Übersetzt Text offline (optionales Modul).",
                 {"text": S, "from": S, "to": S}, ("text",), translate)]
