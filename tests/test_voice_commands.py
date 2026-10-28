"""Sprachbefehl-Klassifikation des Daemons (sleep/cancel/normal)."""

from voice_daemon.commands import classify, is_briefing


def test_is_briefing():
    assert is_briefing("Briefing") is True
    assert is_briefing("gib mir das tagesbriefing") is True
    assert is_briefing("was steht heute an") is True
    assert is_briefing("Wie ist der Status?") is False


def test_sleep_commands():
    for text in ("Jarvis, hör auf zuzuhören.",
                 "Hör auf zu zuhören!",
                 "Hey Jarvis, schlaf weiter.",
                 "Das war alles, danke.",
                 "Okay, das war's.",
                 "Ende.",
                 "Ruhe!",
                 "Gespräch beenden, bitte."):
        assert classify(text) == "sleep", text


def test_cancel_commands():
    for text in ("Jarvis, stop!",
                 "Stopp.",
                 "Stopp, das war so nicht gemeint.",
                 "Vergiss es.",
                 "Schon gut, Jarvis.",
                 "Abbrechen!",
                 "Nee, egal."):
        assert classify(text) == "cancel", text


def test_normal_utterances_pass_through():
    for text in ("Wie stoppe ich einen Dienst in Windows?",
                 "Erzähl mir etwas über den Stopptanz in den Achtzigern.",
                 "Ende gut, alles gut — kennst du das Stück?",
                 "Wie wird das Wetter morgen früh in Hamburg?",
                 "Was bedeutet Ruhe vor dem Sturm eigentlich genau?",
                 ""):
        assert classify(text) is None, text


def test_wake_word_prefix_is_stripped():
    assert classify("Hey Jarvis: Stop") == "cancel"
    assert classify("Jarvis hör auf") == "sleep"


def test_resolve_engine_never_leaves_jarvis_mute(tmp_path):
    from voice_daemon.main import resolve_engine
    ref = tmp_path / "ref.wav"
    assert resolve_engine("xtts", str(ref)) == "edge"          # Referenz fehlt ⇒ Ausweichen
    assert resolve_engine("auto", None) == "edge"
    assert resolve_engine("voicebox", None) == "voicebox" and resolve_engine("system", None) == "system"
    ref.write_bytes(b"x")
    assert resolve_engine("xtts", str(ref)) in ("xtts", "edge")  # xtts nur mit installiertem Paket
