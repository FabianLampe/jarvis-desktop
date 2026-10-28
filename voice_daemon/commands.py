"""Sprachbefehle, die der Daemon selbst behandelt (nie an die App gehen).

classify(text) → "sleep"  (Gespräch beenden, zurück zum Wake Word)
              | "cancel" (Anfrage verwerfen, direkt weiterzuhören)
              | None     (normale Äußerung)

Regeln gegen Fehltreffer: Mehrwort-Muster greifen bis 8 Wörter Gesamtlänge,
Einzelwort-Kommandos („Stop", „Ende") nur bei sehr kurzen Äußerungen (≤ 3
Wörter) — „Wie stoppe ich einen Dienst?" bleibt eine normale Frage.
"""

from __future__ import annotations

import re
from typing import Optional

_PUNCT = re.compile(r"[^\wäöüß ]+", re.UNICODE)
_LEAD = ("hey", "jarvis", "okay", "ok", "also")

SLEEP_PHRASES = (
    "hör auf zuzuhören", "hör auf zu zuhören", "hör auf zu hören",
    "hör auf", "schlaf weiter", "geh schlafen", "leg dich schlafen",
    "das war alles", "das war es", "das wars",
    "gespräch beenden", "gute nacht", "bis später", "danke das wäre alles",
)
SLEEP_WORDS = ("aufhören", "ruhe", "ende", "schluss")

CANCEL_PHRASES = (
    "nicht gemeint", "vergiss es", "vergiss das", "schon gut",
    "falsch verstanden", "lass es", "lass gut sein", "nicht so gemeint",
)
CANCEL_WORDS = ("stop", "stopp", "abbrechen", "abbruch", "egal")

MAX_PHRASE_WORDS = 8
MAX_SINGLE_WORDS = 3


def _normalize(text: str) -> str:
    # Apostrophe verschmelzen („war's" → „wars"), Rest der Satzzeichen → Leerzeichen
    norm = _PUNCT.sub(" ", text.lower().replace("'", "").replace("’", ""))
    words = norm.split()
    while words and words[0] in _LEAD:
        words.pop(0)
    return " ".join(words)


def classify(text: str) -> Optional[str]:
    norm = _normalize(text)
    if not norm:
        return None
    words = norm.split()
    if len(words) > MAX_PHRASE_WORDS:
        return None

    for phrase in SLEEP_PHRASES:
        if phrase in norm:
            return "sleep"
    for phrase in CANCEL_PHRASES:
        if phrase in norm:
            return "cancel"

    if len(words) <= MAX_SINGLE_WORDS:
        if any(w in SLEEP_WORDS for w in words):
            return "sleep"
        if any(w in CANCEL_WORDS for w in words):
            return "cancel"
    return None


_BRIEFING = ("briefing", "tagesbriefing", "was steht heute an", "was steht an",
             "tagesübersicht", "lagebericht", "morgenbriefing")


def is_briefing(text: str) -> bool:
    """Erkennt den Wunsch nach dem Tagesbriefing (Sprach-Fallback zum Klatsch)."""
    norm = _normalize(text)
    return any(p in norm for p in _BRIEFING)


def _norm(t: str) -> str:
    import re
    return re.sub(r"[^a-zäöüß0-9 ]", "", t.lower()).strip()


def is_own_echo(heard: str, recent: list[str], min_ratio: float = 0.75) -> bool:
    """True, wenn die erkannte „Äußerung“ Jarvis' eigene, gerade gesprochene Ausgabe ist
    (Lautsprecher → Mikrofon). Vergleicht unscharf mit den letzten gesprochenen Texten."""
    from difflib import SequenceMatcher
    h = _norm(heard)
    if len(h) < 8:
        return False
    for r in recent:
        n = _norm(r)
        if h in n or SequenceMatcher(None, h, n[: max(len(h) * 2, 40)]).ratio() >= min_ratio:
            return True
        # Teilstück: bestes Fenster gleicher Länge innerhalb des gesprochenen Textes
        w = len(h)
        best = max((SequenceMatcher(None, h, n[i:i + w]).ratio() for i in range(0, max(1, len(n) - w + 1), max(1, w // 3))), default=0)
        if best >= 0.8:
            return True
    return False
