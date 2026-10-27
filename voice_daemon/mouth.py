"""Mund: TTS + Wiedergabe mit Live-Amplituden für den Orb.

Zwei Engines:
- edge  — edge-tts (deutsche Neural-Stimme, schnell, braucht Internet)
- xtts  — Coqui XTTS v2 Stimm-Klon aus einer Referenzaufnahme (Film-Jarvis).
          Auf der GPU (xtts_device=cuda) schneller als Echtzeit (RTF ~0,6) und
          teilt sich das VRAM mit dem LLM; auf der CPU ~4× Echtzeit (Rückfall).
          Satz-Streaming: der erste Satz wird gesprochen, während der Rest rechnet.

Die Amplituden gehen pro ~50-ms-Block an die App — der Orb-Kern pulst damit
zur echten Stimme (Filmreferenz „driven by dialogue waveforms").
"""

from __future__ import annotations

import asyncio
import io
import logging
import queue
import re
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

log = logging.getLogger("daemon.mouth")

_SENTENCE_END = re.compile(r"(?<=[.!?…:])\s+")


def sentence_chunks(text: str, first_max: int = 120, rest_max: int = 200) -> list[str]:
    """Teilt Text in sprechbare Happen: erster Satz allein (schneller erster
    Ton), Folgesätze zu Blöcken bis rest_max Zeichen gebündelt."""
    sentences = [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]
    if not sentences:
        return []
    chunks = [sentences[0][:first_max] if len(sentences[0]) > first_max else sentences[0]]
    buf = ""
    for s in sentences[1:]:
        if buf and len(buf) + len(s) + 1 > rest_max:
            chunks.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf:
        chunks.append(buf)
    return chunks

CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
MD_NOISE = re.compile(r"[*_#`>|]+")


def speakable(text: str, max_chars: int) -> str:
    """Macht eine Chat-Antwort vorlesbar: Code raus, Markdown-Zeichen weg."""
    text = CODE_BLOCK.sub(" Den Code zeige ich im Panel, Sir. ", text)
    text = MD_NOISE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        cut = cut[:cut.rfind(" ")] if " " in cut else cut
        text = cut + " … Den Rest zeige ich im Panel, Sir."
    return text


class Mouth:
    def __init__(self, voice: str = "de-DE-FlorianMultilingualNeural",
                 output_device=None, max_chars: int = 1200,
                 rate: str = "+0%", pitch: str = "+0Hz",
                 engine: str = "edge", speaker_wav: str | None = None,
                 language: str = "de", xtts_device: str = "auto",
                 robotic: float = 0.0, robotic_carrier_hz: float = 55.0):
        """rate/pitch im edge-tts-Format („-8%", „-3Hz") — der Film-Jarvis
        spricht gemessen und einen Hauch tiefer als die Standardstimme.

        robotic (0–1): dezenter Roboter-Effekt (Ringmodulation + metallische
        Resonanz) auf die fertige Stimme — gibt ihr einen synthetischen Anstrich
        und kaschiert nebenbei Unsauberkeiten des Stimm-Klons. 0 = aus."""
        self.voice = voice
        self.device = output_device
        self.max_chars = max_chars
        self.rate = rate
        self.pitch = pitch
        self.engine = engine
        self.speaker_wav = speaker_wav
        self.language = language
        self.xtts_device = xtts_device   # auto | cuda | cpu
        self._active_device = "cpu"      # nach load_xtts: tatsächliches Gerät
        self.robotic = max(0.0, min(1.0, robotic))
        self.robotic_carrier_hz = robotic_carrier_hz
        self._robo_n = 0                 # fortlaufender Sample-Zähler (Phasenkontinuität)
        self._xtts = None
        self._abort = False

    # ------------------------------------------------------------- XTTS

    def load_xtts(self) -> None:
        """Lädt das XTTS-v2-Modell (CPU, ~2 GB RAM; erster Lauf lädt Gewichte)."""
        if self._xtts is not None or self.engine != "xtts":
            return
        import os
        from pathlib import Path
        os.environ.setdefault("COQUI_TOS_AGREED", "1")  # CPML, private Nutzung
        # Modelle auf F: halten — C: ist knapp (Hardware-Realität in CONTEXT.md)
        os.environ.setdefault("TTS_HOME",
                              str(Path(__file__).resolve().parent.parent / ".models"))
        import torch
        from TTS.api import TTS

        dev = self.xtts_device
        if dev == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        log.info("Lade XTTS v2 (Stimm-Klon, %s) — das dauert einen Moment …", dev)
        self._xtts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        if dev == "cuda":
            try:
                self._xtts.to("cuda")
            except Exception:
                log.exception("XTTS passt nicht auf die GPU — falle auf CPU zurück")
                dev = "cpu"
                self._xtts.to("cpu")
        self._active_device = dev
        log.info("XTTS bereit (%s) — Referenz: %s", dev, self.speaker_wav)

    def _synth_xtts(self, text: str):
        self.load_xtts()
        wav = self._xtts.tts(text=text, speaker_wav=self.speaker_wav,
                             language=self.language, split_sentences=False)
        return np.asarray(wav, dtype=np.float32), 24000

    def _say_xtts_streamed(self, text: str,
                           on_amplitude: Optional[Callable[[float], None]],
                           on_sentence: Optional[Callable[[str], None]] = None) -> None:
        """Satz-Streaming: erster Satz spielt, während der Rest noch rechnet.
        on_sentence(text) feuert jeweils unmittelbar vor dem Abspielen — so
        erscheint der Text im Panel synchron zur gesprochenen Stimme."""
        chunks = sentence_chunks(text)
        if not chunks:
            return
        self.load_xtts()
        # (Satztext, Audio) — der Text wird erst beim Abspielen angezeigt
        audio_q: "queue.Queue[Optional[tuple[str, np.ndarray]]]" = queue.Queue(maxsize=4)

        def producer() -> None:
            try:
                for c in chunks:
                    if self._abort:
                        break
                    t0 = time.monotonic()
                    data, _sr = self._synth_xtts(c)
                    log.debug("XTTS-Happen: %.1f s Audio in %.1f s gerechnet",
                              data.size / 24000, time.monotonic() - t0)
                    audio_q.put((c, data))
            except Exception:
                log.exception("XTTS-Synthese abgebrochen")
            finally:
                audio_q.put(None)

        threading.Thread(target=producer, name="xtts-synth", daemon=True).start()
        while True:
            item = audio_q.get()
            if item is None:
                break
            if self._abort:
                continue  # Queue leeren, Producer beendet sich selbst
            sentence, data = item
            if on_sentence is not None:
                on_sentence(sentence)
            self._play(data, 24000, on_amplitude)

    # ------------------------------------------------------------------ API

    def _roboticize(self, data: np.ndarray, sr: int) -> np.ndarray:
        """Dezenter Roboter-Anstrich: Ringmodulation (metallischer Klang) +
        kurzer Kamm-Filter (synthetische Resonanz). Phase läuft über _robo_n
        weiter, damit gestreamte Sätze ohne Knackser ineinander übergehen."""
        amt = self.robotic
        if amt <= 0.0 or data.size == 0:
            return data
        n = np.arange(self._robo_n, self._robo_n + data.size, dtype=np.float32)
        self._robo_n += data.size
        # Ringmodulation: Träger blendet von „ganz trocken" zu „voll metallisch"
        carrier = np.sin(2 * np.pi * self.robotic_carrier_hz * n / sr).astype(np.float32)
        out = data * ((1.0 - 0.9 * amt) + 0.9 * amt * carrier)
        # Kurzer Feedforward-Kamm (~0,9 ms) → blecherne Resonanz
        d = max(1, int(sr * 0.0009))
        comb = np.empty_like(out)
        comb[:d] = out[:d]
        comb[d:] = out[d:] + 0.35 * amt * out[:-d]
        out = comb
        # Pegel zähmen, damit der Effekt nicht übersteuert
        peak = float(np.abs(out).max()) or 1.0
        if peak > 0.99:
            out = out * (0.99 / peak)
        return out.astype(np.float32)

    def say(self, text: str,
            on_amplitude: Optional[Callable[[float], None]] = None,
            on_sentence: Optional[Callable[[str], None]] = None) -> None:
        """Blockierend: synthetisiert und spielt ab; ruft on_amplitude ~20×/s.
        on_sentence(text) feuert beim Abspielen jeder Sentence (Panel-Sync)."""
        self._abort = False
        self._robo_n = 0  # Effekt-Phase pro Äußerung zurücksetzen
        try:
            if self.engine == "xtts":
                self.load_xtts()
                # GPU rechnet schneller als Echtzeit (RTF ~0,6) → volle Länge;
                # CPU schafft nur ~4× Echtzeit → hart begrenzen, Rest im Panel
                max_chars = (self.max_chars if self._active_device == "cuda"
                             else min(self.max_chars, 300))
                clean = speakable(text, max_chars)
                if clean:
                    self._say_xtts_streamed(clean, on_amplitude, on_sentence)
                return
            clean = speakable(text, self.max_chars)
            if not clean:
                return
            mp3 = asyncio.run(self._synth(clean))
            if not mp3:
                return
            data, sr = sf.read(io.BytesIO(mp3), dtype="float32")
        except Exception as exc:
            log.warning("TTS fehlgeschlagen (%s) — Antwort steht im Panel", exc)
            return
        if data.ndim > 1:
            data = data.mean(axis=1)
        # edge-tts spielt am Stück → Text erscheint, sobald die Stimme einsetzt
        if on_sentence is not None:
            on_sentence(clean)
        self._play(data, sr, on_amplitude)

    def stop(self) -> None:
        self._abort = True

    # ---------------------------------------------------------------- intern

    async def _synth(self, text: str) -> bytes:
        import edge_tts

        buf = bytearray()
        communicate = edge_tts.Communicate(text, self.voice,
                                           rate=self.rate, pitch=self.pitch)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    def _play(self, data: np.ndarray, sr: int,
              on_amplitude: Optional[Callable[[float], None]]) -> None:
        # _abort wird von say() gesetzt — hier NICHT zurücksetzen, sonst ginge
        # ein Barge-in zwischen zwei gestreamten Sätzen verloren.
        data = self._roboticize(data, sr)
        block = max(256, int(sr * 0.05))
        # Normierung für die Amplituden-Anzeige (nicht fürs Audio selbst)
        peak = float(np.percentile(np.abs(data), 99)) or 1.0

        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32",
                             device=self.device) as out:
            for i in range(0, data.size, block):
                if self._abort:
                    break
                chunk = data[i:i + block]
                if on_amplitude is not None:
                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    on_amplitude(min(1.0, rms / (peak * 0.7 + 1e-6)))
                out.write(chunk.reshape(-1, 1))
        if on_amplitude is not None:
            on_amplitude(0.0)
