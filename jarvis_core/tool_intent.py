"""Welche Werkzeuggruppen braucht eine Anfrage? Deterministisch, ohne Modell.

Hält die Werkzeugliste klein (kleine lokale Modelle) und erkennt einfache Befehle
für den Schnellpfad ohne LLM."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_G: dict[str, re.Pattern] = {
    "files": re.compile(r"\b(datei|dateien|ordner|verzeichnis|pfad|readme|dokumentenordner|"
                        r"lies|lese|schreib\w*|anleg\w*|erstell\w*|kopier\w*|verschieb\w*|umbenenn\w*|"
                        r"lösch\w*|entfern\w*|bearbeit\w*|ändere|speicher\w*)\b|\.(md|txt|py|json|csv|yaml|js|html)\b", re.I),
    "terminal": re.compile(r"\b(terminal|shell|befehl\w*|skript\w*|script\w*|ausführ\w*|führ\w* .* aus|"
                           r"prozess\w*|brew|git|pip|npm|python3?|laufende)\b", re.I),
    "system": re.compile(r"\b(öffne|starte|starten|app|programm|systeminfo|system-?info|lautstärke|leiser|lauter|"
                         r"zwischenablage|benachrichtig\w*|arbeitsspeicher|prozessor|welcher mac|welche macos|betriebssystem)\b", re.I),
    "memory": re.compile(r"\b(merk|merke|merken|notiz\w*|notier\w*|vergiss|vergessen|was weißt du über|"
                         r"gespeichert\w*|erinnerst du dich|weißt du noch)\b", re.I),
    "docs": re.compile(r"\b(dokument\w*|pdf|unterlagen|wissensbasis|vertrag|im text|laut meinen|"
                       r"indexier\w*|durchsuch\w*)\b", re.I),
    "reminders": re.compile(r"\b(erinner\w*|wecker|timer|erinnerung\w*|jeden (tag|montag|dienstag|mittwoch|donnerstag|freitag)|"
                            r"täglich|wöchentlich|monatlich)\b", re.I),
    "research": re.compile(r"\b(recherchier\w*|recherche|quelle\w*|im internet|im netz|websuche|aktuell\w*|neueste\w*|"
                           r"nachrichten|such\w* .* (online|web))\b", re.I),
    "math": re.compile(r"\b(berechne|rechne|ableit\w*|integral|integrier\w*|gleichung|löse|faktorisier\w*|"
                       r"wurzel|prozent von|\d+\s*[\+\-\*/\^]\s*\d+)\b|\d\s*[\*/\^]\s*\d", re.I),
    "screen": re.compile(r"\b(bildschirm|screenshot|was steht (gerade )?auf (meinem|dem) (bildschirm|schirm)|ocr)\b", re.I),
    "integrations": re.compile(r"\b(kalender|termin\w*|spotify|musik|lied|song|licht|lampe|home assistant|"
                               r"smart ?home|telegram|discord|integration\w*|übersetz\w*)\b", re.I),
    "weather": re.compile(r"\b(wetter|regnet|regnen|regen|schirm|sonnig|schnee|gewitter|temperatur|wie warm|wie kalt|windig)\b", re.I),
    "browser": re.compile(r"\b(browser|webseite|website|seite|youtube|google|klick\w*|tab|tabs)\b", re.I),
}


def select_groups(text: str) -> set[str]:
    groups = {g for g, rx in _G.items() if rx.search(text)}
    # „öffne <Datei/App>“ ohne weitere Hinweise: System reicht
    if "terminal" in groups and "files" in groups and len(groups) > 2:
        groups.discard("system")
    return groups


_OPEN = re.compile(r"^(?:bitte\s+)?(?:öffne|starte|open)\s+(?:die\s+app\s+|das\s+programm\s+|die\s+anwendung\s+)?(?P<n>[\w .\-+&]+?)[.!?]?$", re.I)
_REMEMBER = re.compile(r"^(?:bitte\s+)?merk(?:e)?\s+dir[,:]?\s+(?:dass\s+)?(?P<t>.+)$", re.I | re.S)
_APP_DIRS = (Path("/Applications"), Path("/System/Applications"), Path("/System/Applications/Utilities"),
             Path("/Applications/Utilities"), Path.home() / "Applications")


def installed_app(name: str) -> Optional[str]:
    """Findet eine macOS-App (Groß-/Kleinschreibung egal)."""
    low = name.strip().lower()
    for d in _APP_DIRS:
        try:
            for p in d.glob("*.app"):
                if p.stem.lower() == low:
                    return p.stem
        except OSError:
            continue
    return None


_MATH_OPS = [
    (re.compile(r"^(?:berechne\s+)?(?:die\s+)?ableitung\s+(?:von|der)\s+(?P<e>.+)$", re.I), "diff"),
    (re.compile(r"^(?:berechne\s+)?(?:das\s+)?integral\s+(?:von|der)\s+(?P<e>.+)$|^integriere\s+(?P<e2>.+)$", re.I), "integrate"),
    (re.compile(r"^(?:löse|lösen sie)\s+(?:die\s+gleichung\s+)?(?P<e>.+)$", re.I), "solve"),
    (re.compile(r"^(?:faktorisiere)\s+(?P<e>.+)$", re.I), "factor"),
    (re.compile(r"^(?:berechne|rechne(?:\s+aus)?|was ist)\s+(?P<e>[\d\s\+\-\*/\^\(\)\.,]+|.*(?:sqrt|sin|cos|log|wurzel).*)$", re.I), "evaluate"),
]


def _math(t: str) -> Optional[list[dict]]:
    t = t.strip().rstrip("?!. ")
    for rx, op in _MATH_OPS:
        if (m := rx.match(t)):
            expr = (m.groupdict().get("e") or m.groupdict().get("e2") or "").strip().replace(",", ".")
            expr = re.sub(r"\bwurzel\s*(?:aus|von)?\s*(\d+)", r"sqrt(\1)", expr, flags=re.I)
            if expr and re.fullmatch(r"[\w\s\+\-\*/\^\(\)\.=]+", expr):
                return [{"tool": "calculate", "args": {"expr": expr, "op": op}}]
    return None


_WEATHER_Q = re.compile(r"\bwetter\b|\bwird es (?:heute|morgen)?\s*regnen\b|\bregnet es\b|\bbrauche ich (?:einen )?schirm\b|\bwie (?:warm|kalt) (?:wird|ist) es\b", re.I)
_PLACE = re.compile(r"\bin\s+([A-ZÄÖÜ][\wäöüß\-]+(?:\s[A-ZÄÖÜ][\wäöüß\-]+)?)")


def _weather(t: str) -> Optional[list[dict]]:
    if not _WEATHER_Q.search(t):
        return None
    low = t.lower()
    when = "übermorgen" if re.search(r"über?morgen|uebermorgen", low) else \
           "morgen" if re.search(r"\bmorgen\b(?!s)", low) else "heute"
    args: dict = {"when": when}
    if (m := _PLACE.search(t)):
        args["place"] = m.group(1)
    return [{"tool": "weather", "args": args}]


_CLOCK = re.compile(r"\b(wie sp[äa]t (ist es|haben wir)|wie viel uhr|wieviel uhr|welche uhrzeit|wie sp[äa]t)\b|"
                    r"\b(welcher tag ist (heute|es)|welches datum|den wievielten|welchen tag haben wir)\b", re.I)


def _clock(t: str) -> Optional[list[dict]]:
    m = _CLOCK.search(t)
    if not m:
        return None
    date_q = bool(re.search(r"tag|datum|wievielten", m.group(0), re.I))
    return [{"tool": "clock", "args": {"what": "date" if date_q else "time"}}]


def fast_path(text: str) -> Optional[list[dict]]:
    """Eindeutige Einzelbefehle ohne LLM: [{"tool","args"}] oder None."""
    t = text.strip()
    if (calc := _math(t)):
        return calc
    if (w := _weather(t)):
        return w
    if (c := _clock(t)):
        return c
    if (m := _REMEMBER.match(t)):
        return [{"tool": "memory_save", "args": {"text": m.group("t").strip().rstrip("."), "kind": "fact"}}]
    if (m := _OPEN.match(t)):
        name = m.group("n").strip()
        if (app := installed_app(name)):
            return [{"tool": "open_app", "args": {"target": app}}]
    return None
