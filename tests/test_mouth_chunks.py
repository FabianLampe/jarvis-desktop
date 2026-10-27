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
