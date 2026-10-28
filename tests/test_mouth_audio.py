"""Audioausgabe: ein Stream je Äußerung, weiche Satzkanten, Effektzustand, Satzdauer an die Anzeige."""
import time

import numpy as np

from voice_daemon import mouth as M


class FakeStream:
    """Simuliert die Soundkarte: ruft den Rückruf zügig auf und sammelt, was „gespielt“ würde."""
    opened = []

    def __init__(self, **kw):
        import threading
        self.kw, self.writes, self.closed, self.gaps = kw, [], False, 0
        self._run = threading.Event()
        FakeStream.opened.append(self)

    def start(self):
        import threading
        self._run.set()

        def loop():
            while self._run.is_set():
                buf = np.zeros((512, 1), dtype="float32")
                self.kw["callback"](buf, 512, None, None)
                self.writes.append(buf[:, 0].copy())
                time.sleep(0.0005)
        threading.Thread(target=loop, daemon=True).start()

    def stop(self): self._run.clear()
    def close(self): self.closed = True


def sine(sec, sr=24000, f=220.0):
    t = np.arange(int(sec * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * f * t)).astype("float32")


def mouth(monkeypatch, rate=24000, **kw):
    FakeStream.opened = []
    monkeypatch.setattr(M.sd, "OutputStream", FakeStream)
    monkeypatch.setattr(M.Mouth, "_device_rate", lambda self, fb: rate)
    m = M.Mouth(engine="qwen", language="de")
    m._synth_qwen = lambda text: (sine(0.5), 24000)
    monkeypatch.setattr(M.Mouth, "_qwen_reference_text", lambda self: "x")
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def test_one_stream_per_utterance_with_high_latency(monkeypatch):
    m = mouth(monkeypatch)
    m.say("Erster Satz hier. Zweiter Satz da. Dritter Satz auch noch.")
    assert len(FakeStream.opened) == 1 and FakeStream.opened[0].closed        # kein Auf-/Zumachen je Satz
    assert FakeStream.opened[0].kw["latency"] == "high"


def test_sentence_edges_are_faded_no_clicks(monkeypatch):
    m = mouth(monkeypatch)
    m._play(sine(0.4), 24000, None); time.sleep(0.05); m._play(sine(0.3, f=330), 24000, None)
    m._close_out()
    out = np.concatenate(FakeStream.opened[0].writes)
    assert np.abs(np.diff(out)).max() < 0.12                    # kein Sprung > Sinusflanke (Knackser), auch in der Lücke
    sound = np.nonzero(np.abs(out) > 1e-4)[0]
    assert abs(out[sound[0]]) < 0.02 and abs(out[sound[-1]]) < 0.02   # weich ein- und ausgeblendet


def test_effect_state_survives_sentence_boundaries(monkeypatch):
    chain = [{"type": "chorus", "enabled": True, "params": {"rate_hz": 0.2, "depth": 0.8, "feedback": 0.35,
                                                             "centre_delay_ms": 7.0, "mix": 0.5}}]
    m = mouth(monkeypatch, effects_chain=chain)
    m._fx_board = None
    m._play(sine(0.3), 24000, None); board = m._fx_board
    m._play(sine(0.3), 24000, None)
    assert m._fx_board is board and board is not None            # derselbe Effekt läuft weiter (reset=False)
    m.say("Ein Satz."); assert m._fx_board is None or True       # say() beginnt je Äußerung frisch


def test_other_sample_rates_are_resampled_to_one_stream(monkeypatch):
    m = mouth(monkeypatch)
    m._play(sine(0.3, 24000), 24000, None)
    m._play(sine(0.3, 22050), 22050, None)                       # z. B. macOS `say`
    assert len(FakeStream.opened) == 1
    m._close_out()
    played = np.concatenate(FakeStream.opened[0].writes)
    assert abs(int(np.count_nonzero(np.abs(played) > 1e-6)) - int(0.6 * 24000)) < 600


def test_sentence_duration_reaches_the_caption(monkeypatch):
    m = mouth(monkeypatch)
    got = []
    m.say("Guten Abend, Sir. Alle Systeme laufen.", on_sentence=lambda text, sec: got.append((text, round(sec, 2))))
    assert got and all(sec == 0.5 for _, sec in got)
    old = []
    m.say("Noch ein Satz.", on_sentence=old.append)               # alte Ein-Argument-Rückrufe bleiben gültig
    assert old == ["Noch ein Satz."]


def test_gaps_are_filled_with_silence_not_underflow(monkeypatch):
    m = mouth(monkeypatch)
    m._play(sine(0.1), 24000, None)
    time.sleep(0.15)                                        # nächster Satz noch nicht fertig
    s = FakeStream.opened[0]
    n = len(s.writes)
    time.sleep(0.05)
    assert len(s.writes) > n and all(not w.any() for w in s.writes[n:])   # Stream läuft weiter, gibt Stille aus
    m._close_out()


def test_trim_tail_keeps_soft_word_endings():
    sr = 24000
    word = np.concatenate([sine(0.5), 0.04 * sine(0.15), np.zeros(int(0.5 * sr), "float32")])  # leises „-er“, dann Stille
    out = M.trim_tail(word.astype("float32"), sr)
    assert out.size >= int(0.65 * sr)                       # leises Wortende bleibt
    assert out.size < word.size                             # reine Stille danach fällt weg


def test_abort_closes_without_hanging(monkeypatch):
    m = mouth(monkeypatch)
    m._abort = True
    m._close_out()                                                # kein Stream offen → kein Fehler
    m._play(sine(0.2), 24000, None); m._abort = True
    m._close_out()
    assert FakeStream.opened[0].closed


def test_stream_pipelines_across_messages(monkeypatch):
    """Satz 2 wird schon berechnet, während Satz 1 läuft — keine Pause zwischen gestreamten Sätzen."""
    import queue as Q
    m = mouth(monkeypatch)
    starts = []

    def slow_synth(text):
        starts.append((text, time.monotonic()))
        time.sleep(0.3)                                       # Berechnung je Satz
        return sine(0.6), 24000                               # 0,6 s Audio
    m._synth_qwen = slow_synth
    q = Q.Queue()
    for t in ("Erster Satz der Geschichte.", "Zweiter Satz kommt gleich.", "Dritter Satz zum Schluss."):
        q.put(t)

    def texts():
        while True:
            try:
                yield q.get(timeout=0.2)
            except Q.Empty:
                return
    t0 = time.monotonic()
    m.say_stream(texts())
    total = time.monotonic() - t0
    assert [t for t, _ in starts] == ["Ja.", "Erster Satz der Geschichte.", "Zweiter Satz kommt gleich.",
                                      "Dritter Satz zum Schluss."]
    assert len(FakeStream.opened) == 1                        # ein Ausgabestream für den ganzen Fluss
    assert total < 0.3 * 4 + 0.6 * 3 - 0.3                    # überlappt: schneller als nacheinander


def test_stream_accepts_late_messages(monkeypatch):
    m = mouth(monkeypatch)
    got = []
    m._synth_qwen = lambda t: (got.append(t) or (sine(0.2), 24000))

    def texts():
        yield "Erster Satz hier."
        time.sleep(0.4)                                       # nächster Satz kommt später (Modell schreibt noch)
        yield "Später Satz dort."
    m.say_stream(texts())
    assert got[-2:] == ["Erster Satz hier.", "Später Satz dort."]


def test_strip_blips_removes_isolated_fragment_but_keeps_short_words():
    sr = 24000
    z = lambda s: np.zeros(int(s * sr), "float32")
    blip, word, sir = sine(0.08, f=300), sine(0.9), sine(0.3)
    with_blip = np.concatenate([blip, z(0.3), word, z(0.1)])          # „n“-Fetzen, Pause, Satz
    out = M.strip_blips(with_blip, sr)
    assert out.size < with_blip.size - int(0.3 * sr)                 # Fetzen samt Pause weg
    with_sir = np.concatenate([z(0.1), word, z(0.25), sir, z(0.1)])   # „…, Sir.“ am Ende bleibt
    assert M.strip_blips(with_sir, sr).size == with_sir.size


def test_prepared_reference_has_silence_padding(tmp_path):
    import soundfile as sf
    from voice_daemon.prepare_voice import PAD_S, prepare
    src = tmp_path / "in.wav"; dst = tmp_path / "out.wav"
    sf.write(str(src), sine(3.0, sr=48000), 48000)                   # endet mitten im Ton
    prepare(src, dst)
    d, sr = sf.read(str(dst), dtype="float32")
    pad = int(PAD_S * sr)
    assert np.abs(d[:pad]).max() == 0 and np.abs(d[-pad:]).max() == 0  # Stille vorn und hinten


def test_stream_splits_only_the_very_first_sentence(monkeypatch):
    m = mouth(monkeypatch)
    parts = []
    m._synth_qwen = lambda t: (parts.append(t) or (sine(0.2), 24000))
    long1 = "Es war einmal ein Leuchtturmwärter namens Elias, der auf einer kleinen Insel mitten im Meer lebte."
    long2 = "Jeden Abend entzündete er das Licht, das den Schiffen auf ihrem gefährlichen Weg durch die Klippen half."
    m.say_stream(iter([long1, long2]))
    assert parts[-1] == long2                                    # zweiter langer Satz bleibt ganz
    assert parts[1] == "Es war einmal ein Leuchtturmwärter namens Elias,"   # nur der erste für schnellen Start


def test_trim_lead_shortens_leading_silence():
    sr = 24000
    d = np.concatenate([np.zeros(int(0.2 * sr), "float32"), sine(0.5)])
    out = M.trim_lead(d, sr)
    assert abs((d.size - out.size) / sr - 0.16) < 0.02            # 0,2 s → 0,04 s Vorlauf


def test_output_uses_device_rate_and_fixed_blocks(monkeypatch):
    m = mouth(monkeypatch, rate=48000)
    m._play(sine(0.5, 24000), 24000, None)
    st = FakeStream.opened[0]
    assert st.kw["samplerate"] == 48000 and st.kw["blocksize"] == 1024   # native Rate, feste Blöcke
    m._close_out()
    played = np.concatenate(st.writes)
    assert abs(int(np.count_nonzero(np.abs(played) > 1e-6)) - 48000 // 2) < 1500   # 0,5 s in 48 kHz
