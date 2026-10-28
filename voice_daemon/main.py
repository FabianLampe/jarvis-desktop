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
import queue
import threading
import time

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


def resolve_engine(engine: str, speaker_wav: str | None) -> str:
    """auto ⇒ xtts, wenn Referenzaufnahme UND XTTS-Pakete vorhanden sind, sonst edge.
    Ein ausdrücklich gewähltes xtts ohne Referenz fällt mit Warnung auf edge zurück
    (sonst bliebe Jarvis stumm). Zusätzlich greift zur Laufzeit die Kette → edge → system."""
    import importlib.util
    import os
    have_ref = bool(speaker_wav) and os.path.isfile(speaker_wav)
    have_pkg = importlib.util.find_spec("TTS") is not None
    if engine == "auto":
        return "xtts" if have_ref and have_pkg else "edge"
    if engine == "qwen":
        from pathlib import Path
        from jarvis_core.config import repo_root
        py = repo_root() / ".venv-qwen" / "bin" / "python"
        if not (have_ref and py.exists()):
            log.warning("tts_engine qwen, aber %s — nutze edge-tts. (./start_jarvis_mac.sh --qwen / --voice-ref)",
                        "Referenzaufnahme fehlt: " + str(speaker_wav) if not have_ref else ".venv-qwen fehlt")
            return "edge"
        return "qwen"
    if engine == "xtts" and not (have_ref and have_pkg):
        log.warning("tts_engine xtts, aber %s — nutze edge-tts. (./start_jarvis_mac.sh --voice-ref/--xtts)",
                    "Referenzaufnahme fehlt: " + str(speaker_wav) if not have_ref else "coqui-tts nicht installiert")
        return "edge"
    return engine


class Daemon:
    def __init__(self, cfg):
        v = cfg.voice
        self.link = Link(f"ws://{cfg.ws_host}:{cfg.ws_port}")
        self.stt = Transcriber(v["stt_model"], v["stt_language"], v["stt_compute"], int(v.get("stt_threads", 0)))
        speaker_wav = v.get("xtts_speaker_wav")
        if speaker_wav:
            speaker_wav = str((core_config.repo_root() / speaker_wav).resolve())
        engine = resolve_engine(str(v.get("tts_engine", "edge")), speaker_wav)
        self.mouth = Mouth(v["tts_voice"], v.get("output_device"),
                           int(v.get("tts_max_chars", 1200)),
                           rate=str(v.get("tts_rate", "+0%")),
                           pitch=str(v.get("tts_pitch", "+0Hz")),
                           engine=engine,
                           speaker_wav=speaker_wav,
                           language=str(v.get("stt_language", "de")),
                           xtts_device=str(v.get("xtts_device", "auto")),
                           robotic=float(v.get("robotic", 0.0)),
                           robotic_carrier_hz=float(v.get("robotic_carrier_hz", 55.0)))
        from pathlib import Path
        root = core_config.repo_root()
        self.mouth.qwen_python = str((root / v.get("qwen_python", ".venv-qwen/bin/python")))
        self.mouth.qwen_model = v.get("qwen_model") or None
        self.mouth.qwen_ref_audio = str((root / (v.get("qwen_ref_audio") or v.get("xtts_speaker_wav") or "")).resolve())
        self.mouth.qwen_ref_text = str(v.get("qwen_ref_text") or "")
        self.mouth.qwen_language = str(v.get("qwen_language", "german"))
        self.mouth.effects_chain = list(v.get("effects_chain") or [])
        self.mouth.voicebox_url = str(v.get("voicebox_url", "http://127.0.0.1:17493")).rstrip("/")
        self.mouth.voicebox_profile = v.get("voicebox_profile") or None
        self.mouth.voicebox_effects = str(v.get("voicebox_effects", "profile"))
        self.mouth.voicebox_seed = v.get("voicebox_seed")
        self.mouth.voicebox_engine = v.get("voicebox_engine") or None
        self.mouth.system_voice = v.get("system_voice") or None
        self.mouth.system_rate = v.get("system_rate") or None
        self.ears = Ears(v, on_listen_start=self._on_listen_start,
                         on_audio=self._on_audio,
                         on_interrupt=self._on_interrupt,
                         on_clap=self._on_clap)
        self._busy = threading.Lock()  # eine Äußerung/Antwort zur Zeit
        self._follow_up_s = float(v.get("follow_up_s", 0.0))
        self._pending_speak = False    # Antwort der App steht noch aus
        self._drop_speak = False       # nächste Antwort verwerfen (abgebrochen)
        self._speak_q: "queue.Queue[tuple[str, bool]]" = queue.Queue()
        self._recent_spoken: list[tuple[float, str]] = []   # (Zeit, Text) gegen Selbst-Echo
        threading.Thread(target=self._speaker_loop, name="daemon-speaker", daemon=True).start()

        self.link.on_push_to_talk = self.ears.trigger_listen
        self.link.on_speak = self._on_speak
        self.link.on_stop = self.mouth.stop
        self.link.on_mute = self._on_mute

    # -------------------------------------------------------------- Ablauf

    def _on_listen_start(self) -> None:
        self.link.send_state("listening")

    def _on_audio(self, audio: np.ndarray, speech_seen: bool) -> None:
        """Aufnahme fertig → transkribieren → an die App schicken."""
        if self.ears.muted:                     # kurz vor dem Stummschalten aufgenommen → verwerfen
            self.link.send_state("idle")
            return
        with self._busy:
            # Auch ohne RMS-Sprachtreffer transkribieren, wenn genug Audio da
            # ist — leise Mikrofone; das letzte Wort hat Whispers VAD.
            if audio.size == 0:                 # Aufnahme abgebrochen (z. B. Stumm)
                self.link.send_state("idle")
                return
            if audio.size < int(0.6 * 16000):
                log.info("Aufnahme zu kurz (%.1f s) — verworfen. Nach „Hey Jarvis“ und dem Signalton sprechen.",
                         audio.size / 16000)
                self.link.send_state("idle")
                return
            self.link.send_state("thinking")
            try:
                text = self.stt.transcribe(audio)
            except Exception:
                log.exception("STT fehlgeschlagen")
                text = ""
            if text and self._is_echo(text):
                log.info("Eigene Stimme erkannt (Lautsprecher → Mikrofon) — verworfen: %r", text[:70])
                self.link.send_state("idle")
                return
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

    def _on_mute(self, muted: bool) -> None:
        self.ears.set_muted(muted)
        if muted:
            while True:                          # anstehende Ausgaben verwerfen
                try:
                    self._speak_q.get_nowait()
                except queue.Empty:
                    break
            self.mouth.stop()               # laufende Ausgabe beenden
            self.link.send_state("idle")
        self.link.send_muted(muted)

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
        self._speak(text, True)

    def _is_echo(self, heard: str) -> bool:
        from .commands import is_own_echo
        now = time.monotonic()
        # nur Ausgaben, die gerade erst endeten: Echo kommt sofort, nicht eine halbe Minute später
        self._recent_spoken = [(t, x) for t, x in self._recent_spoken if now - t < 12.0]
        return is_own_echo(heard, [x for _, x in self._recent_spoken])

    def _speak(self, text: str, follow_up: bool = False) -> None:
        """Reiht eine Ausgabe ein. Ein einziger Sprecher-Thread spielt sie nacheinander ab —
        das Mikrofon bleibt über die ganze Folge (z. B. Briefing-Zeilen) stummgeschaltet."""
        self._speak_q.put((text, follow_up))

    STREAM_WAIT_S = 1.5      # so lange auf den nächsten Satz einer laufenden Antwort warten

    def _speaker_loop(self) -> None:
        while True:
            text, follow_up = self._speak_q.get()
            self.ears.suppress(True)                # das eigene Sprechen nicht wieder aufnehmen
            state = {"follow_up": follow_up}

            def texts(first=text):
                """Erster Text, dann alles, was während des Sprechens nachkommt (Satz für Satz
                gestreamte Antworten, Briefing-Zeilen) — ein durchgehender Fluss ohne Pausen."""
                self._recent_spoken.append((time.monotonic(), first))
                yield first
                while True:
                    try:
                        nxt, fu = self._speak_q.get(timeout=self.STREAM_WAIT_S)
                    except queue.Empty:
                        return
                    state["follow_up"] = fu
                    self._recent_spoken.append((time.monotonic(), nxt))
                    yield nxt
            try:
                self.link.send_state("thinking")
                try:
                    self.mouth.say_stream(texts(),
                                          on_amplitude=lambda a: self.link.send_state("speaking", a),
                                          on_sentence=self.link.send_caption)
                except Exception:
                    log.exception("Sprachausgabe fehlgeschlagen")
                self._recent_spoken.append((time.monotonic(), text))
                want_follow_up = state["follow_up"]
            finally:
                self.ears.suppress(False)           # startet den Nachlauf (Echo-Ausklang)
                self.link.send_state("idle")
            if want_follow_up and self._follow_up_s > 0:
                time.sleep(self.ears._echo_tail_s)  # Gesprächsfenster erst nach dem Nachlauf öffnen
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
            log.exception("XTTS-Laden fehlgeschlagen — falle auf edge-tts zurück "
                          "(klingt anders als die Film-Stimme; offline: tts_engine: system)")
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
    from jarvis_core.platform import current
    logdir = current().data_dir() / "logs"; logdir.mkdir(exist_ok=True)
    fh = logging.FileHandler(logdir / "daemon.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(fh)

    from jarvis_core import singleton
    singleton.require("voice_daemon", "Der Sprach-Daemon")
    core_config.load_env_file()
    cfg = core_config.load(args.config)
    Daemon(cfg).run()


if __name__ == "__main__":
    main()
