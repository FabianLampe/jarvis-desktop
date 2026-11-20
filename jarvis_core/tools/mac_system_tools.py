"""Mac-Systemhandgriffe und Dateisuche: `system_control` (feste Aktionen) und `spotlight_search` (mdfind).

system_control kennt nur eine feste Liste von Aktionen mit festen Befehlen — kein freier Text vom Modell
erreicht je einen Skripttext oder eine Shell. Spotlight-Suche prüft Namen/Dateitypen: Der Suchbegriff wird auf
harmlose Zeichen reduziert, bevor er in die (feste) mdfind-Abfrage kommt; Treffer sind persönliche Daten (PRIVATE)
und ihre Namen fremder Text."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import mac_script as ms
from .network_tools import clean
from .paths import is_protected
from .registry import ALLOW, CONFIRM, Assessment, Tool, ToolContext, ToolError, ToolResult, wrap_untrusted

# ------------------------------------------------------------ system_control

DARK_SCRIPTS = {
    "dark_mode_on": 'tell application "System Events" to tell appearance preferences to set dark mode to true',
    "dark_mode_off": 'tell application "System Events" to tell appearance preferences to set dark mode to false',
    "dark_mode_toggle": 'tell application "System Events" to tell appearance preferences to set dark mode to not dark mode',
}
LOCK_SCRIPT = 'tell application "System Events" to keystroke "q" using {control down, command down}'
TRASH_SCRIPT = 'tell application "Finder" to empty trash'

ACTIONS = ["dark_mode_on", "dark_mode_off", "dark_mode_toggle", "lock_screen", "display_sleep", "empty_trash",
           "wifi_on", "wifi_off", "do_not_disturb"]
CONFIRMED = {"empty_trash": "PAPIERKORB ENDGÜLTIG LEEREN", "wifi_on": "WLAN EINSCHALTEN", "wifi_off": "WLAN AUSSCHALTEN"}
DONE = {"dark_mode_on": "Dunkelmodus ist an.", "dark_mode_off": "Dunkelmodus ist aus.",
        "dark_mode_toggle": "Darstellung umgeschaltet.", "lock_screen": "Bildschirm gesperrt.",
        "display_sleep": "Bildschirm schläft.", "empty_trash": "Papierkorb geleert.",
        "wifi_on": "WLAN ist an.", "wifi_off": "WLAN ist aus."}
DND_HELP = ("„Nicht stören“ lässt sich auf dem Mac nicht direkt schalten, ohne einen Kurzbefehl. Legen Sie in der App "
            "Kurzbefehle einen Befehl an (Aktion „Fokus festlegen“, z. B. „Nicht stören an“) und tragen Sie seinen "
            "Namen in config.yaml unter shortcuts.allowed ein; dann führe ich ihn auf Zuruf aus.")


def wifi_device() -> str:
    rc, out, _ = ms.cmd(["networksetup", "-listallhardwareports"], 8)
    m = re.search(r"Hardware Port:\s*(?:Wi-?Fi|AirPort)[^\n]*\nDevice:\s*(\w+)", out, re.I)
    if rc != 0 or not m:
        raise ToolError("Ich finde keine WLAN-Schnittstelle an diesem Mac.")
    return m.group(1)


def system_control(ctx: ToolContext, a: dict) -> ToolResult:
    action = str(a.get("action", ""))
    if action not in ACTIONS:
        raise ToolError("action muss eines von " + ", ".join(ACTIONS) + " sein.")
    if action == "do_not_disturb":
        return ToolResult(False, DND_HELP)
    if action in DARK_SCRIPTS:
        ms.osa(DARK_SCRIPTS[action], app="Systemeinstellungen")
    elif action == "lock_screen":
        ms.osa(LOCK_SCRIPT, app="System Events")
    elif action == "display_sleep":
        rc, _, err = ms.cmd(["pmset", "displaysleepnow"], 8)
        if rc != 0:
            raise ToolError("Der Bildschirm ließ sich nicht schlafen legen: " + (err.strip() or "unbekannter Fehler")[:120])
    elif action == "empty_trash":
        ms.osa(TRASH_SCRIPT, app="Finder", timeout=120)
    else:
        rc, _, err = ms.cmd(["networksetup", "-setairportpower", wifi_device(), "on" if action == "wifi_on" else "off"], 15)
        if rc != 0:
            raise ToolError("WLAN ließ sich nicht schalten: " + (err.strip() or "unbekannter Fehler")[:120])
    return ToolResult(True, DONE[action])


def _assess_system(ctx: ToolContext, a: dict) -> Assessment:
    action = str(a.get("action", ""))
    if action in CONFIRMED:
        return Assessment(CONFIRM, CONFIRMED[action])
    if ctx.tainted and action != "do_not_disturb":
        return Assessment(CONFIRM, f"SYSTEMAKTION {action} (nach Lesen fremder Inhalte)")
    return Assessment(ALLOW)


# --------------------------------------------------------- spotlight_search

KINDS = {
    "pdf": 'kMDItemContentType == "com.adobe.pdf"',
    "bild": 'kMDItemContentTypeTree == "public.image"',
    "dokument": '(kMDItemContentTypeTree == "public.composite-content" || kMDItemContentType == "com.adobe.pdf" '
                '|| kMDItemContentTypeTree == "public.presentation" || kMDItemContentTypeTree == "public.spreadsheet" '
                '|| kMDItemContentTypeTree == "public.plain-text")',
    "app": 'kMDItemContentType == "com.apple.application-bundle"',
    "ordner": 'kMDItemContentType == "public.folder"',
    "musik": 'kMDItemContentTypeTree == "public.audio"',
    "video": 'kMDItemContentTypeTree == "public.movie"',
}
KIND_WORDS = {"bilder": "bild", "foto": "bild", "fotos": "bild", "image": "bild", "dokumente": "dokument",
              "document": "dokument", "apps": "app", "programm": "app", "programme": "app", "verzeichnis": "ordner",
              "folder": "ordner", "pdfs": "pdf", "audio": "musik", "film": "video", "filme": "video"}
MAX_HITS = 20
_QUERY_SAFE = re.compile(r"[^0-9A-Za-zÄÖÜäöüß ._\-]")


def kind_of(raw) -> str | None:
    k = str(raw or "").strip().casefold()
    if not k:
        return None
    k = KIND_WORDS.get(k, k)
    if k not in KINDS:
        raise ToolError("kind muss eines von " + ", ".join(KINDS) + " sein (oder leer).")
    return k


def build_query(query: str, kind: str | None, field: str) -> str:
    """Feste mdfind-Abfrage; der Suchbegriff darf nur harmlose Zeichen enthalten (kein Abfrage-Syntax)."""
    term = re.sub(r" +", " ", _QUERY_SAFE.sub("", query)).strip()
    if not term:
        raise ToolError("Bitte einen Suchbegriff angeben (Buchstaben/Ziffern).")
    q = f'{field} == "*{term}*"cd'
    return f"({q}) && {KINDS[kind]}" if kind else q


def _search(home: Path, query: str) -> list[str]:
    rc, out, err = ms.cmd(["mdfind", "-onlyin", str(home), query], 20)
    if rc != 0:
        raise ToolError("Die Spotlight-Suche ist nicht verfügbar: " + (err.strip().splitlines() or ["?"])[-1][:100])
    return [l for l in out.splitlines() if l.startswith("/")]


def spotlight_search(ctx: ToolContext, a: dict) -> ToolResult:
    query = str(a.get("query", "")).strip()
    kind = kind_of(a.get("kind"))
    home = Path.home()
    paths = _search(home, build_query(query, kind, "kMDItemDisplayName"))
    if not paths:      # nichts im Namen: Inhalt durchsuchen
        paths = _search(home, build_query(query, kind, "kMDItemTextContent"))
    library = str(home / "Library") + os.sep
    keep = []
    for p in paths:
        if p.startswith(library) or "/." in p or is_protected(ctx, Path(p)):
            continue
        keep.append(p)

    def mtime(p: str) -> float:
        try:
            return os.stat(p).st_mtime
        except OSError:
            return 0.0
    keep.sort(key=mtime, reverse=True)
    if not keep:
        return ToolResult(True, f"Spotlight findet nichts zu „{clean(query, 40)}“" + (f" ({kind})" if kind else "") + ".")
    lines = []
    for p in keep[:MAX_HITS]:
        rest = p[len(str(home)):].lstrip("/") if p.startswith(str(home) + "/") else p
        shown = ("~/" + rest) if rest is not p else p
        c = ("~/" if rest is not p else "") + clean(rest, 200)
        lines.append(f"- {c}" + ("" if c == shown else "  (Sonderzeichen im Namen weggelassen)"))
    more = f"\n… {len(keep) - MAX_HITS} weitere nicht gezeigt" if len(keep) > MAX_HITS else ""
    return ToolResult(True, f"{len(keep)} Treffer:\n" + wrap_untrusted("\n".join(lines)) + more, {"count": len(keep)})


def tools() -> list[Tool]:
    S = {"type": "string"}
    return [
        Tool("system_control", "system",
             "Feste Mac-Aktionen: dark_mode_on/off/toggle, lock_screen (Bildschirm sperren), display_sleep, "
             "empty_trash (Papierkorb leeren, mit Rückfrage), wifi_on/wifi_off (mit Rückfrage), do_not_disturb.",
             {"action": {**S, "enum": ACTIONS}}, ("action",), system_control, _assess_system,
             announce=lambda a: "Ich erledige das am Mac, Sir."),
        Tool("spotlight_search", "spotlight",
             "Sucht Dateien per Spotlight im Benutzerordner nach Namen (sonst Inhalt), optional nach kind "
             "(pdf, bild, dokument, app, ordner, musik, video). Schnell, auch für „wo ist meine Datei …“.",
             {"query": S, "kind": S}, ("query",), spotlight_search, untrusted=True,
             announce=lambda a: "Ich suche mit Spotlight, Sir."),
    ]
