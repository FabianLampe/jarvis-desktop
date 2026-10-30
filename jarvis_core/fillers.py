"""Überbrückung langer Denkpausen — nur geprüfte Inhalte, KEIN Sprachmodell.

Ein Modell-generierter „Fun Fact“ erfand im Test Fakten (falscher „erster Leuchtturm“) und verdrängte
die echte Ansage. Deshalb: eine kuratierte Liste gut belegter Technik-Fakten und echtes Wetter."""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, Optional

FACTS = [
    "Der erste dokumentierte Computer-Bug war 1947 eine echte Motte im Relais des Harvard Mark II.",
    "Python ist nach der Komikertruppe Monty Python benannt, nicht nach der Schlange.",
    "Das Wort Roboter stammt aus Karel Čapeks Theaterstück R.U.R. von 1920.",
    "Der Apollo-Bordcomputer, der 1969 auf dem Mond landete, hatte nur rund vier Kilobyte Arbeitsspeicher.",
    "Die erste Webseite der Welt ging 1991 am CERN in Genf online.",
    "Die erste E-Mail verschickte Ray Tomlinson 1971 — er führte dabei auch das @-Zeichen ein.",
    "Das erste Handytelefonat führte Martin Cooper 1973 in New York, mit einem Gerät von gut einem Kilogramm.",
    "Die Computermaus wurde 1968 von Douglas Engelbart öffentlich vorgeführt.",
    "Linux begann 1991 als Hobbyprojekt des Studenten Linus Torvalds.",
    "Die Programmiersprache Java hieß in der Entwicklung zunächst Oak — nach einer Eiche vor dem Büro.",
    "Bluetooth ist nach dem dänischen König Harald Blauzahn benannt.",
    "Das QWERTY-Tastenlayout entstand in den 1870er-Jahren für mechanische Schreibmaschinen.",
    "Ada Lovelace veröffentlichte bereits 1843 einen Algorithmus für Babbages Analytical Engine.",
    "Alan Turing beschrieb 1950 den nach ihm benannten Test, ob Maschinen denken können.",
    "Licht braucht von der Sonne bis zur Erde gut acht Minuten.",
    "Der Begriff Bit ist eine Verkürzung von binary digit.",
    "Der erste PC-Virus, Brain, entstand 1986 in Pakistan.",
    "Das erste Foto im World Wide Web zeigte 1992 die CERN-Band Les Horribles Cernettes.",
]

LEADS = ["Kleine Randnotiz, während ich daran arbeite, Sir: ", "Am Rande, Sir: ", "Übrigens, Sir: "]


class FillerSource:
    """Liefert abwechselnd geprüfte Fakten und (falls verfügbar) echtes Wetter, ohne Wiederholung."""

    def __init__(self, weather: Optional[Callable[[], Optional[str]]] = None, seed: Optional[int] = None):
        self._rnd = random.Random(seed)
        self._facts = FACTS[:]
        self._rnd.shuffle(self._facts)
        self._weather = weather
        self._weather_cache: tuple[float, Optional[str]] = (0.0, None)
        self._lock = threading.Lock()
        self._n = 0

    def _weather_line(self) -> Optional[str]:
        if self._weather is None:
            return None
        ts, text = self._weather_cache
        if ts == 0 or time.monotonic() - ts > 1800: # auch kurz nach Systemstart einmal abfragen
            try:
                text = self._weather()
            except Exception:  # noqa: BLE001 — Füller ist Kür
                text = None
            self._weather_cache = (time.monotonic(), text)
        return text

    def next(self, request: str = "") -> str:
        with self._lock:
            self._n += 1
            lead = LEADS[(self._n - 1) % len(LEADS)]
            wants_weather = self._n % 2 == 0 and "wetter" not in request.lower()
            if wants_weather and (w := self._weather_line()):
                return f"Kleine Zwischeninfo zum Wetter, Sir: {w}"
            if not self._facts:
                self._facts = FACTS[:]
                self._rnd.shuffle(self._facts)
            return lead + self._facts.pop()
