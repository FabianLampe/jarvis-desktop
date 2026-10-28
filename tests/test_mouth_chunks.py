"""Satz-Chunking für XTTS-Streaming (erster Satz allein, Rest gebündelt)."""

from voice_daemon.mouth import sentence_chunks, speakable


def test_first_sentence_is_isolated_for_fast_start():
    text = "Guten Tag, Sir. Die Systeme laufen stabil und der Reaktor ist im grünen Bereich. Sonst alles ruhig."
    chunks = sentence_chunks(text)
    assert chunks[0] == "Guten Tag, Sir."
    assert len(chunks) >= 2


def test_short_sentences_are_bundled():
    text = "Eins. Zwei. Drei. Vier. Fünf."
    chunks = sentence_chunks(text, first_max=120, rest_max=200)
    # erster Satz allein, der kurze Rest wird zu einem Block gebündelt
    assert chunks[0] == "Eins."
    assert chunks[1] == "Zwei. Drei. Vier. Fünf."


def test_single_sentence_stays_one_chunk():
    assert sentence_chunks("Nur ein Satz ohne Ende") == ["Nur ein Satz ohne Ende"]


def test_empty_text_yields_no_chunks():
    assert sentence_chunks("   ") == []


def test_speakable_strips_code_blocks():
    out = speakable("Hier ist Code:\n```python\nprint(1)\n```\nFertig, Sir.", 1200)
    assert "```" not in out and "print" not in out
    assert "Panel" in out


def test_long_first_sentence_is_split_not_truncated():
    from voice_daemon.mouth import sentence_chunks
    t = ("Es war einmal ein Leuchtturmwärter namens Elias, der auf einer kleinen Insel mitten im Meer lebte. "
         "Er war glücklich.")
    for fm, rm in ((80, 40), (120, 200), (30, 40)):
        chunks = sentence_chunks(t, first_max=fm, rest_max=rm)
        assert " ".join(chunks).split() == t.split(), (fm, chunks)     # kein Wort verloren
        assert len(chunks[0]) <= fm


def test_no_words_lost_for_any_text():
    from voice_daemon.mouth import sentence_chunks
    import random
    rnd = random.Random(3)
    words = "Sir Licht Meer Insel lebte Sturm, Boot. Hafen! Nacht? wach und hielt das".split()
    for _ in range(200):
        t = " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 60)))
        assert " ".join(sentence_chunks(t, first_max=rnd.randint(10, 120), rest_max=rnd.randint(10, 200))).split() == t.split()
