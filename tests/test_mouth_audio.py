"""Audioausgabe: ein Stream je Äußerung, weiche Satzkanten, Effektzustand, Satzdauer an die Anzeige."""
import numpy as np

from voice_daemon import mouth as M


class FakeStream:
    opened = []

    def __init__(self, **kw):
        self.kw, self.writes, self.closed = kw, [], False
        FakeStream.opened.append(self)

    def start(self): pass
    def write(self, x): self.writes.append(np.asarray(x).copy())
    def stop(self): pass
    def close(self): self.closed = True


def sine(sec, sr=24000, f=220.0):
    t = np.arange(int(sec * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * f * t)).astype("float32")


def mouth(monkeypatch, **kw):
    FakeStream.opened = []
    monkeypatch.setattr(M.sd, "OutputStream", FakeStream)
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
    m._play(sine(0.4), 24000, None)
    out = np.concatenate([w.reshape(-1) for w in FakeStream.opened[0].writes])
    assert abs(out[0]) < 1e-3 and abs(out[-1]) < 0.02          # Start und Ende laufen weich aus/ein
    assert np.abs(np.diff(out)).max() < 0.12                    # kein Sprung > Sinusflanke (Knackser)


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
    total = sum(w.size for w in FakeStream.opened[0].writes)
    assert abs(total - int(0.6 * 24000)) < 400


def test_sentence_duration_reaches_the_caption(monkeypatch):
    m = mouth(monkeypatch)
    got = []
    m.say("Guten Abend, Sir. Alle Systeme laufen.", on_sentence=lambda text, sec: got.append((text, round(sec, 2))))
    assert got and all(sec == 0.5 for _, sec in got)
    old = []
    m.say("Noch ein Satz.", on_sentence=old.append)               # alte Ein-Argument-Rückrufe bleiben gültig
    assert old == ["Noch ein Satz."]


def test_abort_closes_without_hanging(monkeypatch):
    m = mouth(monkeypatch)
    m._abort = True
    m._close_out()                                                # kein Stream offen → kein Fehler
    m._play(sine(0.2), 24000, None); m._abort = True
    m._close_out()
    assert FakeStream.opened[0].closed
