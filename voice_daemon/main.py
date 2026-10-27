"""J.A.R.V.I.S. Sprach-Daemon — Einstieg.

Ablauf:  „Hey Jarvis“ (openWakeWord) → aufnehmen bis Stille → faster-whisper
(Deutsch) → Text per WebSocket an die App (gleicher Router/Verlauf wie im
Panel) → Antwort kommt zurück und wird mit edge-tts vorgelesen; die
Amplituden treiben den Orb-Kern.

Start:  python -m voice_daemon.main [--config config.yaml]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading

import numpy as np

from jarvis_core import config as core_config

from .commands import classify
from .ears import Ears
from .link import Link
from .mouth import Mouth
from .stt import Transcriber

log = logging.getLogger("daemon")

MSG_NOT_UNDERSTOOD = "Verzeihung, Sir — das habe ich akustisch nicht erfasst."
MSG_SLEEP = "Sehr wohl, Sir."
MSG_CANCEL = "Verstanden, Sir."


class Daemon:
    def __init__(self, cfg):
        v = cfg.voice
        self.link = Link(f"ws://{cfg.ws_host}:{cfg.ws_port}")
        self.stt = Transcriber(v["stt_model"], v["stt_language"], v["stt_compute"])
        speaker_wav = v.get("xtts_speaker_wav")
        if speaker_wav:
            speaker_wav = str((core_config.repo_root() / speaker_wav).resolve())
        self.mouth = Mouth(v["tts_voice"], v.get("output_device"),
                           int(v.get("tts_max_chars", 1200)),
                           rate=str(v.get("tts_rate", "+0%")),
                           pitch=str(v.get("tts_pitch", "+0Hz")),
                           engine=str(v.get("tts_engine", "edge")),
                           speaker_wav=speaker_wav,
                           language=str(v.get("stt_language", "de")),
                           xtts_device=str(v.get("xtts_device", "auto")),
                           robotic=float(v.get("robotic", 0.0)),
                           robotic_carrier_hz=float(v.get("robotic_carrier_hz", 55.0)))
        self.ears = Ears(v, on_listen_start=self._on_listen_start,
                         on_audio=self._on_audio,
                         on_interrupt=self._on_interrupt,
                         on_clap=self._on_clap)
        self._busy = threading.Lock()  # eine Äußerung/Antwort zur Zeit
        self._follow_up_s = float(v.get("follow_up_s", 0.0))
        self._pending_speak = False    # Antwort der App steht noch aus
        self._drop_speak = False       # nächste Antwort verwerfen (abgebrochen)

        self.link.on_push_to_talk = self.ears.trigger_listen
        self.link.on_speak = self._on_speak

    # -------------------------------------------------------------- Ablauf

    def _on_listen_start(self) -> None:
        self.link.send_state("listening")

    def _on_audio(self, audio: np.ndarray, speech_seen: bool) -> None:
        """Aufnahme fertig → transkribieren → an die App schicken."""
        with self._busy:
            # Auch ohne RMS-Sprachtreffer transkribieren, wenn genug Audio da
            # ist — leise Mikrofone; das letzte Wort hat Whispers VAD.
            if audio.size < int(1.2 * 16000):
                self.link.send_state("idle")
                return
            self.link.send_state("thinking")
            try:
                text = self.stt.transcribe(audio)
            except Exception:
                log.exception("STT fehlgeschlagen")
                text = ""
            if not text:
                self.link.send_state("idle")
                if speech_seen:  # gehört, aber nicht verstanden → erneut zuhören
                    self._speak(MSG_NOT_UNDERSTOOD, follow_up=True)
                return

            # Sprach-Auslöser fürs Tagesbriefing (Fallback zum Doppelklatsch)
            from .commands import is_briefing
            if is_briefing(text):
                log.info("Sprachbefehl Briefing: %r", text)
                self.link.send_briefing()
                self.link.send_state("idle")
                return

            # Daemon-Befehle: „hör auf zuzuhören" / „stop, das war nicht gemeint"
            command = classify(text)
            if command == "sleep":
                log.info("Schlaf-Befehl: %r", text)
                self._drop_pending()
                self.link.send_state("idle")
                self._speak(MSG_SLEEP, follow_up=False)  # zurück zum Wake Word
                return
            if command == "cancel":
                log.info("Abbruch-Befehl: %r", text)
                self._drop_pending()
                self.link.send_state("idle")
                self._speak(MSG_CANCEL, follow_up=True)  # gleich neu formulieren
                return

            if self.link.connected:
                self._pending_speak = True
                self.link.send_utterance(text)
                # Antwort kommt asynchron als "speak" zurück; Zustände steuert
                # ab hier der Chat-Lebenszyklus der App.
            else:
                self.link.send_state("idle")
                self._speak("Die Verbindung zur Oberfläche fehlt, Sir. "
                            "Ich höre, aber niemand antwortet.")

    def _on_interrupt(self) -> None:
        """Barge-in: Sir ruft „Hey Jarvis" in die laufende Ausgabe."""
        self.mouth.stop()  # _speak räumt auf und öffnet das Gesprächsfenster

    def _on_clap(self) -> None:
        """Doppelklatsch → Tagesbriefing in der App auslösen."""
        self.link.send_briefing()

    def _drop_pending(self) -> None:
        """Eine noch ausstehende App-Antwort soll nicht mehr vorgelesen werden."""
        if self._pending_speak:
            self._drop_speak = True

    def _on_speak(self, text: str) -> None:
        self._pending_speak = False
        if self._drop_speak:
            self._drop_speak = False
            log.info("Antwort verworfen (abgebrochen): %r", text[:60])
            return
        threading.Thread(target=self._speak, args=(text, True),
                         name="daemon-speak", daemon=True).start()

    def _speak(self, text: str, follow_up: bool = False) -> None:
        self.ears.suppress(True)  # das eigene Sprechen nicht wieder aufnehmen
        try:
            # Während der Synthese (XTTS: spürbar) zeigt der Orb „denkt";
            # der erste Amplituden-Callback schaltet auf „spricht" um.
            # on_sentence schickt jede Sentence beim Abspielen ans Panel — Text
            # und Stimme erscheinen damit gleichzeitig statt Text weit voraus.
            self.link.send_state("thinking")
            self.mouth.say(text,
                           on_amplitude=lambda a: self.link.send_state("speaking", a),
                           on_sentence=self.link.send_caption)
        finally:
            self.ears.suppress(False)
            self.link.send_state("idle")
        if follow_up and self._follow_up_s > 0:
            # Gesprächsmodus: nach der Antwort ohne neues „Hey Jarvis"
            # weiterzuhören; endet still, wenn niemand spricht.
            self.ears.trigger_listen(self._follow_up_s)

    # --------------------------------------------------------------- Start

    def run(self) -> None:
        log.info("Whisper vorwärmen …")
        self.stt.load()
        try:
            # Erst-Transkription initialisiert VAD/Tokenizer — sonst dauert
            # die erste echte Anfrage Dutzende Sekunden (Kaltstart-Befund).
            self.stt.transcribe(np.zeros(16000, dtype=np.float32))
            log.info("Whisper warmgelaufen — erste Anfrage wird flott.")
        except Exception:
            log.exception("Whisper-Warmup fehlgeschlagen (weiter ohne)")
        try:
            self.mouth.load_xtts()  # nur aktiv, wenn tts_engine = xtts
        except Exception:
            log.exception("XTTS-Laden fehlgeschlagen — falle auf edge-tts zurück")
            self.mouth.engine = "edge"
        self.ears.start()
        asyncio.run(self.link.run())


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. Sprach-Daemon")
    parser.add_argument("--config", default=str(core_config.default_config_path()))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.WARNING)

    cfg = core_config.load(args.config)
    Daemon(cfg).run()


if __name__ == "__main__":
    main()
