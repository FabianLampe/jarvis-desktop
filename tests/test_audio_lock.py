"""PortAudio-Streams werden nie parallel geöffnet und gestoppt (CoreAudio-Deadlock beim Stummschalten)."""
import threading
import time

from voice_daemon import audio_lock


class SlowStream:
    """Meldet, wenn sich zwei PortAudio-Aufrufe zeitlich überlappen."""
    active = 0
    overlaps = 0
    guard = threading.Lock()

    def _op(self):
        with SlowStream.guard:
            SlowStream.active += 1
            if SlowStream.active > 1:
                SlowStream.overlaps += 1
        time.sleep(0.02)
        with SlowStream.guard:
            SlowStream.active -= 1

    start = stop = close = _op


def test_open_and_close_never_overlap():
    SlowStream.overlaps = 0

    def worker():
        for _ in range(5):
            with audio_lock.opened_stream(SlowStream):
                pass

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert SlowStream.overlaps == 0


def test_failed_start_closes_stream():
    closed = []

    class Broken:
        def start(self):
            raise RuntimeError("kein Gerät")

        def stop(self):
            pass

        def close(self):
            closed.append(True)

    try:
        audio_lock.start_stream(Broken)
    except RuntimeError:
        pass
    assert closed == [True]
