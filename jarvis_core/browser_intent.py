"""Schlüsselwort-Schnellpfad für Browser-Befehle — bewusst deterministisch.

Das kleine LLM ist zu schwach/halluziniert beim Tool-Entscheiden, daher erkennen
wir die gängigen Befehle hier hart und führen sie über tools.browser aus.

- detect(text)      → eine Aktion {"tool","args"} oder None (erste Teilanweisung)
- detect_all(text)  → Liste von Aktionen; verkettet „… und … / … dann …"
                      (z. B. „öffne X und lies vor"). Leer ⇒ kein Browser-Befehl.
"""
from __future__ import annotations

import re
from typing import Optional

KNOWN_SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "wikipedia": "https://de.wikipedia.org",
    "gmail": "https://mail.google.com",
    "spotify": "https://open.spotify.com",
}

_URL_RE = re.compile(r"\b([a-z0-9-]+\.[a-z]{2,}(?:/\S*)?)\b", re.IGNORECASE)

# „spiel/play <q> [auf youtube] [ab]" → erstes YouTube-Treffer abspielen
_PLAY_VERBS = ("spiele", "spiel", "play")
# „such(e)/google [mir] [auf <engine>] [nach] <q>"
_SEARCH_RE = re.compile(
    r"^(?:such|suche|google)\b\s*(?:mir\s+)?"
    r"(?:auf\s+(?P<engine>youtube|google)\s+)?(?:nach\s+)?(?P<q>.+)$",
    re.IGNORECASE)
# „öffne / geh zu / gehe auf / navigiere zu / ruf(e) auf <target>"
_OPEN_RE = re.compile(
    r"^(?:öffne(?:\s+mal)?|geh(?:e)?\s+(?:zu|auf)|navigiere\s+zu|ruf(?:e)?\s+auf)\s+(?P<t>.+)$",
    re.IGNORECASE)

_NAV_BACK = {"geh zurück", "zurück", "zurück gehen", "geh zurueck"}
_NAV_FORWARD = {"geh vor", "vorwärts", "vor", "vorwaerts"}
_NAV_RELOAD = {"lade neu", "neu laden", "aktualisiere", "seite neu laden", "lad neu", "neu"}
_READ = ("lies", "vorlesen", "vorliest", "was steht", "fasse die seite", "fass die seite",
         "zusammenfassen", "zusammenfassung")
_SPLIT_RE = re.compile(r"\s+(?:und|dann|danach)\s+", re.IGNORECASE)
# Füllwörter am Anfang einer Zielangabe („die seite von/vom …")
_TARGET_PREFIX = re.compile(r"^(?:die\s+|der\s+|das\s+)?(?:seite\s+|webseite\s+|website\s+)?"
                            r"(?:von\s+|vom\s+|der\s+)?", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(".!?")).strip()


def _clause(text: str) -> Optional[dict]:
    """Erkennt EINE Teilanweisung."""
    cleaned = _clean(text)
    low = cleaned.lower()
    if not low:
        return None

    if low in _NAV_BACK:
        return {"tool": "navigate", "args": {"action": "back"}}
    if low in _NAV_FORWARD:
        return {"tool": "navigate", "args": {"action": "forward"}}
    if low in _NAV_RELOAD:
        return {"tool": "navigate", "args": {"action": "reload"}}

    # Vorlesen / Seiteninhalt (auch „lies mir den ersten satz vor")
    if low.startswith(("lies", "vorlesen", "fasse die seite", "fass die seite")) \
            or low in ("was steht auf der seite", "seite vorlesen", "lies vor"):
        return {"tool": "read_page", "args": {}}

    # Scrollen
    if low.startswith(("scroll", "scrolle")):
        up = any(w in low for w in ("hoch", "rauf", "oben"))
        return {"tool": "scroll", "args": {"direction": "up" if up else "down"}}

    # Play (YouTube): „spiel <q> [auf youtube] [ab]"
    for verb in _PLAY_VERBS:
        if low.startswith(verb + " "):
            rest = cleaned[len(verb):].strip()
            low_rest = rest.lower()
            if "youtube" in low_rest or low_rest.endswith((" ab", " an")) \
                    or "abspielen" in low_rest or verb == "play":
                q = re.sub(r"\s+(?:auf|on)\s+youtube\b", " ", rest, flags=re.IGNORECASE)
                q = re.sub(r"\b(?:ab|an|abspielen|abspielst|bitte)\s*$", "", q,
                           flags=re.IGNORECASE).strip(" ,")
                if q:
                    return {"tool": "play_youtube", "args": {"query": q}}

    # Suche (vor dem Öffnen — „suche auf youtube nach …")
    m = _SEARCH_RE.match(cleaned)
    if m:
        query = m.group("q").strip(" :,")
        if query:
            return {"tool": "search", "args": {"query": query,
                                               "engine": (m.group("engine") or "google").lower()}}

    # Öffnen: bekannte Seite / URL → open_url; sonst googeln + erstes Ergebnis öffnen
    m = _OPEN_RE.match(cleaned)
    if m:
        target = m.group("t").strip(" :,")
        low_t = target.lower()
        for name, url in KNOWN_SITES.items():
            if re.search(r"\b" + name + r"\b", low_t):
                return {"tool": "open_url", "args": {"url": url}}
        um = _URL_RE.search(target)
        if um:
            url = um.group(1)
            return {"tool": "open_url", "args": {"url": url if url.startswith("http") else "https://" + url}}
        # unbekanntes Ziel („die seite vom bundesministerium") → Suche + erstes Ergebnis
        query = _TARGET_PREFIX.sub("", target).strip()
        if query:
            return {"tool": "search_open", "args": {"query": query, "engine": "google"}}

    return None


def detect(text: str) -> Optional[dict]:
    """Erste erkannte Teilanweisung (Rückwärtskompatibilität)."""
    for part in _SPLIT_RE.split(text):
        action = _clause(part)
        if action is not None:
            return action
    return None


def detect_all(text: str) -> list[dict]:
    """Alle Teilanweisungen in Reihenfolge (verkettet mit und/dann/danach).

    „lies vor" ohne vorherige Aktion zählt mit; eine reine Nicht-Browser-Eingabe
    ergibt eine leere Liste.
    """
    actions: list[dict] = []
    for part in _SPLIT_RE.split(text):
        action = _clause(part)
        if action is not None:
            actions.append(action)
    return actions
