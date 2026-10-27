"""Modell-Router — Schlüsselwort-Schnellpfad (V1).

Verhalten laut Spec/ADR 0002:
1. Erzwungen: 7B-Schalter aktiv oder Schwerlast-Schlüsselwort → heavy.
2. Schnellpfad: Code-Indikatoren → Coder-Modell.
3. Sonst: Allrounder (darf Eskalation aufs 7B vorschlagen — macht die Persona).

Ein 3B-Klassifikator für mehrdeutige Fälle bleibt als Erweiterungspunkt
vorgesehen (route() ist die einzige Stelle, die dafür angefasst werden muss).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Profile

HEAVY_KEYWORDS = (
    "schwerlast",
    "tiefe recherche",
    "tiefenrecherche",
    "deep research",
    "gründliche recherche",
)

# Muster, die stark nach Code/Technik riechen (Backticks, Definitionen,
# Fehlermeldungs-Muster) — bewusst konservativ, der Allrounder ist der Default.
_CODE_PATTERNS = [
    re.compile(r"```"),                          # Codeblock
    re.compile(r"`[^`\n]+`"),                    # Inline-Code
    re.compile(r"\bdef \w+\s*\("),               # Python-Definition
    re.compile(r"\bclass \w+\s*[(:{]"),          # Klassendefinition
    re.compile(r"\bfunction\s+\w+\s*\("),        # JS-Funktion
    re.compile(r"\bimport \w+"),
    re.compile(r"#include\s*<"),
    re.compile(r"\w+(Error|Exception)\b"),       # Fehlermeldungs-Muster
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"\bstack\s?trace\b", re.IGNORECASE),
]

_CODE_WORDS = (
    "code", "quellcode", "skript", "script", "funktion", "methode", "klasse",
    "bug", "debug", "debuggen", "fehlermeldung", "syntaxfehler", "exception",
    "traceback", "stacktrace", "compiler", "kompilieren", "refactor",
    "programmieren", "programmiere", "implementieren", "implementiere",
    "python", "javascript", "typescript", "java", "c++", "c#", "rust",
    "golang", "kotlin", "swift", "sql", "regex", "html", "css", "json",
    "yaml", "bash", "powershell", "api", "endpoint", "unittest", "pytest",
)

_WORD_RE = re.compile(r"[a-zäöüß#+]+", re.IGNORECASE)


@dataclass(frozen=True)
class RouteDecision:
    lane: str          # "general" | "coder" | "heavy"
    model: str
    reason: str
    heavy_requested: bool = False
    heavy_unavailable: bool = False  # Schwerlast verlangt, aber Profil hat kein 7B


def wants_heavy(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in HEAVY_KEYWORDS)


def looks_like_code(text: str) -> bool:
    if any(p.search(text) for p in _CODE_PATTERNS):
        return True
    words = set(w.lower() for w in _WORD_RE.findall(text))
    return any(w in words for w in _CODE_WORDS)


def route(text: str, force_7b: bool, profile: Profile) -> RouteDecision:
    heavy_requested = force_7b or wants_heavy(text)

    if heavy_requested:
        if profile.heavy:
            reason = "7B-Schalter" if force_7b else "Schwerlast-Schlüsselwort"
            return RouteDecision("heavy", profile.heavy, reason, heavy_requested=True)
        # dev-Profil: Schwerlast nicht verfügbar → normal weiterrouten
        if looks_like_code(text):
            return RouteDecision("coder", profile.coder, "Code-Indikator (Schwerlast nicht verfügbar)",
                                 heavy_requested=True, heavy_unavailable=True)
        return RouteDecision("general", profile.general, "Allrounder (Schwerlast nicht verfügbar)",
                             heavy_requested=True, heavy_unavailable=True)

    if looks_like_code(text):
        return RouteDecision("coder", profile.coder, "Code-Indikator")

    return RouteDecision("general", profile.general, "Allrounder (Default)")
