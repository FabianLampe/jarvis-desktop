"""The soundtrack's completion signal must follow playback, not speech queueing."""
import queue
import threading
from types import SimpleNamespace

from voice_daemon.main import Daemon


class SpeakerDone(BaseException):
    """End the otherwise permanent speaker loop after a single isolated test batch."""


def speaker_with_queue():
    d = Daemon.__new__(Daemon)
    d._speech_generation = 0
    d._speech_lock = threading.Lock()
    d._speak_q = queue.Queue()
    d._standby = False
    d._follow_up_s = 0
    d._recent_spoken = []
    d._pending_speak = d._drop_speak = False
    d.ears = SimpleNamespace(suppress=lambda value: None)
    return d


def test_completion_follows_last_audio_and_does_not_consume_next_answer():
    d = speaker_with_queue()
    completed, heard = [], []
    d.link = SimpleNamespace(send_state=lambda *a: None, send_caption=lambda *a: None,
                             send_speech_finished=completed.append)
    d._speak('Begrüßung.')
    d._speak('Wetterbericht.', completion_id='wake-1')
    d._speak('Eine spätere Antwort.')

    def playback(texts, **kwargs):
        heard.extend(texts)  # Synthesis may read ahead; this must not report playback done.
        assert completed == []
        assert heard == ['Begrüßung.', 'Wetterbericht.']
        # Stop the worker after its finally block has sent the completion event.
        raise SpeakerDone()

    d.mouth = SimpleNamespace(say_stream=playback)
    try:
        d._speaker_loop()
    except SpeakerDone:
        pass
    assert completed == ['wake-1']
    assert d._speak_q.get_nowait()[0] == 'Eine spätere Antwort.'


def test_cancelling_a_queued_briefing_releases_its_soundtrack():
    d = speaker_with_queue()
    completed = []
    d.link = SimpleNamespace(send_speech_finished=completed.append)
    d.mouth = SimpleNamespace(stop=lambda: None)
    d._speak('Begrüßung.')
    d._speak('Letzter Satz.', completion_id='wake-2')
    d._on_stop()
    assert completed == ['wake-2']
    assert d._speak_q.get_nowait() == ('', False, 1, None)
