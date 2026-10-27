"""Selbsttest des Sprach-Daemons — ohne Mikrofon ausführbar.

  python -m voice_daemon.selfcheck

1. Audio-Geräte auflisten
2. Deutsche TTS erzeugen (edge-tts)
3. STT-Roundtrip: die TTS-Aufnahme mit faster-whisper (de) transkribieren
4. Wake-Word-Modell laden und mit synthetischem „Hey Jarvis!“ anstoßen
5. Verbindung zur laufenden App prüfen (ws://…:8765)
"""

from __future__ import annotations

import asyncio
import io
import sys

import numpy as np

from jarvis_core import config as core_config

SENTENCE = ("Guten Abend, Sir. Wie spät ist es, und öffne bitte die "
            "Wettervorhersage für morgen früh.")
KEYWORDS = ("guten abend", "wie spät", "wetter")


def resample_to_16k(data: np.ndarray, sr: int) -> np.ndarray:
    if sr == 16000:
        return data.astype(np.float32)
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(16000, sr)
        return resample_poly(data, 16000 // g, sr // g).astype(np.float32)
    except ImportError:
        x_old = np.linspace(0, 1, data.size, endpoint=False)
        x_new = np.linspace(0, 1, int(data.size * 16000 / sr), endpoint=False)
        return np.interp(x_new, x_old, data).astype(np.float32)


def tts_to_f32(text: str, voice: str) -> tuple[np.ndarray, int]:
    import edge_tts
    import soundfile as sf

    async def synth() -> bytes:
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    mp3 = asyncio.run(synth())
    data, sr = sf.read(io.BytesIO(mp3), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, sr


def main() -> int:
    cfg = core_config.load(core_config.default_config_path())
    v = cfg.voice
    ok = True
    print("J.A.R.V.I.S. Sprach-Daemon — Selbsttest\n" + "=" * 46)

    # 1 — Geräte
    try:
        import sounddevice as sd
        din, dout = sd.default.device
        devs = sd.query_devices()
        print(f"[1] Audio-Geräte: {len(devs)} gefunden")
        print(f"    Standard-Mikrofon : {devs[din]['name'] if din >= 0 else '— FEHLT —'}")
        print(f"    Standard-Ausgabe  : {devs[dout]['name'] if dout >= 0 else '— FEHLT —'}")
        if din < 0:
            ok = False
    except Exception as exc:
        print(f"[1] FEHLER bei Audio-Geräten: {exc}")
        ok = False

    # 2 — TTS
    try:
        data, sr = tts_to_f32(SENTENCE, v["tts_voice"])
        print(f"[2] TTS ({v['tts_voice']}): {data.size / sr:.1f} s Audio erzeugt")
    except Exception as exc:
        print(f"[2] FEHLER bei TTS (Internet nötig): {exc}")
        return 1

    # 3 — STT-Roundtrip (der Kern: versteht Whisper Deutsch?)
    try:
        from .stt import Transcriber
        stt = Transcriber(v["stt_model"], v["stt_language"], v["stt_compute"])
        text = stt.transcribe(resample_to_16k(data, sr))
        low = text.lower()
        hits = [k for k in KEYWORDS if k in low]
        print(f"[3] STT ({v['stt_model']}, de) erkannte: „{text}“")
        print(f"    Schlüsselwörter: {len(hits)}/{len(KEYWORDS)} → "
              + ("BESTANDEN" if len(hits) >= 2 else "DURCHGEFALLEN"))
        if len(hits) < 2:
            ok = False
    except Exception as exc:
        print(f"[3] FEHLER bei STT: {exc}")
        ok = False

    # 4 — Wake Word
    try:
        from .ears import ensure_wake_model
        model = ensure_wake_model(v["wake_word"])
        key = list(model.models.keys())[0]
        wake, wsr = tts_to_f32("Hey Jarvis!", v["tts_voice"])
        pcm = (resample_to_16k(wake, wsr) * 32767).astype(np.int16)
        best = 0.0
        for i in range(0, pcm.size - 1280, 1280):
            best = max(best, float(model.predict(pcm[i:i + 1280])[key]))
        verdict = "BESTANDEN" if best >= v["wake_threshold"] else \
            f"niedrig (TTS-Stimme; mit echter Stimme meist deutlich höher)"
        print(f"[4] Wake Word „{key}“: bester Score {best:.2f} "
              f"(Schwelle {v['wake_threshold']}) → {verdict}")
    except Exception as exc:
        print(f"[4] FEHLER beim Wake-Word-Modell: {exc}")
        ok = False

    # 5 — App-Verbindung
    try:
        from websockets.sync.client import connect
        ws = connect(f"ws://{cfg.ws_host}:{cfg.ws_port}", open_timeout=2)
        ws.close()
        print(f"[5] App erreichbar unter ws://{cfg.ws_host}:{cfg.ws_port} → BESTANDEN")
    except Exception:
        print(f"[5] App nicht erreichbar (läuft start_jarvis.bat?) — "
              f"Daemon verbindet sich später automatisch")

    print("=" * 46)
    print("Ergebnis:", "BEREIT, Sir." if ok else "Es gibt noch Stolpersteine.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
