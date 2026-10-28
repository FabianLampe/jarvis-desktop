"""Qwen3-TTS-Worker (MLX) — läuft in .venv-qwen als eigener Prozess.

Protokoll: eine JSON-Zeile pro Anfrage auf stdin, eine JSON-Zeile pro Antwort auf stdout:
  {"id":1,"text":"…","ref_audio":"x.wav","ref_text":"…","language":"german","out":"/tmp/x.wav"}
  → {"id":1,"ok":true,"wav":"/tmp/x.wav","seconds":3.2} | {"id":1,"ok":false,"error":"…"}
Alle MLX-Aufrufe laufen im Hauptthread dieses Prozesses (MLX-Streams sind threadgebunden —
Voicebox 0.5.0 scheitert genau daran).  Start:  .venv-qwen/bin/python -m voice_daemon.qwen_worker"""
from __future__ import annotations

import json
import os
import sys
import time

MODEL = os.environ.get("JARVIS_QWEN_MODEL", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16")


def main() -> None:
    real_stdout = sys.stdout
    sys.stdout = sys.stderr                     # Bibliotheks-Ausgaben dürfen das Protokoll nicht stören

    def reply(obj: dict) -> None:
        real_stdout.write(json.dumps(obj) + "\n"); real_stdout.flush()

    import numpy as np
    import soundfile as sf
    from mlx_audio.tts.utils import load_model  # pyright: ignore[reportMissingImports] — läuft in .venv-qwen

    t0 = time.time()
    model = load_model(MODEL)
    sr = getattr(model, "sample_rate", 24000)
    reply({"ready": True, "model": MODEL, "load_s": round(time.time() - t0, 1), "sample_rate": sr})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        try:
            t = time.time()
            parts = []
            for r in model.generate(text=req["text"], ref_audio=req["ref_audio"], ref_text=req["ref_text"],
                                    lang_code=req.get("language", "german")):
                parts.append(np.asarray(r.audio, dtype="float32").reshape(-1))
            if not parts:
                raise RuntimeError("Qwen lieferte kein Audio")
            audio = np.concatenate(parts)
            sf.write(req["out"], audio, sr, subtype="PCM_16")
            reply({"id": req.get("id"), "ok": True, "wav": req["out"], "seconds": round(len(audio) / sr, 2),
                   "took_s": round(time.time() - t, 1)})
        except Exception as exc:  # noqa: BLE001
            reply({"id": req.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]})


if __name__ == "__main__":
    main()
