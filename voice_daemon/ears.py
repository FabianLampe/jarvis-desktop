"""Ohren: Mikrofonstrom, Wake-Word-Erkennung (openWakeWord), Aufnahme mit Stille-Erkennung.

Designentscheidung: Wake Word über das vortrainierte openWakeWord-Modell
„hey_jarvis" statt des alten Whisper-Schnipsel-Vergleichs — der scheiterte
an nicht-muttersprachlichem Englisch. openWakeWord ist auf viele Stimmen/
Akzente trainiert und läuft mit Mini-Last auf der CPU.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

log = logging.getLogger("daemon.ears")

SR = 16000          # openWakeWord + Whisper erwarten 16 kHz
FRAME = 1280        # 80 ms — openWakeWord-Schrittweite


def ensure_wake_model(name: str):
    """Lädt das openWakeWord-Modell (einmaliger Download der Basismodelle)."""
    import openwakeword
    from openwakeword.model import Model

    try:
        return Model(wakeword_models=[name], inference_framework="onnx")
    except Exception:
        log.info("Lade openWakeWord-Modelle herunter (einmalig) …")
        openwakeword.utils.download_models()
        return Model(wakeword_models=[name], inference_framework="onnx")


class Ears:
    """Eine Mikrofon-Schleife mit zwei Modi: Wake-Scan und Aufnahme."""

    def __init__(self, voice_cfg: dict,
                 on_listen_start: Callable[[], None],
                 on_audio: Callable[[np.ndarray, bool], None],
                 on_interrupt: Callable[[], None] = lambda: None,
                 on_clap: Callable[[], None] = lambda: None):
        """on_audio(audio_f32_16k, speech_detected) — nach jeder Aufnahme.
        on_interrupt() — Wake Word fiel, während Jarvis selbst sprach (Barge-in).
        on_clap() — Doppelklatsch erkannt (Tagesbriefing).
        """
        self.cfg = voice_cfg
        self.on_listen_start = on_listen_start
        self.on_audio = on_audio
        self.on_interrupt = on_interrupt
        self.on_clap = on_clap

        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)
        self._listen_now = threading.Event()   # Push-to-talk / Wake ausgelöst
        self._suppress = threading.Event()     # an, während Jarvis selbst spricht
        self._stop = threading.Event()
        self._model = None
        self._model_key: Optional[str] = None
        self._next_lead_in = 5.0               # Wartezeit auf Sprachbeginn (s)
        self._clap = None
        if voice_cfg.get("clap_enabled", True):
            from .clap import ClapDetector
            self._clap = ClapDetector(
                threshold=float(voice_cfg.get("clap_threshold", 4.0)),
                gap_min_s=float(voice_cfg.get("clap_gap_min_s", 0.15)),
                gap_max_s=float(voice_cfg.get("clap_gap_max_s", 0.8)),
                cooldown_s=float(voice_cfg.get("clap_cooldown_s", 3.0)))

    # ------------------------------------------------------------------ API

    def start(self) -> None:
        self._model = ensure_wake_model(self.cfg["wake_word"])
        # openWakeWord indiziert Scores über den Modellnamen
        self._model_key = list(self._model.models.keys())[0]
        log.info("Wake-Word-Modell aktiv: %s (Schwelle %.2f)",
                 self._model_key, self.cfg["wake_threshold"])
        threading.Thread(target=self._loop, name="ears", daemon=True).start()

    def trigger_listen(self, lead_in_s: float = 5.0) -> None:
        """Sofort zuhören, ohne Wake Word (Push-to-talk / Gesprächsmodus).

        lead_in_s: wie lange auf Sprachbeginn gewartet wird, bevor still
        abgebrochen wird (Gesprächsfenster).
        """
        self._next_lead_in = max(1.0, float(lead_in_s))
        self._listen_now.set()

    def suppress(self, on: bool) -> None:
        """Während der TTS-Ausgabe nicht das eigene Sprechen aufnehmen."""
        if on:
            self._suppress.set()
        else:
            self._suppress.clear()

    def stop(self) -> None:
        self._stop.set()

    # ---------------------------------------------------------------- intern

    def _audio_cb(self, indata, frames, t, status) -> None:
        if status:
            log.debug("Audio-Status: %s", status)
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # lieber Frames verlieren als blockieren

    def _loop(self) -> None:
        device = self.cfg.get("input_device")
        threshold = float(self.cfg["wake_threshold"])
        # Beim Vorlesen ist der Störabstand schlechter (Lautsprecher-Übersprechen)
        # — Barge-in darf deshalb etwas empfindlicher sein.
        interrupt_threshold = float(self.cfg.get("interrupt_threshold", 0.5))
        cooldown_until = 0.0

        with sd.InputStream(samplerate=SR, channels=1, dtype="int16",
                            blocksize=FRAME, device=device,
                            callback=self._audio_cb):
            log.info("Mikrofon offen — sage „Hey Jarvis“ …")
            while not self._stop.is_set():
                try:
                    frame = self._q.get(timeout=0.5)
                except queue.Empty:
                    continue

                # Doppelklatsch (nicht während Jarvis spricht) → Tagesbriefing
                if self._clap is not None and not self._suppress.is_set():
                    if self._clap.process(frame):
                        log.info("Doppelklatsch erkannt — Briefing")
                        self.on_clap()
                        continue

                if self._suppress.is_set():
                    # Jarvis spricht: weiter auf „Hey Jarvis“ lauschen,
                    # damit Sir ihn unterbrechen kann.
                    self._listen_now.clear()
                    score = float(self._model.predict(frame)[self._model_key])
                    if score >= interrupt_threshold and time.monotonic() >= cooldown_until:
                        log.info("Barge-in erkannt (Score %.2f) — Ausgabe stoppt", score)
                        self._model.reset()
                        cooldown_until = time.monotonic() + 1.5
                        self.on_interrupt()
                    continue

                if self._listen_now.is_set():
                    self._listen_now.clear()
                    self._record(self._next_lead_in)
                    self._next_lead_in = 5.0
                    self._model.reset()  # alte Scores verwerfen
                    cooldown_until = time.monotonic() + 1.0
                    continue

                score = float(self._model.predict(frame)[self._model_key])
                if score >= threshold and time.monotonic() >= cooldown_until:
                    log.info("Wake Word erkannt (Score %.2f)", score)
                    self._record(5.0)
                    self._model.reset()
                    cooldown_until = time.monotonic() + 1.0

    def _record(self, lead_in_s: float) -> None:
        """Nimmt eine Äußerung auf: Start sofort, Ende nach Stille oder Limit."""
        self.on_listen_start()
        if self.cfg.get("beep", True):
            self._beep()
        self._drain()

        max_frames = int(float(self.cfg["max_utterance_s"]) * SR / FRAME)
        silence_frames = int(float(self.cfg["silence_after_s"]) * SR / FRAME)
        lead_in_frames = int(lead_in_s * SR / FRAME)  # Wartezeit auf Sprachbeginn

        chunks: list[np.ndarray] = []
        noise = 60.0          # adaptiver Grundpegel (int16-RMS)
        peak = 0.0
        speech_seen = False
        quiet_run = 0

        for i in range(max_frames):
            try:
                frame = self._q.get(timeout=1.0)
            except queue.Empty:
                break
            chunks.append(frame)
            rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
            peak = max(peak, rms)

            # Schwellen bewusst niedrig (leise Mikrofone); die Feinarbeit
            # übernimmt Whispers VAD — RMS steuert nur das Aufnahme-Ende.
            if not speech_seen:
                noise = 0.95 * noise + 0.05 * rms
                if rms > max(120.0, noise * 3.0):
                    speech_seen = True
                    quiet_run = 0
                elif i > lead_in_frames:
                    break  # niemand spricht
            else:
                if rms < max(90.0, noise * 2.2):
                    quiet_run += 1
                    if quiet_run >= silence_frames:
                        break
                else:
                    quiet_run = 0

        audio = (np.concatenate(chunks).astype(np.float32) / 32768.0
                 if chunks else np.zeros(0, dtype=np.float32))
        log.debug("Aufnahme: %.1f s, Spitzen-RMS %.0f, Sprache=%s",
                  audio.size / SR, peak, speech_seen)
        self.on_audio(audio, speech_seen)

    def _drain(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def _beep(self) -> None:
        try:
            t = np.linspace(0, 0.09, int(0.09 * 24000), endpoint=False)
            tone = (0.18 * np.sin(2 * np.pi * 880 * t) *
                    np.hanning(t.size)).astype(np.float32)
            sd.play(tone, samplerate=24000,
                    device=self.cfg.get("output_device"), blocking=False)
        except Exception as exc:
            log.debug("Beep fehlgeschlagen: %s", exc)
