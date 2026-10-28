"""Mund: TTS + Wiedergabe mit Live-Amplituden für den Orb.

Engines:
- edge  — edge-tts (deutsche Neural-Stimme, schnell, braucht Internet)
- qwen  — Qwen3-TTS (MLX) als eigener Prozess in .venv-qwen; Stimmklon aus Referenz-WAV (+Text).
          Gemessen auf M5 Pro: ~0,45× Echtzeit. Umgeht den Thread-Fehler von Voicebox 0.5.0.
- voicebox — lokaler Voicebox-Server (REST, Stimmklon z. B. Qwen3-TTS/Chatterbox). Fällt bei Ausfall
          auf edge-tts, dann auf die Systemstimme zurück.
- system — Betriebssystem-Stimme, offline (macOS: `say`). AUSWEICHPFAD: klingt NICHT wie die
          Film-Stimme; wird automatisch genutzt, wenn XTTS/edge nicht verfügbar sind.
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

from jarvis_core.platform import current as current_platform

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


def _emit_sentence(cb, text: str, seconds: float) -> None:
    """on_sentence(text) bzw. on_sentence(text, dauer_s) — Dauer nur an Rückrufe, die sie annehmen."""
    import inspect
    try:
        n = len([p for p in inspect.signature(cb).parameters.values()
                 if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
    except (TypeError, ValueError):
        n = 1
    cb(text, seconds) if n >= 2 else cb(text)


EFFECT_TYPES = ("chorus", "reverb", "delay", "compressor", "gain", "highpass", "lowpass", "pitch_shift")


def build_board(chain: list[dict]):
    """Pedalboard aus einer Effektkette im Voicebox-Format (None, wenn leer)."""
    from pedalboard import (Chorus, Compressor, Delay, Gain, HighpassFilter,  # pyright: ignore[reportPrivateImportUsage]
                            LowpassFilter, PitchShift, Reverb)
    from pedalboard import Pedalboard  # pyright: ignore[reportPrivateImportUsage] — Paket ohne __all__-Export
    makers = {"chorus": Chorus, "reverb": Reverb, "delay": Delay, "compressor": Compressor, "gain": Gain,
              "highpass": HighpassFilter, "lowpass": LowpassFilter, "pitch_shift": PitchShift}
    plugins = []
    for fx in chain or []:
        if not fx.get("enabled", True):
            continue
        cls = makers.get(str(fx.get("type")))
        if cls is None:
            log.warning("Unbekannter Effekt %r übersprungen", fx.get("type"))
            continue
        plugins.append(cls(**(fx.get("params") or {})))
    return Pedalboard(plugins) if plugins else None


def apply_effects(data: np.ndarray, sr: int, chain: list[dict], board=None) -> np.ndarray:
    """Effektkette im Voicebox-Format ([{type, enabled, params}]) mit Pedalboard — dieselbe
    Bibliothek wie Voicebox, damit der Klang 1:1 übereinstimmt."""
    """Effektkette im Voicebox-Format ([{type, enabled, params}]) mit Pedalboard — dieselbe Bibliothek
    wie Voicebox. Wird `board` übergeben, behält der Effekt seinen Zustand über mehrere Sätze hinweg
    (reset=False) — sonst knackt es an jeder Satzgrenze."""
    board = board if board is not None else build_board(chain)
    if board is None or data.size == 0:
        return data
    out = board(data.astype(np.float32).reshape(1, -1), sr, reset=False).reshape(-1)
    peak = float(np.abs(out).max())
    return (out * (0.99 / peak)).astype(np.float32) if peak > 0.99 else out.astype(np.float32)


class QwenWorker:
    """Hält den Qwen3-TTS-Prozess (.venv-qwen, MLX) am Laufen; Anfragen laufen nacheinander."""

    def __init__(self, python: str, model: str | None = None):
        self.python, self.model = python, model
        self._proc = None
        self._lock = threading.Lock()
        self._n = 0

    def _start(self) -> None:
        import json
        import os
        import subprocess
        from jarvis_core.config import repo_root
        env = dict(os.environ)
        if self.model:
            env["JARVIS_QWEN_MODEL"] = self.model
        log.info("Starte Qwen3-TTS-Worker (Modell laden dauert ~30 s) …")
        self._proc = subprocess.Popen([self.python, "-m", "voice_daemon.qwen_worker"], cwd=str(repo_root()),
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      text=True, bufsize=1, env=env)
        assert self._proc.stdout is not None and self._proc.stdin is not None
        ready = json.loads(self._proc.stdout.readline() or "{}")
        if not ready.get("ready"):
            raise RuntimeError("Qwen-Worker startete nicht (pip install in .venv-qwen? ./start_jarvis_mac.sh --qwen)")
        log.info("Qwen3-TTS bereit (%s s)", ready.get("load_s"))

    def synth(self, text: str, ref_audio: str, ref_text: str, language: str = "german"):
        import json
        import os
        import tempfile
        import soundfile as _sf
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None
            self._n += 1
            with tempfile.TemporaryDirectory(prefix="jarvis-qwen-") as tmp:
                out = os.path.join(tmp, "o.wav")
                proc.stdin.write(json.dumps({"id": self._n, "text": text, "ref_audio": ref_audio,
                                                   "ref_text": ref_text, "language": language, "out": out}) + "\n")
                proc.stdin.flush()
                reply = json.loads(proc.stdout.readline() or "{}")
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "Qwen-Worker beendet")
                data, sr = _sf.read(out, dtype="float32")
        return data, sr

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


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
        self._out = None                # durchgehender Ausgabestream der laufenden Äußerung
        self._out_sr = 24000
        self._fx_board = None           # Effektzustand über Sätze hinweg
        self.voicebox_url = "http://127.0.0.1:17493"
        self.voicebox_profile = None    # Name oder ID des Voicebox-Stimmprofils
        self.voicebox_engine = None     # optional, z. B. "qwen" / "chatterbox" (Voicebox-Engine)
        self.qwen_python = ".venv-qwen/bin/python"
        self.qwen_model = None
        self.qwen_ref_audio = ""
        self.qwen_ref_text = ""
        self.qwen_language = "german"
        self.effects_chain: list = []          # Voicebox-Format; wirkt bei qwen/xtts/edge/system
        self._qwen = None
        self._qwen_warned = False
        self.voicebox_effects = "profile"   # profile = Kette des Profils mitsenden · none = ohne Effekte
        self.voicebox_seed = None
        self._vb_effects = []
        self._vb_profile_id = None
        self._vb_warned = False
        self.system_voice = None        # z. B. "Anna" (macOS); None = Systemstandard
        self.system_rate = None

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
        elif dev == "mps" and not torch.backends.mps.is_available():
            dev = "cpu"
        log.info("Lade XTTS v2 (Stimm-Klon, %s) — das dauert einen Moment …", dev)
        self._xtts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        if dev in ("cuda", "mps"):
            try:
                self._xtts.to(dev)
            except Exception:
                log.exception("XTTS passt nicht auf %s — falle auf CPU zurück", dev)
                dev = "cpu"
                self._xtts.to("cpu")
        self._active_device = dev
        log.info("XTTS bereit (%s) — Referenz: %s", dev, self.speaker_wav)

    def _synth_xtts(self, text: str):
        self.load_xtts()
        assert self._xtts is not None
        wav = self._xtts.tts(text=text, speaker_wav=self.speaker_wav,
                             language=self.language, split_sentences=False)
        return np.asarray(wav, dtype=np.float32), 24000

    # --------------------------------------------------------- Voicebox

    # ------------------------------------------------------------- Qwen

    def _qwen_reference_text(self) -> str:
        """Exakter Text der Referenzaufnahme: aus der Config, sonst per Whisper (einmal, wird gecacht)."""
        from pathlib import Path
        if self.qwen_ref_text:
            return self.qwen_ref_text
        cache = Path(self.qwen_ref_audio).with_suffix(".txt")
        if cache.is_file():
            return cache.read_text(encoding="utf-8").strip()
        from faster_whisper import WhisperModel
        data, sr = sf.read(self.qwen_ref_audio, dtype="float32", always_2d=True)
        data = data.mean(axis=1)
        if sr != 16000:
            data = np.interp(np.linspace(0, len(data), int(len(data) * 16000 / sr)), np.arange(len(data)), data).astype("float32")
        segs, _ = WhisperModel("small", device="cpu", compute_type="int8").transcribe(data, language=self.language)
        text = " ".join(s_.text.strip() for s_ in segs).strip()
        cache.write_text(text, encoding="utf-8")
        log.info("Referenztext erkannt (bei Fehlern in qwen_ref_text korrigieren): %s", text)
        return text

    def _synth_qwen(self, text: str):
        if self._qwen is None:
            self._qwen = QwenWorker(self.qwen_python, self.qwen_model)
        return self._qwen.synth(text, self.qwen_ref_audio, self._qwen_reference_text(), self.qwen_language)

    def _say_qwen(self, text: str, on_amplitude, on_sentence) -> bool:
        clean = speakable(text, self.max_chars)
        chunks = sentence_chunks(clean, first_max=80, rest_max=40)   # kleine Happen: Rechnen und Sprechen überlappen, keine Pausen
        if not chunks:
            return True
        try:
            first = self._synth_qwen(chunks[0])
        except Exception as exc:  # noqa: BLE001
            if not self._qwen_warned:
                log.warning("Qwen3-TTS nicht nutzbar (%s) — Ausweichpfad edge-tts/Systemstimme", exc)
                self._qwen_warned = True
            return False
        self._qwen_warned = False
        self._say_xtts_streamed(clean, on_amplitude, on_sentence, synth=self._synth_qwen, chunks=chunks, first=first)
        return True

    def voicebox_profile_id(self) -> str:
        """Profilname oder -ID aus der Konfiguration → ID (Namen werden über GET /profiles aufgelöst)."""
        import requests
        if self._vb_profile_id:
            return self._vb_profile_id
        want = (self.voicebox_profile or "").strip()
        r = requests.get(self.voicebox_url + "/profiles", timeout=5)
        r.raise_for_status()
        data = r.json()
        profiles = data.get("profiles", data.get("items", [])) if isinstance(data, dict) else data
        if not profiles:
            raise RuntimeError("Voicebox hat kein Stimmprofil — in der Voicebox-App eines anlegen")
        for p in profiles:
            if want and want.lower() in (str(p.get("id", "")).lower(), str(p.get("name", "")).lower()):
                self._vb_profile_id = str(p["id"])
                self._vb_effects = p.get("effects_chain") or []     # z. B. „Robotic“-Kette aus der App
                return self._vb_profile_id
        if want:
            names = ", ".join(str(p.get("name")) for p in profiles)
            raise RuntimeError(f"Voicebox-Profil {want!r} nicht gefunden (vorhanden: {names})")
        self._vb_effects = profiles[0].get("effects_chain") or []
        self._vb_profile_id = str(profiles[0]["id"])        # kein Profil konfiguriert ⇒ erstes
        log.info("Voicebox: kein voicebox_profile gesetzt — nutze %r", profiles[0].get("name"))
        return self._vb_profile_id

    def _synth_voicebox(self, text: str):
        """POST /generate/stream → WAV (synchron). Wirft bei Fehlern; der Aufrufer fällt zurück."""
        import requests
        payload: dict = {"text": text, "profile_id": self.voicebox_profile_id(), "language": self.language}
        if self.voicebox_engine:
            payload["engine"] = self.voicebox_engine
        if self.voicebox_effects == "profile" and self._vb_effects:
            payload["effects_chain"] = self._vb_effects      # Effektkette 1:1 aus dem Voicebox-Profil
        elif self.voicebox_effects == "none":
            payload["effects_chain"] = []
        if self.voicebox_seed is not None:
            payload["seed"] = int(self.voicebox_seed)
        r = requests.post(self.voicebox_url + "/generate/stream", json=payload, timeout=180)
        if not r.ok:
            raise RuntimeError(f"Voicebox HTTP {r.status_code}: {r.text[:200]}")
        data, sr = sf.read(io.BytesIO(r.content), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data, sr

    def _say_voicebox(self, text: str, on_amplitude, on_sentence) -> bool:
        """True, wenn Voicebox gesprochen hat. False ⇒ nicht erreichbar/Fehler, Aufrufer weicht aus."""
        clean = speakable(text, self.max_chars)
        chunks = sentence_chunks(clean)
        if not chunks:
            return True
        try:
            first = self._synth_voicebox(chunks[0])         # schlägt früh fehl, wenn Voicebox fehlt
        except Exception as exc:  # noqa: BLE001
            if not self._vb_warned:
                log.warning("Voicebox nicht nutzbar (%s) — Ausweichpfad edge-tts/Systemstimme", exc)
                self._vb_warned = True
            self._vb_profile_id = None
            return False
        self._vb_warned = False
        self._say_xtts_streamed(clean, on_amplitude, on_sentence, synth=self._synth_voicebox,
                                chunks=chunks, first=first)
        return True

    def _say_xtts_streamed(self, text: str,
                           on_amplitude: Optional[Callable[[float], None]],
                           on_sentence: Optional[Callable[[str], None]] = None,
                           synth=None, chunks: Optional[list] = None,
                           first: Optional[tuple] = None) -> None:
        """Satz-Streaming: erster Satz spielt, während der Rest noch rechnet.
        on_sentence(text) feuert jeweils unmittelbar vor dem Abspielen — so
        erscheint der Text im Panel synchron zur gesprochenen Stimme."""
        chunks = chunks if chunks is not None else sentence_chunks(text)
        if not chunks:
            return
        if synth is None:
            self.load_xtts()
            synth = self._synth_xtts
        # (Satztext, Audio) — der Text wird erst beim Abspielen angezeigt
        audio_q: "queue.Queue[Optional[tuple[str, np.ndarray, int]]]" = queue.Queue(maxsize=4)

        def producer() -> None:
            try:
                if first is not None:                       # schon berechnet (Verfügbarkeitstest)
                    audio_q.put((chunks[0], *first))
                for c in (chunks[1:] if first is not None else chunks):
                    if self._abort:
                        break
                    t0 = time.monotonic()
                    data, sr = synth(c)
                    log.debug("TTS-Happen: %.1f s Audio in %.1f s gerechnet",
                              data.size / sr, time.monotonic() - t0)
                    audio_q.put((c, data, sr))
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
            sentence, data, sr = item
            if on_sentence is not None:
                _emit_sentence(on_sentence, sentence, data.size / sr)
            self._play(data, sr, on_amplitude)

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
        on_sentence(text) bzw. on_sentence(text, dauer_s) feuert beim Abspielen jeder Sentence
        (Panel-Sync). Ein Ausgabestream trägt die ganze Äußerung (kein Knacken zwischen Sätzen)."""
        self._fx_board = None
        try:
            self._say_inner(text, on_amplitude, on_sentence)
        finally:
            self._close_out()

    def _say_inner(self, text: str, on_amplitude, on_sentence) -> None:
        self._abort = False
        self._robo_n = 0  # Effekt-Phase pro Äußerung zurücksetzen
        try:
            if self.engine == "xtts":
                self.load_xtts()
                # GPU rechnet schneller als Echtzeit (RTF ~0,6) → volle Länge;
                # CPU schafft nur ~4× Echtzeit → hart begrenzen, Rest im Panel
                # Apple Silicon: CPU-Synthese gemessen RTF ~0,6 (schneller als Echtzeit)
                import sys
                max_chars = (self.max_chars if self._active_device in ("cuda", "mps") or sys.platform == "darwin"
                             else min(self.max_chars, 300))
                clean = speakable(text, max_chars)
                if clean:
                    self._say_xtts_streamed(clean, on_amplitude, on_sentence)
                return
            if self.engine == "qwen" and self._say_qwen(text, on_amplitude, on_sentence):
                return
            if self.engine == "voicebox" and self._say_voicebox(text, on_amplitude, on_sentence):
                return
            clean = speakable(text, self.max_chars)
            if not clean:
                return
            data, sr = self._synth_with_fallback(clean)
            if data is None:
                return
        except Exception as exc:
            log.warning("TTS fehlgeschlagen (%s) — Antwort steht im Panel", exc)
            return
        if data.ndim > 1:
            data = data.mean(axis=1)
        # edge-tts spielt am Stück → Text erscheint, sobald die Stimme einsetzt
        if on_sentence is not None:
            _emit_sentence(on_sentence, clean, data.size / sr)
        self._play(data, sr, on_amplitude)

    def stop(self) -> None:
        self._abort = True

    # ---------------------------------------------------------------- intern

    def _synth_with_fallback(self, clean: str):
        """edge → (offline/Fehler) → Systemstimme. Gibt (Audio, Samplerate) oder (None, 0)."""
        if self.engine in ("edge", "voicebox", "qwen"):             # voicebox ausgefallen ⇒ edge, dann Systemstimme
            try:
                mp3 = asyncio.run(self._synth(clean))
                if mp3:
                    data, sr = sf.read(io.BytesIO(mp3), dtype="float32")
                    return data, sr
            except Exception as exc:  # noqa: BLE001
                log.warning("edge-tts nicht verfügbar (%s) — Systemstimme als Ausweichpfad", exc)
        out = self._synth_system(clean)
        return out if out else (None, 0)

    def _synth_system(self, text: str):
        """Offline-Systemstimme (macOS `say`) → Audio, damit Orb-Amplituden weiter funktionieren."""
        import subprocess
        import tempfile
        from pathlib import Path
        argv0 = current_platform().speak_argv(text, voice=self.system_voice, rate=self.system_rate)
        if not argv0:
            log.warning("Keine Systemstimme auf dieser Plattform")
            return None
        with tempfile.TemporaryDirectory(prefix="jarvis-say-") as tmp:
            wav = Path(tmp) / "say.aiff"
            argv = [*argv0[:-2], "-o", str(wav), *argv0[-2:]]    # … -o datei -- text
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            if r.returncode != 0 and self.system_voice:           # Stimme nicht installiert → Standard
                log.info("Systemstimme %r nicht verfügbar — nutze Standardstimme", self.system_voice)
                argv = [a for a in argv if a not in ("-v", self.system_voice)]
                r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            if r.returncode != 0 or not wav.exists():
                log.warning("say fehlgeschlagen: %s", r.stderr.strip()[:200])
                return None
            data, sr = sf.read(str(wav), dtype="float32")
        return data, sr

    async def _synth(self, text: str) -> bytes:
        import edge_tts

        buf = bytearray()
        communicate = edge_tts.Communicate(text, self.voice,
                                           rate=self.rate, pitch=self.pitch)
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buf.extend(chunk.get("data") or b"")
        return bytes(buf)

    FADE_S = 0.008            # weiche Kanten je Satz — verhindert Knacken an den Nahtstellen

    def _open_out(self, sr: int):
        """Ein durchgehender Ausgabestream je Äußerung (kein Öffnen/Schließen pro Satz = kein Plopp).
        latency="high" gibt Reserve gegen Unterläufe, wenn andere Threads kurz rechnen."""
        if self._out is None:
            self._out_sr = sr
            self._out = sd.OutputStream(samplerate=sr, channels=1, dtype="float32",
                                        device=self.device, latency="high")
            self._out.start()
        return self._out

    def _close_out(self) -> None:
        out, self._out = self._out, None
        if out is None:
            return
        try:
            if not self._abort:
                out.write(np.zeros((int(self._out_sr * 0.12), 1), dtype="float32"))   # Ausklang ohne Abriss
            out.stop()
        except Exception:  # noqa: BLE001
            log.debug("Ausgabestream schließen", exc_info=True)
        finally:
            try:
                out.close()
            except Exception:  # noqa: BLE001
                pass

    def _play(self, data: np.ndarray, sr: int,
              on_amplitude: Optional[Callable[[float], None]]) -> None:
        # _abort wird von say() gesetzt — hier NICHT zurücksetzen, sonst ginge
        # ein Barge-in zwischen zwei gestreamten Sätzen verloren.
        data = self._roboticize(data, sr)
        if self.effects_chain and self.engine != "voicebox":
            try:
                if self._fx_board is None:
                    self._fx_board = build_board(self.effects_chain)
                data = apply_effects(data, sr, self.effects_chain, self._fx_board)
            except Exception:  # noqa: BLE001
                log.exception("Effektkette fehlgeschlagen — spiele ohne Effekt")
        if self._out is not None and sr != self._out_sr:           # alle Sätze auf eine Abtastrate bringen
            from math import gcd
            from scipy.signal import resample_poly
            g = gcd(self._out_sr, sr)
            data = resample_poly(data, self._out_sr // g, sr // g).astype(np.float32)
            sr = self._out_sr
        data = np.ascontiguousarray(data, dtype=np.float32).copy()
        fade = min(int(sr * self.FADE_S), data.size // 2)
        if fade > 1:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            data[:fade] *= ramp
            data[-fade:] *= ramp[::-1]
        block = max(256, int(sr * 0.05))
        # Normierung für die Amplituden-Anzeige (nicht fürs Audio selbst)
        peak = float(np.percentile(np.abs(data), 99)) or 1.0

        out = self._open_out(sr)
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
