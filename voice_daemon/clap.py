"""Doppelklatsch-Erkennung auf dem Mikrofon-Stream (Energie-Transienten).

Ein Klatsch = kurzer, lauter Breitband-Impuls (1–2 Frames weit über dem
Grundpegel, danach sofort wieder leise). Zwei davon im Abstand [gap_min, gap_max]
→ Auslöser. Dauer-Lautstärke (Sprache/Musik) wird verworfen (zu lange laut).

Zeit wird in Frames gemessen (deterministisch testbar): 1 Frame = frame/sr s.
"""
from __future__ import annotations

import numpy as np


class ClapDetector:
    def __init__(self, sr: int = 16000, frame: int = 1280, threshold: float = 4.0,
                 gap_min_s: float = 0.15, gap_max_s: float = 0.8, cooldown_s: float = 3.0,
                 min_floor: float = 120.0, max_burst: int = 3):
        fs = frame / sr
        self.threshold = threshold
        self.gap_min = gap_min_s / fs
        self.gap_max = gap_max_s / fs
        self.cooldown = cooldown_s / fs
        self.min_floor = min_floor
        self.max_burst = max_burst
        self._noise = min_floor
        self._i = 0
        self._above_run = 0
        self._was_above = False
        self._onset = 0
        self._last_clap: float | None = None
        self._cooldown_until = -1.0

    def process(self, frame_int16: np.ndarray) -> bool:
        i = self._i
        self._i += 1
        rms = float(np.sqrt(np.mean(frame_int16.astype(np.float32) ** 2)))
        thr = max(self.min_floor, self._noise) * self.threshold
        above = rms > thr
        triggered = False

        if above:
            if not self._was_above:
                self._onset = i           # steigende Flanke = möglicher Klatsch-Beginn
            self._above_run += 1
        else:
            if self._was_above:           # fallende Flanke = Impuls endete
                if self._above_run <= self.max_burst and i >= self._cooldown_until:
                    onset = self._onset
                    if (self._last_clap is not None
                            and self.gap_min <= (onset - self._last_clap) <= self.gap_max):
                        triggered = True
                        self._cooldown_until = i + self.cooldown
                        self._last_clap = None
                    else:
                        self._last_clap = onset
                else:
                    self._last_clap = None  # zu langer Lärm → kein Klatsch
            else:
                # nur in ruhigen Frames den Grundpegel nachführen
                self._noise = 0.98 * self._noise + 0.02 * rms
            self._above_run = 0

        self._was_above = above
        return triggered

    def reset(self) -> None:
        self._last_clap = None
        self._above_run = 0
        self._was_above = False
