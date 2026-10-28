"""STT: faster-whisper auf CPU, Sprache fest auf Deutsch.

Kein Auto-Detect mehr — das war die Wurzel des alten Problems („er versteht
mich nicht"): erkannt wurde Englisch mit Akzent statt Deutsch. language="de"
+ VAD-Filter + deutscher Kontext-Prompt liefern ChatGPT-ähnliche Trefferquote.
"""

from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger("daemon.stt")

INITIAL_PROMPT = ("Gespräch auf Deutsch mit J.A.R.V.I.S., dem persönlichen "
                  "KI-Assistenten. Der Nutzer gibt Befehle und stellt Fragen.")


class Transcriber:
    def __init__(self, model: str = "small", language: str = "de",
                 compute: str = "int8", threads: int = 0):
        self.threads = int(threads)            # 0 = Bibliotheksstandard; 12 ≈ 2× schneller auf M-Serie
        self.model_name = model
        self.language = language
        self.compute = compute
        self._model = None

    def load(self) -> None:
        """Lädt das Modell (erster Aufruf lädt ggf. die Gewichte herunter)."""
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        t0 = time.monotonic()
        log.info("Lade Whisper „%s“ (CPU, %s) …", self.model_name, self.compute)
        try:
            self._model = WhisperModel(self.model_name, device="cpu", compute_type=self.compute, cpu_threads=self.threads)
        except Exception as exc:  # noqa: BLE001 — Download/Speicher: auf „small“ ausweichen statt stumm zu bleiben
            if self.model_name == "small":
                raise
            log.warning("Whisper „%s“ nicht ladbar (%s) — nutze „small“", self.model_name, exc)
            self.model_name = "small"
            self._model = WhisperModel("small", device="cpu", compute_type=self.compute, cpu_threads=self.threads)
        log.info("Whisper „%s“ bereit (%.1f s)", self.model_name, time.monotonic() - t0)

    def transcribe(self, audio_f32_16k: np.ndarray) -> str:
        """Erwartet mono float32 [-1,1] mit 16 kHz; liefert erkannten Text."""
        if audio_f32_16k.size < 1600:  # < 0,1 s
            return ""
        self.load()
        assert self._model is not None
        t0 = time.monotonic()
        segments, info = self._model.transcribe(
            audio_f32_16k,
            language=self.language,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=INITIAL_PROMPT,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("STT (%.1f s Audio → %.1f s Rechenzeit): %r",
                 audio_f32_16k.size / 16000, time.monotonic() - t0, text)
        return text
