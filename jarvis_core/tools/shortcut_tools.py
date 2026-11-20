"""Apple Kurzbefehle: auflisten und ausführen (`shortcuts list` / `shortcuts run`).

Ein Kurzbefehl kann beliebig viel tun (Nachrichten senden, Dateien ändern, das Licht schalten). Deshalb gilt:
Ausführen braucht eine Bestätigung — außer der Name steht in config `shortcuts.allowed` UND in diesem
Auftrag wurde kein Fremdinhalt gelesen. Es laufen nur Kurzbefehle, die es wirklich gibt (exakter Abgleich mit
der Liste von macOS); Namen kommen als argv-Element, nie über eine Shell. Namen und Ausgaben sind fremder Text."""
from __future__ import annotations

import os
import tempfile

from . import mac_script as ms
from .network_tools import clean
from .registry import (ALLOW, CONFIRM, Assessment, InvalidArgs, Tool, ToolContext, ToolError, ToolResult,
                       wrap_untrusted)

MAX_LISTED = 80
MAX_INPUT_CHARS = 4000
MAX_OUTPUT_CHARS = 4000
RUN_TIMEOUT_S = 90.0


def list_names() -> list[str]:
    """Echte Namen aller Kurzbefehle (unbereinigt — nur zum exakten Abgleich, nie ans Modell)."""
    rc, out, err = ms.cmd(["shortcuts", "list"], 20)
    if rc != 0:
        raise ToolError("Kurzbefehle sind nicht verfügbar (" + (err.strip().splitlines() or ["unbekannter Fehler"])[-1][:120] + ")")
    return [l.strip() for l in out.splitlines() if l.strip()]


def resolve_name(requested: str) -> str:
    """Findet den echten Namen. Das Modell sieht bereinigte Namen und liefert sie zurück; deshalb gilt
    ein Treffer, wenn der echte oder der bereinigte Name übereinstimmt — und nur, wenn er eindeutig ist."""
    wanted = str(requested or "").strip()
    if not wanted:
        raise InvalidArgs("Kein Kurzbefehl angegeben.")
    names = list_names()
    hits = [n for n in names if n == wanted] or [n for n in names if clean(n, 200) == clean(wanted, 200)] \
        or [n for n in names if n.casefold() == wanted.casefold()]
    if len(set(hits)) == 1:
        name = hits[0]
        if name.startswith("-"):
            raise ToolError("Kurzbefehle, deren Name mit „-“ beginnt, führe ich nicht aus.")
        return name
    if len(set(hits)) > 1:
        raise InvalidArgs(f"„{clean(wanted)}“ ist nicht eindeutig; es gibt mehrere Kurzbefehle mit diesem Namen.")
    near = [clean(n, 40) for n in names if wanted.casefold() in n.casefold()][:8]
    hint = (" Ähnlich: " + ", ".join(near)) if near else " Mit shortcuts_list sehe ich nach, welche es gibt."
    raise InvalidArgs(f"Einen Kurzbefehl „{clean(wanted)}“ gibt es nicht.{hint}")


def shortcuts_list(ctx: ToolContext, a: dict) -> ToolResult:
    names = list_names()
    if not names:
        return ToolResult(True, "Es sind keine Kurzbefehle angelegt (App „Kurzbefehle“).")
    shown = [clean(n, 60) for n in names[:MAX_LISTED]]
    allowed = {x.casefold() for x in ctx.cfg.shortcuts.allowed}
    lines = [f"- {c}" + (" (ohne Rückfrage erlaubt)" if n.casefold() in allowed else "")
             for c, n in zip(shown, names)]
    more = f"\n… und {len(names) - MAX_LISTED} weitere" if len(names) > MAX_LISTED else ""
    return ToolResult(True, f"{len(names)} Kurzbefehle:\n" + wrap_untrusted("\n".join(lines)) + more,
                      {"count": len(names)})


def shortcut_run(ctx: ToolContext, a: dict) -> ToolResult:
    name = resolve_name(a.get("name", ""))
    argv = ["shortcuts", "run", name]
    tmp = None
    try:
        text = a.get("input")
        if text:
            text = str(text)[:MAX_INPUT_CHARS]
            fd, tmp = tempfile.mkstemp(prefix="jarvis-shortcut-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            argv += ["--input-path", tmp]
        rc, out, err = ms.cmd(argv, RUN_TIMEOUT_S)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    shown = clean(name, 60)
    if rc != 0:
        detail = clean((err.strip().splitlines() or ["unbekannter Fehler"])[-1], 160)
        raise ToolError(f"Kurzbefehl „{shown}“ ist fehlgeschlagen: {detail}")
    body = out.strip()
    if not body:
        return ToolResult(True, f"Kurzbefehl „{shown}“ ausgeführt.")
    return ToolResult(True, f"Kurzbefehl „{shown}“ ausgeführt. Ausgabe:\n" + wrap_untrusted(body[:MAX_OUTPUT_CHARS]))


def _assess_run(ctx: ToolContext, a: dict) -> Assessment:
    name = resolve_name(a.get("name", ""))            # InvalidArgs/ToolError vor jeder Rückfrage
    extra = f" mit Eingabe „{clean(str(a['input']), 80)}“" if a.get("input") else ""
    allowed = {x.casefold() for x in ctx.cfg.shortcuts.allowed}
    if name.casefold() in allowed and not ctx.tainted:
        return Assessment(ALLOW)
    why = " (nach Lesen fremder Inhalte)" if ctx.tainted and name.casefold() in allowed else ""
    return Assessment(CONFIRM, f"KURZBEFEHL AUSFÜHREN: „{clean(name, 60)}“{extra}{why}")


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("shortcuts_list", "shortcuts", "Listet die Apple Kurzbefehle auf (Namen).", {}, (), shortcuts_list,
             untrusted=True),
        Tool("shortcut_run", "shortcuts",
             "Führt einen Apple Kurzbefehl aus (z. B. Smart-Home-Szene, Fokus, eigene Abläufe). name wie in "
             "shortcuts_list; input optional als Text-Eingabe. Gibt die Ausgabe des Kurzbefehls zurück.",
             {"name": S, "input": S}, ("name",), shortcut_run, _assess_run, untrusted=True,
             announce=lambda a: f"Ich führe den Kurzbefehl „{clean(str(a.get('name', '')), 40)}“ aus, Sir."),
    ]
