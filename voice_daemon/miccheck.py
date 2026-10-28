"""Mikrofontest:  ./start_jarvis_mac.sh --mic   (im Terminal ausführen, das Jarvis startet)

Nimmt 4 s auf und meldet den Pegel. Liegt er bei ~0, hat macOS dem Terminal den Mikrofonzugriff
nicht erlaubt (Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon)."""
import sys

import numpy as np
import sounddevice as sd


def main() -> int:
    dev = sd.query_devices(sd.default.device[0])
    print(f"Mikrofon: {dev['name']}\nJetzt 4 Sekunden normal sprechen …", flush=True)
    x = sd.rec(4 * 16000, samplerate=16000, channels=1, dtype="float32")
    sd.wait()
    x = x[:, 0]
    peak, rms = float(np.abs(x).max()), float(np.sqrt((x ** 2).mean()))
    print(f"Spitze {peak:.3f} · RMS {rms:.4f}")
    if peak < 0.02:
        print("✗ Praktisch Stille. Zugriff erlauben: Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon → "
              "Terminal (bzw. iTerm) einschalten, Terminal neu starten und nochmal testen.")
        return 1
    if rms < 0.01:
        print("△ Sehr leise: Eingangslautstärke erhöhen (Systemeinstellungen → Ton → Eingabe) oder näher sprechen.")
        return 0
    print("✓ Mikrofon liefert Sprache.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
