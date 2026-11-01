"""Lokaler Modell-Router: manuelle Schwerlast, optionales Reasoning, Coder/Allrounder.

Verhalten laut Spec/ADR 0002:
1. Erzwungen: 7B-Schalter aktiv oder Schwerlast-Schlüsselwort → heavy.
2. Profil mit Reasoner + passende Aufgabe/Modus on → Reasoning-Modell (ADR 0005).
3. Code-Indikatoren → Coder-Modell, sonst Allrounder.

Ein 3B-Klassifikator für mehrdeutige Fälle bleibt als Erweiterungspunkt
vorgesehen (route() ist die einzige Stelle, die dafür angefasst werden muss).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Profile, ReasoningConfig

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
    lane: str          # "general" | "coder" | "heavy" | "reasoning"
    model: str
    reason: str
    heavy_requested: bool = False
    heavy_unavailable: bool = False  # Schwerlast verlangt, aber Profil hat kein 7B
    reasoning: bool = False


_REASONING = re.compile(
    r"\b(analys\w*|vergleich\w*|vergleiche|abwäg\w*|abwaeg\w*|begründe\w*|begruende\w*|"
    r"plane|planen|planung|strategie\w*|optimier\w*|debug\w*|fehlersuche|fehler\w*|"
    r"warum|weshalb|ursache\w*|beweis\w*|herleit\w*|berechn\w*|rechnung|"
    r"reasoning|nachdenken|durchdenken|schrittweise|mehrstufig\w*|"
    r"analy[sz]e|compare|plan|debug|reason|prove)\b|"
    r"\b(schritt für schritt|step by step|denke nach|denk nach)\b", re.I)
_ACTION = re.compile(r"\b(erstell\w*|lege|lies|lese|prüf\w*|schreib\w*|öffne|starte|"
                     r"such\w*|recherchier\w*|kopier\w*|verschieb\w*|test\w*|zeig\w*|"
                     r"installier\w*|führ\w*)\b", re.I)


def wants_reasoning(text: str, settings: ReasoningConfig, *, failed: bool = False) -> bool:
    """Nachvollziehbare lokale Heuristik, kein zusätzlicher Klassifikator-Aufruf."""
    if settings.mode == "off":
        return False
    return (settings.mode == "on" or failed or bool(_REASONING.search(text))
            or looks_like_code(text)
            or len(_ACTION.findall(text)) >= 2
            or bool(re.search(r"\b(zuerst|anschließend|anschliessend|danach)\b", text, re.I)))


def wants_heavy(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in HEAVY_KEYWORDS)


def looks_like_code(text: str) -> bool:
    if any(p.search(text) for p in _CODE_PATTERNS):
        return True
    words = set(w.lower() for w in _WORD_RE.findall(text))
    return any(w in words for w in _CODE_WORDS)


def route(text: str, force_7b: bool, profile: Profile,
          reasoning: ReasoningConfig | None = None) -> RouteDecision:
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

    if profile.reasoning and reasoning and wants_reasoning(text, reasoning):
        return RouteDecision("reasoning", profile.reasoning, "Automatisches Reasoning", reasoning=True)

    if looks_like_code(text):
        return RouteDecision("coder", profile.coder, "Code-Indikator")

    return RouteDecision("general", profile.general, "Allrounder (Default)")
