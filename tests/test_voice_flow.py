"""Satzweises Sprechen gestreamter Antworten und Füllsätze in Denkpausen."""
import threading
import time

from jarvis_app.bridge import Bridge, _SentenceSpeaker
from tests.test_bridge import FakeHub, FakeWindow
from tests.tools_helpers import CFG


def test_sentence_speaker_speaks_while_streaming():
    said, first = [], []
    sp = _SentenceSpeaker(said.append, on_first=lambda: first.append(1))
    for tok in ["Es war ", "einmal ein kleiner Roboter. ", "Er lebte am Hafen", ". Jeden ", "Morgen putzte er."]:
        sp.feed(tok)
    assert said == ["Es war einmal ein kleiner Roboter.", "Er lebte am Hafen."] and first == [1]
    sp.finish()
    assert said[-1] == "Jeden Morgen putzte er."


def test_sentence_speaker_keeps_code_blocks_together():
    said = []
    sp = _SentenceSpeaker(said.append)
    for tok in ["Hier der Code. Bitte sehr: ```py\nx = 1. \ny = 2.\n", "```\nFertig, Sir."]:
        sp.feed(tok)
    sp.finish()
    assert all(s.count("```") % 2 == 0 for s in said)


class SlowChat:
    """Antwortet erst nach 4,5 s mit der ersten Satzhälfte — Füllsatz muss vorher kommen."""
    history = []

    def send(self, text, force_7b=False, model_override=None):
        yield {"type": "route", "lane": "general", "model": "g", "reason": "x",
               "heavy_requested": False, "heavy_unavailable": False}
        time.sleep(4.5)
        yield {"type": "token", "content": "Es war einmal ein mutiger Roboter am Hafen. "}
        yield {"type": "done", "tps": 1.0, "text": "…"}

    def remember_exchange(self, u, a): pass
    def context_info(self): return {"used": 0, "total": 1, "pct": 0, "messages": 0, "source": "x", "model": "g"}
    def reset(self): pass


class QuickClient:
    last_usage = None
    calls = 0
    def chat_once(self, model, messages, **kw):
        QuickClient.calls += 1
        return {"content": "Wussten Sie, Sir, dass das erste Märchenbuch der Brüder Grimm 1812 erschien? Mehr dazu gleich."}
    def ping(self): return True
    def preload(self, *a): return True


def make(tmp_path, chat):
    hub = FakeHub(); hub.connected = True
    b = Bridge(CFG, chat=chat, hub=hub, layout_path=tmp_path / "l.json")
    b._window = FakeWindow(); b._client = QuickClient()
    return b


def test_filler_bridges_long_silence_then_story(tmp_path):
    from jarvis_core.fillers import FACTS
    QuickClient.calls = 0
    b = make(tmp_path, SlowChat())
    b._run_stream("s1", "Erzähl mir eine Geschichte", False, True, True)
    spoken = b._hub.spoken
    assert any(f in spoken[0] for f in FACTS)                  # geprüfter Fakt, kein erfundener Modelltext
    assert QuickClient.calls == 0                              # kein Sprachmodell für Füllsätze
    assert spoken[-1] == "Es war einmal ein mutiger Roboter am Hafen."


def test_filler_waits_for_real_silence(tmp_path):
    b = make(tmp_path, SlowChat())
    answered = threading.Event()
    b._start_filler("x", answered)
    for _ in range(12):                                        # Jarvis spricht laufend (Ansagen) ⇒ kein Füller
        b._hub.say("Ich arbeite daran, Sir."); time.sleep(0.4)
    answered.set()
    assert all(s == "Ich arbeite daran, Sir." for s in b._hub.spoken)


def test_filler_source_rotates_and_uses_real_weather():
    from jarvis_core.fillers import FACTS, FillerSource
    f = FillerSource(weather=lambda: "Hamburg, heute: leichter Regen, 9 bis 12 Grad.", seed=1)
    first, second, third = f.next("Recherchiere etwas"), f.next("Recherchiere etwas"), f.next("x")
    assert any(x in first for x in FACTS) and "Hamburg" in second and any(x in third for x in FACTS)
    assert first != third
    g = FillerSource(weather=lambda: "Wetter", seed=1)
    g.next("Wie wird das Wetter?")
    assert "Wetter, Sir" not in g.next("Wie wird das Wetter?")   # kein Wetter-Füller bei einer Wetterfrage


def test_no_filler_when_answer_is_quick(tmp_path):
    class FastChat(SlowChat):
        def send(self, text, force_7b=False, model_override=None):
            yield {"type": "token", "content": "Gerne, Sir, hier ist eine kurze Geschichte für Sie. "}
            yield {"type": "done", "tps": 1.0, "text": "…"}
    QuickClient.calls = 0
    b = make(tmp_path, FastChat())
    b._run_stream("s1", "Erzähl mir eine Geschichte", False, True, True)
    time.sleep(3.3)
    assert QuickClient.calls == 0 and b._hub.spoken == ["Gerne, Sir, hier ist eine kurze Geschichte für Sie."]


def test_long_story_is_not_cut_for_speech():
    from voice_daemon.mouth import speakable
    from jarvis_core import config
    story = "Es war einmal ein Roboter. " * 150                  # ~4000 Zeichen
    limit = int(config.load(config.default_config_path()).voice["tts_max_chars"])
    assert "Rest zeige ich im Panel" not in speakable(story, limit)


def test_filler_not_immediately_after_a_quiet_period(tmp_path):
    b = make(tmp_path, SlowChat())
    b._last_said = 0.0                                         # Jarvis war lange still (vor der Anfrage)
    answered = threading.Event()
    b._start_filler("x", answered)
    time.sleep(2.0)
    assert b._hub.spoken == []                                  # Wartezeit zählt ab der Anfrage
    answered.set()
