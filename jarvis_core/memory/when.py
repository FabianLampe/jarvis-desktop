"""Deutsche Zeitangaben → datetime (Europe/Berlin). Deterministisch, ohne Modell."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

_WEEKDAYS = {"montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3, "freitag": 4,
             "samstag": 5, "sonnabend": 5, "sonntag": 6}
_NUM_WORDS = {"einer": 1, "einem": 1, "eine": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4,
              "fünf": 5, "sechs": 6, "zehn": 10, "fünfzehn": 15, "zwanzig": 20, "dreißig": 30}
_HOUR_WORDS = {"eins": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
               "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12}


def _unit(word: str) -> str:
    for prefix, unit in (("sek", "seconds"), ("min", "minutes"), ("std", "hours"),
                         ("stund", "hours"), ("tag", "days"), ("woch", "weeks")):
        if word.startswith(prefix):
            return unit
    raise ValueError(f"Einheit nicht verstanden: {word}")


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else _HOUR_WORDS.get(tok, _NUM_WORDS.get(tok, 0))

REPEATS = ("daily", "weekdays", "weekly", "monthly")


def detect_repeat(text: str) -> Optional[str]:
    low = text.lower()
    if re.search(r"\b(jeden tag|täglich|taeglich|jeden morgen|jeden abend)\b", low):
        return "daily"
    if re.search(r"\b(werktags|jeden werktag|montags? (bis|-) freitags?|wochentags)\b", low):
        return "weekdays"
    if re.search(r"\b(jede woche|wöchentlich|woechentlich|jeden (montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)|(montags|dienstags|mittwochs|donnerstags|freitags|samstags|sonntags))\b", low):
        return "weekly"
    if re.search(r"\b(jeden monat|monatlich)\b", low):
        return "monthly"
    return None


_HW = "|".join(_HOUR_WORDS)


def _pm(h: int, low: str) -> int:
    """Umgangssprache: „um drei“ meint tagsüber 15 Uhr; „abends/nachmittags“ immer nachmittags/abends;
    „nachts“ ab 7 Uhr = 19–23 Uhr; „morgens/früh/vormittags“ bleibt vormittags."""
    if re.search(r"abend|nachmittag", low) and 1 <= h <= 11:
        return h + 12
    if re.search(r"nachts", low):
        return h + 12 if 7 <= h <= 11 else h
    if 1 <= h <= 6 and not re.search(r"morgens|früh|vormittag", low):
        return h + 12
    return h


def _time_of(low: str) -> Optional[tuple[int, int]]:
    low = re.sub(r"\b\d{1,2}\.\d{1,2}\.(?:\d{2,4})?", " ", low)          # Datum nicht als Uhrzeit lesen
    if (m := re.search(rf"\bhalb\s+(\d{{1,2}}|{_HW})\b", low)):
        return _pm((_num(m.group(1)) - 1) % 24, low), 30
    if (m := re.search(r"\b(\d{1,2}):(\d{2})\s*(?:uhr)?\b", low)):
        h, mi = int(m.group(1)), int(m.group(2))
        if h < 24 and mi < 60:
            return h, mi
    if (m := re.search(rf"\b(\d{{1,2}}|{_HW})\s*uhr(?:\s*(\d{{1,2}}))?\b", low)):
        return _pm(_num(m.group(1)) % 24, low), int(m.group(2) or 0)
    if (m := re.search(rf"\bum\s+(\d{{1,2}}|{_HW})\b", low)):
        return _pm(_num(m.group(1)) % 24, low), 0
    return None


def parse_when(text: str, now: Optional[datetime] = None, tz: str = "Europe/Berlin") -> datetime:
    """Versteht ISO-Zeit, „in 10 Minuten“, „morgen um 9 Uhr“, „Freitag 14:30“,
    „am 05.11. um 8“. Wirft ValueError, wenn nichts Eindeutiges erkannt wurde."""
    zone = ZoneInfo(tz)
    now = (now or datetime.now(zone)).astimezone(zone)
    s = text.strip()
    low = s.lower()

    # ISO 8601
    iso = re.search(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?", s)
    if iso:
        raw = iso.group(0).replace(" ", "T")
        d = datetime.fromisoformat(raw if "T" in raw else raw + "T09:00")
        return d.replace(tzinfo=zone, second=0, microsecond=0)

    # relativ
    m = re.search(r"\bin\s+(\d+|einer|einem|eine|ein|zwei|drei|vier|fünf|sechs|zehn|fünfzehn|zwanzig|dreißig)"
                  r"\s*(?:(halbe[rn]?)\s+)?(sekunden?|minuten?|min|stunden?|std|tagen?|wochen?)\b", low)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS[m.group(1)]
        unit = _unit(m.group(3))
        delta = timedelta(**{unit: n})
        if m.group(2):
            delta = timedelta(**{unit: n - 0.5})
        target = (now + delta).replace(microsecond=0)
        rest = low[:m.start()] + " " + low[m.end():]
        if unit in ("days", "weeks") and (t := _time_of(rest)) is not None:   # „in 2 Tagen um 9 Uhr“
            return _at(target.date(), t, zone)
        return target
    if "in einer halben stunde" in low or "in 30 minuten" in low:
        return (now + timedelta(minutes=30)).replace(microsecond=0)

    # Datum
    day = None
    if "übermorgen" in low or "uebermorgen" in low:
        day = (now + timedelta(days=2)).date()
    elif "morgen" in low and not re.search(r"\bmorgens\b|\bjeden morgen\b", low):
        day = (now + timedelta(days=1)).date()
    elif "heute" in low:
        day = now.date()
    elif (m := re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})?", low)):
        y = int(m.group(3)) if m.group(3) else now.year
        y += 2000 if y < 100 else 0
        day = datetime(y, int(m.group(2)), int(m.group(1))).date()
        if not m.group(3) and day < now.date():
            day = day.replace(year=y + 1)
    else:
        for name, wd in _WEEKDAYS.items():
            if re.search(rf"\b{name}s?\b", low):
                ahead = (wd - now.weekday()) % 7
                t0 = _time_of(low)
                if ahead == 0 and (t0 is None or _at(now.date(), t0, zone) <= now):
                    ahead = 7
                if "nächsten" in low or "naechsten" in low:
                    ahead = ahead or 7
                day = (now + timedelta(days=ahead)).date()
                break

    t = _time_of(low)
    if day is None and t is None:
        raise ValueError(f"Zeitangabe nicht verstanden: „{text}“")
    if t is None:
        t = (9, 0)
    if day is None:          # nur Uhrzeit: heute, sonst morgen
        day = now.date()
        if _at(day, t, zone) <= now:
            day = day + timedelta(days=1)
    return _at(day, t, zone)


def _at(day, t: tuple[int, int], zone) -> datetime:
    return datetime(day.year, day.month, day.day, t[0], t[1], tzinfo=zone)
