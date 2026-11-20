"""Vorlieben selbst merken: Aussagen, die Sir über sich macht, werden als Fakt gespeichert.

Rein deterministisch, ohne Modell — und nur aus SEINEN eigenen Nachrichten (Tippen oder Sprechen). Nie aus
Werkzeugergebnissen, Webseiten, Dateien oder Antworten; der Aufrufer reicht ausschließlich die Nutzereingabe herein.

Erkannt werden (jeweils ein ganzer Satz, kein Fragesatz):
  „Ich heiße Fabian.“ · „Mein Name ist …“ · „Ich wohne/lebe in Hamburg.“ · „Ich komme aus …“
  „Ich arbeite als/bei/für …“ · „Ich studiere …“ · „Ich bin von Beruf …“
  „Ich mag/liebe/bevorzuge/hasse …“ · „Ich trinke/esse/höre gern …“ · „Mein Lieblings… ist …“
  „Ich bin allergisch gegen …“ · „Ich bin Vegetarier/Veganer“

Bewusste Grenzen gegen Falschtreffer: Sätze, die Jarvis ansprechen oder einen Nebensatz einleiten
(„Ich mag nicht, dass du …“, „Ich hasse es, wenn …“, „Ich mag es, wenn du kurz antwortest“), und Sätze mit
Befehlswörtern („Ich mag Kaffee, mach mir einen“) werden NICHT gemerkt — dafür gibt es „Merk dir, dass …“.
Ein Fakt mit festem Thema (Name, Wohnort, Beruf, Lieblings-X) ersetzt den älteren Eintrag zum selben Thema."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_PRE = r"^(?:(?:übrigens|also|ach ja|na ja|ja|nebenbei|zur info|damit du es weißt|jarvis|hey|hör mal|und|aber|ähm|äh)[,:]?\s+)*"
_NAME = r"[A-ZÄÖÜ][\wäöüß\-]+(?:\s[A-ZÄÖÜ][\wäöüß\-]+){0,2}"
_PLACE = r"[A-ZÄÖÜ][\wäöüß\-]+(?:[ \-][A-ZÄÖÜ][\wäöüß\-]+){0,3}"
_END = r"\s*[.!]?\s*$"
_QUESTION_START = re.compile(r"^(?:wie|was|wer|wo|wann|warum|wieso|weshalb|welche\w*|kannst|könntest|weißt|magst|bin ich|"
                             r"habe ich|heiße ich|wohne ich|ist|sind|gibt)\b", re.I)
# Alles, was nach Anrede an Jarvis, Nebensatz oder Auftrag aussieht.
_REJECT = re.compile(
    r"\b(?:du|dich|dir|dein\w*|ihr|euch|jarvis|dass|wenn|weil|ob|damit|sobald|während|obwohl|falls)\b|"
    r"\b(?:mach\w*|zeig\w*|öffne|starte\w*|spiel\w*|such\w*|erstell\w*|schreib\w*|sag\w*|schalte\w*|bitte|erinner\w*|"
    r"lies|lösch\w*|find\w*|hol\w*|gib|schick\w*|sende\w*|ruf\w*|kauf\w*|bestell\w*)\b|[?;:]", re.I)

_LIKE_VERBS = {"mag": "mag", "liebe": "liebt", "bevorzuge": "bevorzugt", "hasse": "hasst",
               "trinke gern": "trinkt gern", "trinke gerne": "trinkt gern", "esse gern": "isst gern",
               "esse gerne": "isst gern", "höre gern": "hört gern", "höre gerne": "hört gern",
               "lese gern": "liest gern", "lese gerne": "liest gern", "spiele gern": "spielt gern",
               "spiele gerne": "spielt gern", "koche gern": "kocht gern", "koche gerne": "kocht gern"}
_LIKE = re.compile(_PRE + r"ich (?P<v>" + "|".join(sorted(map(re.escape, _LIKE_VERBS), key=len, reverse=True)) +
                   r")\s+(?P<o>.+?)" + _END, re.I)

# (Muster, Schlüssel, Satzbau). Schlüssel None ⇒ kein festes Thema (nur Dubletten vermeiden).
_RULES: list[tuple[re.Pattern, Optional[str], str]] = [
    (re.compile(_PRE + r"(?:ich heiße|mein name ist)\s+(?P<o>" + _NAME + r")" + _END, re.I), "name", "Sir heißt {o}."),
    (re.compile(_PRE + r"ich (?:wohne|lebe) (?:jetzt |derzeit |zurzeit |momentan |schon lange )?in\s+(?P<o>" + _PLACE + r")" + _END, re.I),
     "wohnort", "Sir wohnt in {o}."),
    (re.compile(_PRE + r"ich komme aus\s+(?P<o>" + _PLACE + r")" + _END, re.I), "herkunft", "Sir kommt aus {o}."),
    (re.compile(_PRE + r"ich arbeite\s+(?P<p>als|bei|für|im|in der|an der|an einer)\s+(?P<o>[^,]{2,60}?)" + _END, re.I),
     "beruf", "Sir arbeitet {p} {o}."),
    (re.compile(_PRE + r"(?:ich bin von beruf|von beruf bin ich)\s+(?P<o>[^,]{2,50}?)" + _END, re.I), "beruf", "Sir ist von Beruf {o}."),
    (re.compile(_PRE + r"ich studiere\s+(?P<o>[^,]{2,50}?)" + _END, re.I), "studium", "Sir studiert {o}."),
    (re.compile(_PRE + r"ich bin allergisch (?P<p>gegen|auf)\s+(?P<o>[^,]{2,50}?)" + _END, re.I), None, "Sir ist allergisch {p} {o}."),
    (re.compile(_PRE + r"ich bin\s+(?P<o>vegetarier(?:in)?|veganer(?:in)?)" + _END, re.I), "ernaehrung", "Sir ist {o}."),
]
_FAVORITE = re.compile(_PRE + r"meine?n? lieblings(?P<k>[\wäöüß\-]+)\s+(?:ist|sind|war)\s+(?P<o>.+?)" + _END, re.I)
# Themen, bei denen ein neuer Fakt den alten ersetzt: Schlüssel → Beginn des gespeicherten Satzes.
_PREFIX = {"name": "Sir heißt ", "wohnort": "Sir wohnt in ", "herkunft": "Sir kommt aus ", "beruf": "Sir arbeitet ",
           "studium": "Sir studiert "}
MAX_FACTS = 3


@dataclass(frozen=True)
class Fact:
    text: str
    key: Optional[str] = None

    @property
    def prefix(self) -> Optional[str]:
        if self.key and self.key.startswith("fav:"):
            return f"Sirs Lieblings{self.key[4:]} ist "
        return _PREFIX.get(self.key or "")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


_FUNCTION_WORDS = {"der", "die", "das", "dem", "den", "des", "ein", "einem", "einer", "einen", "eine", "meiner", "meinem",
                   "meinen", "meine", "mein", "nähe", "nicht", "kein", "keinem", "keiner", "unserer", "unserem", "diesem",
                   "dieser", "diesen", "einer"}


def _proper_name(o: str) -> bool:
    """Name/Ort muss im ORIGINALTEXT wirklich großgeschrieben sein (re.I im Muster prüft das nicht)
    und darf nicht mit Artikel/Funktionswort beginnen oder „nicht“ enthalten."""
    words = o.split()
    return bool(words) and words[0][0].isupper() and words[0].casefold() not in _FUNCTION_WORDS \
        and not any(w.casefold() == "nicht" for w in words)


def _clean_object(o: str) -> Optional[str]:
    o = re.sub(r"\s+", " ", o).strip(" ,")
    if not (2 <= len(o) <= 60) or _REJECT.search(o):
        return None
    if re.match(r"^(?:es|das|dies\w*|nicht|so)\b", o, re.I):      # „Ich mag es, wenn …“, „Ich mag nicht, dass …“
        return None
    return o


def extract(text: str) -> list[Fact]:
    """Fakten aus einer Nutzernachricht. Leere Liste, wenn nichts Eindeutiges dasteht."""
    out: list[Fact] = []
    if not text or text.lstrip().startswith("["):
        return out
    sentences = _sentences(text)
    if len(text) > 300 or len(sentences) > 3:          # sieht nach eingefügtem Fremdtext aus (Mail, Artikel …)
        return out
    for sent in sentences:
        if sent.endswith("?") or _QUESTION_START.match(sent) or len(sent) > 160:
            continue
        if (m := _FAVORITE.match(sent)):
            o = _clean_object(m.group("o"))
            if o and not _REJECT.search(sent):
                k = m.group("k").lower()
                out.append(Fact(f"Sirs Lieblings{k} ist {o}.", f"fav:{k}"))
            continue
        matched = False
        for rx, key, frame in _RULES:
            if (m := rx.match(sent)):
                matched = True
                o = _clean_object(m.group("o"))
                if o and key in ("name", "wohnort", "herkunft") and not _proper_name(o):
                    o = None
                if o and not (key not in ("name", "wohnort", "herkunft") and _REJECT.search(sent)):
                    gd = m.groupdict()
                    out.append(Fact(frame.format(o=o, p=gd.get("p") or ""), key))
                break
        if matched:
            continue
        if (m := _LIKE.match(sent)):
            o = _clean_object(m.group("o"))
            if o:
                out.append(Fact(f"Sir {_LIKE_VERBS[m.group('v').lower()]} {o}.", None))
    seen, unique = set(), []
    for f in out:
        if f.text.casefold() not in seen:
            seen.add(f.text.casefold())
            unique.append(f)
    return unique[:MAX_FACTS]


def learn(memory, text: str) -> list[tuple[str, bool]]:
    """Speichert erkannte Fakten (Dubletten werden übersprungen, Themen aktualisiert).
    Gibt [(Fakt, aktualisiert?)] der tatsächlich neu gemerkten Einträge zurück."""
    done: list[tuple[str, bool]] = []
    for fact in extract(text):
        existing = memory.recent(500)
        if any(r["text"].casefold() == fact.text.casefold() for r in existing):
            continue
        prefix = fact.prefix
        old = next((r for r in existing if prefix and r["kind"] == "fact" and r["text"].startswith(prefix)), None)
        if old is not None:
            memory.update(old["id"], fact.text)
            done.append((fact.text, True))
        else:
            _, new = memory.add(fact.text, "fact")
            if new:
                done.append((fact.text, False))
    return done
