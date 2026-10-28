"""Stimmreferenz vorbereiten:  python -m voice_daemon.prepare_voice "Jarvis voice.mp3"

Wandelt eine Aufnahme (mp3/wav/m4a/aiff …) in die Referenz für XTTS bzw. Voicebox um:
mono, 24 kHz, Pegel normalisiert, auf die lauteste zusammenhängende Passage von höchstens
`--seconds` (Standard 15) gekürzt. Schreibt voice_daemon/assets/jarvis_reference.wav."""
from __future__ import annotations

import argparse
import sys
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf

from jarvis_core import config as core_config

TARGET_SR = 24000
PAD_S = 0.6          # Stille vor und nach der Referenz


def prepare(src: Path, dst: Path, seconds: float = 15.0) -> tuple[float, float]:
    """Gibt (Dauer_Quelle_s, Dauer_Ergebnis_s) zurück."""
    data, sr = sf.read(str(src), dtype="float32", always_2d=True)
    data = data.mean(axis=1)
    if sr != TARGET_SR:
        from scipy.signal import resample_poly
        g = gcd(TARGET_SR, sr)
        data = resample_poly(data, TARGET_SR // g, sr // g).astype(np.float32)
    total = data.size / TARGET_SR
    win = int(seconds * TARGET_SR)
    if data.size > win:                          # Fenster mit der meisten Sprachenergie wählen
        hop = TARGET_SR
        energy = [float(np.mean(data[i:i + win] ** 2)) for i in range(0, data.size - win + 1, hop)]
        start = int(np.argmax(energy)) * hop
        data = data[start:start + win]
    peak = float(np.abs(data).max())
    if peak < 1e-4:
        raise ValueError("Die Aufnahme ist praktisch stumm")
    data = data * (0.9 / peak)
    # weich ein-/ausblenden und Stille anhängen: endet die Referenz mitten im Wort, setzt Qwen
    # dieses Lautfragment (z. B. ein „n“) sonst am Anfang JEDES erzeugten Satzes fort
    fade = int(TARGET_SR * 0.06)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    data[:fade] *= ramp
    data[-fade:] *= ramp[::-1]
    pad = np.zeros(int(TARGET_SR * PAD_S), dtype=np.float32)
    data = np.concatenate([pad, data, pad])
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), data, TARGET_SR, subtype="PCM_16")
    return total, data.size / TARGET_SR - 2 * PAD_S


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "Stimmreferenz").splitlines()[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    cfg = core_config.load(core_config.default_config_path())
    dst = a.out or (core_config.repo_root() / cfg.voice["xtts_speaker_wav"])
    if not a.source.is_file():
        print(f"Datei nicht gefunden: {a.source}"); return 1
    try:
        total, kept = prepare(a.source, dst, a.seconds)
    except Exception as exc:  # noqa: BLE001
        print(f"Konnte {a.source.name} nicht lesen: {exc}"); return 1
    print(f"Referenz geschrieben: {dst}\n  Quelle {total:.1f} s → {kept:.1f} s, mono, {TARGET_SR} Hz")
    print("Dieselbe Datei kannst du in der Voicebox-App als Stimmprofil hochladen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
