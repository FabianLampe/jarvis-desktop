"""Explicit local lifecycle commands; never passed to the shell or an LLM."""
from __future__ import annotations

import re


def lifecycle_command(text: str) -> str | None:
    normalized = re.sub(r"\bj[.\s]*a[.\s]*r[.\s]*v[.\s]*i[.\s]*s\b\.?", "jarvis", text.lower())
    words = re.sub(r"[^\wäöüß]+", " ", normalized).split()
    if words in (["hey", "jarvis"], ["hey", "javis"], ["jarvis"], ["javis"]):
        return "wake"
    while words and words[0] in ("hey", "jarvis", "javis", "bitte", "okay", "ok"):
        words.pop(0)
    while words and words[-1] in ("bitte", "jarvis", "javis"):
        words.pop()
    phrase = " ".join(words)
    commands = {
        "wake": {"wake up", "aufwachen", "wach auf", "wache auf", "aufstehen"},
        "reboot": {"system reboot", "system neustarten", "system neu starten", "system neustart",
                   "reboot", "neustarten", "neu starten", "neustart", "starte dich neu"},
        "sleep": {"schlafen", "geh schlafen", "gehe schlafen", "schlaf weiter", "standby",
                  "gute nacht", "leg dich schlafen", "geh in standby", "schlaf gut", "schlaf schön"},
        "music_stop": {"musik aus", "musik stoppen", "stoppe die musik", "musik ausschalten"},
    }
    exact = next((action for action, phrases in commands.items() if phrase in phrases), None)
    if exact:
        return exact
    # Tolerant gegenüber Spracherkennungs-Varianten („geht schlafen“, „geh mal schlafen“) — aber nur kurze
    # Befehle, damit „Wie lange sollte ich schlafen?“ eine normale Frage bleibt.
    if len(words) <= 5:
        if re.fullmatch(r"(?:(?:geh|gehe|geht|gehst|leg|lege|legst|ab)\s+)?(?:(?:mal|jetzt|bitte|doch|dich|ruhig|nun)\s+){0,3}"
                        r"(?:schlafen|in den (?:standby|ruhemodus|schlafmodus)|ins bett)(?:\s+gehen)?", phrase) \
                or phrase in ("schlafen gehen", "ruhemodus", "schlafmodus", "standby modus", "geh in den ruhemodus"):
            return "sleep"
        if re.fullmatch(r"(?:(?:mal|jetzt|bitte|wieder)\s+){0,2}(?:wach(?:e|t)?\s+(?:(?:mal|jetzt|bitte|wieder)\s+)?auf|aufwachen|"
                        r"wake\s+up|bist du wach|wieder wach|aufstehen)", phrase):
            return "wake"
    return None
