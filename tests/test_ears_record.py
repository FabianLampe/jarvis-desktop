"""Aufnahme nach dem Weckwort: der Satzanfang darf nicht verloren gehen."""
import queue
import threading

import numpy as np

from voice_daemon import ears as E


def _ears(frames):
    ears = object.__new__(E.Ears)
    ears.cfg = {"beep": False, "max_utterance_s": 14.0, "silence_after_s": 0.4}
    ears._q = queue.Queue()
    ears._muted = threading.Event()
    ears.on_listen_start = lambda: None
    ears.got = []
    ears.on_audio = lambda audio, speech: ears.got.append((audio, speech))
    for f in frames:
        ears._q.put(f)
    return ears


def loud(n):
    t = np.arange(E.FRAME * n) / E.SR
    return list((4000 * np.sin(2 * np.pi * 200 * t)).astype(np.int16).reshape(n, E.FRAME))


def quiet(n):
    return [np.zeros(E.FRAME, dtype=np.int16) for _ in range(n)]


def test_speech_said_right_after_the_wake_word_is_kept():
    # „Hey Jarvis, morgen zwischen …“: die ersten Wörter liegen schon in der Queue, wenn die Aufnahme startet
    early = loud(8)
    ears = _ears(early + quiet(10))
    ears._record(lead_in_s=1.0)
    audio, speech = ears.got[-1]
    assert speech
    assert audio.size >= len(early) * E.FRAME
    assert np.abs(audio[:E.FRAME]).max() > 0.05                     # Satzanfang ist drin


def test_a_short_beep_alone_is_not_speech():
    ears = _ears(loud(2) + quiet(20))                               # Beep ≈ 2 Frames
    ears._record(lead_in_s=0.5)
    assert ears.got[-1][1] is False
