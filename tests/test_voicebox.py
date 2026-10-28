"""Voicebox-Adapter gegen einen lokalen Server, der die dokumentierte REST-Schnittstelle nachbildet
(POST /generate/stream → audio/wav, GET /profiles). Kein echter Voicebox-Server nötig."""
import http.server
import io
import json
import threading

import numpy as np
import pytest
import soundfile as sf

from voice_daemon.mouth import Mouth


class FakeVoicebox:
    def __init__(self, profiles=None, fail=False):
        self.requests = []
        self.profiles = profiles if profiles is not None else [{"id": "p1", "name": "Anna"}, {"id": "p2", "name": "Jarvis"}]
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(("GET", self.path, None))
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps(outer.profiles).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(("POST", self.path, body))
                if fail:
                    self.send_response(500); self.end_headers(); self.wfile.write(b"boom"); return
                buf = io.BytesIO()
                sf.write(buf, (0.3 * np.sin(np.linspace(0, 440, 12000))).astype("float32"), 24000, format="WAV")
                self.send_response(200); self.send_header("Content-Type", "audio/wav"); self.end_headers()
                self.wfile.write(buf.getvalue())

            def log_message(self, *a): pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def stop(self): self.srv.shutdown()


@pytest.fixture
def played(monkeypatch):
    out = []
    monkeypatch.setattr(Mouth, "_play", lambda self, data, sr, amp: out.append((len(data), sr)))
    return out


def mouth(url, profile="Jarvis"):
    m = Mouth(engine="voicebox", language="de")
    m.voicebox_url, m.voicebox_profile = url, profile
    return m


def test_profile_resolved_by_name_and_request_shape(played):
    vb = FakeVoicebox()
    m = mouth(vb.url, "jarvis")                      # Groß-/Kleinschreibung egal
    sentences = []
    m.say("Guten Abend, Sir. Alle Systeme laufen.", on_sentence=sentences.append)
    posts = [b for k, p, b in vb.requests if k == "POST"]
    assert posts and all(p == "/generate/stream" for k, p, _ in vb.requests if k == "POST")
    assert posts[0]["profile_id"] == "p2" and posts[0]["language"] == "de" and posts[0]["text"]
    assert played and played[0][1] == 24000 and sentences
    vb.stop()


def test_streams_sentence_by_sentence(played):
    vb = FakeVoicebox()
    m = mouth(vb.url)
    m.say("Erster Satz. Zweiter Satz. Dritter Satz mit etwas mehr Text, damit er einzeln bleibt.")
    assert len([1 for k, *_ in vb.requests if k == "POST"]) >= 2 and len(played) >= 2
    vb.stop()


def test_unknown_profile_falls_back(monkeypatch, played):
    vb = FakeVoicebox()
    m = mouth(vb.url, "Gibt-es-nicht")
    used = []
    monkeypatch.setattr(Mouth, "_synth_with_fallback", lambda self, clean: (used.append(clean) or (np.zeros(100, "float32"), 22050)))
    m.say("Hallo Sir.")
    assert used == ["Hallo Sir."] and played == [(100, 22050)]     # Ausweichpfad, kein Absturz
    vb.stop()


def test_server_down_falls_back_to_edge_then_system(monkeypatch, played):
    m = mouth("http://127.0.0.1:9")                                # nichts hört dort zu
    calls = []
    async def bad_edge(self, text): calls.append("edge"); raise OSError("offline")
    monkeypatch.setattr(Mouth, "_synth", bad_edge)
    monkeypatch.setattr(Mouth, "_synth_system", lambda self, t: (calls.append("system") or (np.ones(50, "float32"), 22050)))
    m.say("Hallo Sir.")
    assert calls == ["edge", "system"] and played == [(50, 22050)]


def test_server_error_500_falls_back(monkeypatch, played):
    vb = FakeVoicebox(fail=True)
    m = mouth(vb.url)
    monkeypatch.setattr(Mouth, "_synth_with_fallback", lambda self, c: (np.zeros(10, "float32"), 16000))
    m.say("Hallo Sir.")
    assert played == [(10, 16000)]
    vb.stop()


def test_no_profile_configured_uses_first(played):
    vb = FakeVoicebox()
    m = mouth(vb.url, None)
    m.say("Hallo Sir.")
    assert [b for k, p, b in vb.requests if k == "POST"][0]["profile_id"] == "p1"
    vb.stop()


def test_empty_profile_list_falls_back(monkeypatch, played):
    vb = FakeVoicebox(profiles=[])
    m = mouth(vb.url)
    monkeypatch.setattr(Mouth, "_synth_with_fallback", lambda self, c: (np.zeros(10, "float32"), 16000))
    m.say("Hallo Sir.")
    assert played == [(10, 16000)]
    vb.stop()


def test_profile_effect_chain_is_sent_one_to_one(played):
    chain = [{"type": "chorus", "enabled": True, "params": {"rate_hz": 0.2, "depth": 1, "feedback": 0.35, "centre_delay_ms": 7, "mix": 0.5}}]
    vb = FakeVoicebox(profiles=[{"id": "p2", "name": "Jarvis", "effects_chain": chain}])
    m = mouth(vb.url)
    m.say("Hallo Sir.")
    assert [b for k, p, b in vb.requests if k == "POST"][0]["effects_chain"] == chain
    m2 = mouth(vb.url); m2.voicebox_effects = "none"; m2.say("Hallo Sir.")
    assert [b for k, p, b in vb.requests if k == "POST"][-1]["effects_chain"] == []
    vb.stop()
