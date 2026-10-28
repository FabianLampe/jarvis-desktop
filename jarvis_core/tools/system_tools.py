"""Systemwerkzeuge: Programme öffnen, Systeminfo, Lautstärke, Zwischenablage,
Benachrichtigung, Bildschirm-OCR. Betriebssystemspezifisches steckt im Adapter."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ..platform import PlatformError
from .paths import resolve
from .process import ProcessRunner
from .registry import (ALLOW, CONFIRM, Assessment, Tool, ToolContext, ToolError,
                       ToolResult, wrap_untrusted)

_SAFE_URL = re.compile(r"^(https?|mailto)://|^mailto:", re.I)
_RISKY_SUFFIX = {".command", ".sh", ".app", ".pkg", ".dmg", ".terminal", ".workflow", ".scpt",
                 ".exe", ".bat", ".cmd", ".ps1", ".msi", ".jar", ".py"}


def _target(ctx: ToolContext, a: dict) -> tuple[str, str]:
    """(kind, value): url | path | app"""
    t = str(a.get("target", "")).strip()
    if not t:
        raise ToolError("target fehlt (App-Name, Datei oder URL)")
    if "://" in t or t.lower().startswith("mailto:"):
        if not _SAFE_URL.match(t):
            raise ToolError("Nur http(s)- und mailto-Adressen werden geöffnet")
        return "url", t
    if "/" in t or t.startswith(("~", ".")) or Path(t).suffix:
        return "path", str(resolve(ctx, t, must_exist=True))
    return "app", t


def open_app(ctx: ToolContext, a: dict) -> ToolResult:
    kind, value = _target(ctx, a)
    argv = ctx.platform.open_target(value)
    res = ProcessRunner(ctx.platform).run(argv, ctx.workspace, timeout_s=15, max_chars=1500)
    if not res.ok:
        detail = (res.stderr or res.error or "").strip().splitlines()
        raise ToolError(f"„{value}“ ließ sich nicht öffnen"
                        + (f": {detail[0]}" if detail else f" (Rückgabecode {res.exit_code})"))
    return ToolResult(True, f"Geöffnet: {value}")


def _assess_open(ctx: ToolContext, a: dict) -> Assessment:
    try:
        kind, value = _target(ctx, a)
    except ToolError:
        return Assessment(ALLOW)
    if kind == "path" and Path(value).suffix.lower() in _RISKY_SUFFIX:
        return Assessment(CONFIRM, f"Ausführbare/Skript-Datei öffnen: {value}")
    if ctx.tainted and kind != "app":
        return Assessment(CONFIRM, f"Nach Lesen fremder Inhalte öffnen: {value}")
    return Assessment(ALLOW)


_TAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
           "November", "Dezember"]


def clock(ctx: ToolContext, a: dict) -> ToolResult:
    """Datum und Uhrzeit von der Systemuhr (Europe/Berlin) — ohne Sprachmodell, sofort."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    n = datetime.now(ZoneInfo(ctx.cfg.reminders.timezone))
    what = str(a.get("what") or "both")
    date = f"{_TAGE[n.weekday()]}, der {n.day}. {_MONATE[n.month - 1]} {n.year}"
    time_ = f"{n:%H:%M} Uhr"
    text = {"time": f"Es ist {time_}, Sir.", "date": f"Heute ist {date}, Sir."}.get(
        what, f"Es ist {time_}, {date}, Sir.")
    return ToolResult(True, text)


def system_info(ctx: ToolContext, a: dict) -> ToolResult:
    info = ctx.platform.system_info()
    gpu = ctx.platform.gpu_status()
    if gpu:
        info["vram_mb"] = f"{gpu[0]}/{gpu[1]}"
    return ToolResult(True, "\n".join(f"{k}: {v}" for k, v in info.items() if v is not None), info)


def get_set_volume(ctx: ToolContext, a: dict) -> ToolResult:
    try:
        if a.get("percent") is None:
            v = ctx.platform.get_volume()
            if v is None:
                raise ToolError("Lautstärke nicht lesbar")
            return ToolResult(True, f"Lautstärke: {v} %")
        ctx.platform.set_volume(int(a["percent"]))
        return ToolResult(True, f"Lautstärke auf {max(0, min(100, int(a['percent'])))} % gesetzt (gelesen: {ctx.platform.get_volume()} %)")
    except PlatformError as exc:
        raise ToolError(str(exc)) from exc


def clipboard(ctx: ToolContext, a: dict) -> ToolResult:
    try:
        if a.get("text") is not None:
            ctx.platform.clipboard_set(str(a["text"]))
            return ToolResult(True, "In die Zwischenablage kopiert")
        return ToolResult(True, wrap_untrusted(ctx.platform.clipboard_get()[:4000]))
    except PlatformError as exc:
        raise ToolError(str(exc)) from exc


def notify(ctx: ToolContext, a: dict) -> ToolResult:
    ok = ctx.platform.notify(str(a.get("title") or "Jarvis"), str(a.get("text", "")))
    if not ok:
        raise ToolError("Benachrichtigung nicht zugestellt (Mitteilungen für das Terminal/die App erlaubt?)")
    return ToolResult(True, "Benachrichtigung gesendet")


def screen_ocr(ctx: ToolContext, a: dict) -> ToolResult:
    """Screenshot → lokale OCR. Das Bild wird danach gelöscht, nur Text bleibt."""
    with tempfile.TemporaryDirectory(prefix="jarvis-ocr-") as tmp:
        img = Path(tmp) / "screen.png"
        try:
            ctx.platform.screenshot(img)
            text = ctx.platform.ocr_image(img).strip()
        except PlatformError as exc:
            raise ToolError(str(exc)) from exc
    if not text:
        return ToolResult(True, "Auf dem Bildschirm wurde kein Text erkannt.")
    return ToolResult(True, "Text auf dem Bildschirm:\n" + wrap_untrusted(text[:4000]))


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("open_app", "system", "Öffnet eine App (z. B. Safari), Datei oder http(s)-Adresse.",
             {"target": {**S, "description": "App-Name, Pfad oder URL"}}, ("target",), open_app,
             _assess_open, announce=lambda a: f"Ich öffne {a.get('target', '')}, Sir."),
        Tool("clock", "system", "Aktuelle Uhrzeit und/oder Datum (what: time|date|both).",
             {"what": {**S, "enum": ["time", "date", "both"]}}, (), clock),
        Tool("system_info", "system", "Betriebssystem, Chip, Arbeitsspeicher, Python/Node/Ollama.",
             {}, (), system_info),
        Tool("volume", "system", "Liest oder setzt die Lautstärke (percent 0–100).",
             {"percent": {"type": "integer"}}, (), get_set_volume),
        Tool("clipboard", "system", "Liest die Zwischenablage oder setzt sie (text).",
             {"text": S}, (), clipboard, untrusted=True),
        Tool("notify", "system", "Zeigt eine Systembenachrichtigung.",
             {"title": S, "text": S}, ("text",), notify),
        Tool("screen_ocr", "screen", "Liest den Text, der gerade auf dem Bildschirm steht (lokale OCR).",
             {}, (), screen_ocr, untrusted=True,
             announce=lambda a: "Ich sehe auf den Bildschirm, Sir."),
    ]
