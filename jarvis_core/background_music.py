"""Streaming playback of a user-selected local file, with independent volume/ducking.

No copyrighted audio is bundled, fetched or copied. Audio dependencies load on use.
"""
from __future__ import annotations

import threading
from pathlib import Path


class BackgroundMusic:
    def __init__(self, volume: float = 0.28, duck_volume: float = 0.07,
                 output_device=None, on_change=lambda *_: None):
        self.volume = max(0.0, min(1.0, volume))
        self.duck_volume = max(0.0, min(self.volume, duck_volume))
        self.output_device = output_device
        self.on_change = on_change
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._ducked = False
        self.playing = False
        self.title = ""
        self.error = ""

    def duck(self, active: bool) -> None:
        self._ducked = bool(active)

    def snapshot(self) -> dict:
        return {"playing": self.playing, "title": self.title, "error": self.error}

    def start(self, path: Path) -> tuple[bool, str]:
        with self._lock:
            self.stop()
            if self._thread is not None and self._thread.is_alive():
                return False, "Die vorherige Wiedergabe wird noch beendet."
            self._stop = stop = threading.Event()
            ready = threading.Event()
            self.title, self.error = path.name, ""
            self._thread = threading.Thread(target=self._play, args=(path, stop, ready),
                                            name="jarvis-background-music", daemon=True)
            self._thread.start()
            if not ready.wait(4):
                stop.set()
                return False, "Audiogerät antwortet nicht."
            return not bool(self.error), self.error or self.title

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.8)

    def _play(self, path: Path, stop: threading.Event, ready: threading.Event) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            import soundfile as sf
            with sf.SoundFile(str(path)) as source:
                if source.channels not in (1, 2) or source.frames == 0:
                    raise ValueError("Bitte eine nicht leere Mono- oder Stereo-Audiodatei wählen.")
                with sd.OutputStream(samplerate=source.samplerate, channels=source.channels,
                                     dtype="float32", device=self.output_device) as output:
                    self.playing = True
                    ready.set()
                    self.on_change(self.snapshot())
                    gain = 0.0
                    # Small blocks bound memory and keep stop/duck responsive on long MP3s.
                    block = max(256, int(source.samplerate * 0.04))
                    while True:
                        samples = source.read(block, dtype="float32", always_2d=True)
                        if not len(samples):
                            break
                        target = 0.0 if stop.is_set() else self.duck_volume if self._ducked else self.volume
                        next_gain = gain + (target - gain) * 0.35
                        samples *= np.linspace(gain, next_gain, len(samples), dtype="float32")[:, None]
                        output.write(samples)
                        gain = next_gain
                        if stop.is_set() and gain < 0.002:
                            break
        except Exception as exc:
            self.error = f"Musik nicht verfügbar: {exc}"
        finally:
            self.playing = False
            ready.set()
            self.on_change(self.snapshot())
